import asyncio
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from core.errors import AppError, ErrorCode
from core.logging import cause_chain
from domain.model_runtime import (
    Capability,
    ModelMessage,
    ProviderErrorKind,
    ProviderName,
)
from services.provider_errors import ProviderInvocationError
from services.providers.codex_app_server import (
    AsyncioAppServerProcessFactory,
    CodexAppServerClient,
)
from services.structured_output import invoke_structured


class _Answer(BaseModel):
    value: str


class _OptionalDetails(BaseModel):
    note: str | None = None


class _StrictAnswer(BaseModel):
    details: _OptionalDetails
    labels: list[str] = Field(default_factory=list)


class _ClientStructuredModel:
    def __init__(self, client: CodexAppServerClient) -> None:
        self.client = client
        self.calls = 0

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> _Answer:
        self.calls += 1
        assert isinstance(messages, list)
        result = await self.client.invoke(
            messages,
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )
        return result.output


def _model(model: str, *, modalities: list[str] | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "id": f"catalog-{model}",
        "model": model,
        "displayName": model,
        "hidden": False,
        "isDefault": False,
        "description": "test model",
        "defaultReasoningEffort": "low",
        "supportedReasoningEfforts": [],
    }
    if modalities is not None:
        record["inputModalities"] = modalities
    return record


class _FakeReader:
    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.lines.get()

    async def send(self, payload: dict[str, object]) -> None:
        await self.lines.put(json.dumps(payload).encode() + b"\n")


class _FakeWriter:
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
        reader: _FakeReader,
    ) -> None:
        self._respond = respond
        self._reader = reader
        self.messages: list[dict[str, object]] = []

    def write(self, data: bytes) -> None:
        payload = json.loads(data)
        self.messages.append(payload)
        response = self._respond(payload)
        responses = response if isinstance(response, list) else [response]
        for item in responses:
            if item is not None:
                if item == b"":
                    self._reader.lines.put_nowait(b"")
                    continue
                encoded = item if isinstance(item, bytes) else json.dumps(item).encode()
                self._reader.lines.put_nowait(
                    encoded + (b"" if encoded.endswith(b"\n") else b"\n")
                )

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _SelectiveFailWriter(_FakeWriter):
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
        reader: _FakeReader,
    ) -> None:
        super().__init__(respond, reader)
        self.fail_method: str | None = None
        self.fail_phase: str | None = None

    def write(self, data: bytes) -> None:
        payload = json.loads(data)
        if self.fail_phase == "write" and payload.get("method") == self.fail_method:
            raise OSError("private-write-canary")
        super().write(data)

    async def drain(self) -> None:
        if (
            self.fail_phase == "drain"
            and self.messages
            and self.messages[-1].get("method") == self.fail_method
        ):
            raise OSError("private-drain-canary")


class _FakeProcess:
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
    ) -> None:
        self.stdout = _FakeReader()
        self.stderr = _FakeReader()
        self.stdin = _FakeWriter(respond, self.stdout)
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0
        self.stdout.lines.put_nowait(b"")
        self.stderr.lines.put_nowait(b"")

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.stdout.lines.put_nowait(b"")
        self.stderr.lines.put_nowait(b"")

    async def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode or 0


class _StreamWriter:
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
        reader: asyncio.StreamReader,
    ) -> None:
        self._respond = respond
        self._reader = reader

    def write(self, data: bytes) -> None:
        payload = json.loads(data)
        response = self._respond(payload)
        responses = response if isinstance(response, list) else [response]
        for item in responses:
            if item is not None:
                encoded = item if isinstance(item, bytes) else json.dumps(item).encode()
                self._reader.feed_data(
                    encoded + (b"" if encoded.endswith(b"\n") else b"\n")
                )

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _StreamProcess(_FakeProcess):
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
        *,
        limit: int,
    ) -> None:
        super().__init__(respond)
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stdin = _StreamWriter(respond, self.stdout)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0
        self.stdout.feed_eof()
        self.stderr.lines.put_nowait(b"")


class _StubbornProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__(lambda _: None)
        self._reaped = asyncio.Event()

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.stdout.lines.put_nowait(b"")
        self.stderr.lines.put_nowait(b"")
        self._reaped.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self._reaped.wait()
        return -9


class _DelayedStartWriter(_FakeWriter):
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
        reader: _FakeReader,
        delayed_method: str,
    ) -> None:
        super().__init__(respond, reader)
        self.delayed_method = delayed_method
        self.started = asyncio.Event()
        self._held: list[object] = []

    def write(self, data: bytes) -> None:
        payload = json.loads(data)
        if payload.get("method") != self.delayed_method:
            super().write(data)
            return
        self.messages.append(payload)
        response = self._respond(payload)
        self._held = response if isinstance(response, list) else [response]
        self.started.set()

    def release(self) -> None:
        for item in self._held:
            if item is not None:
                encoded = item if isinstance(item, bytes) else json.dumps(item).encode()
                self._reader.lines.put_nowait(
                    encoded + (b"" if encoded.endswith(b"\n") else b"\n")
                )
        self._held = []


class _MethodDelayWriter(_FakeWriter):
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
        reader: _FakeReader,
        delays: dict[str, float],
    ) -> None:
        super().__init__(respond, reader)
        self._delays = delays

    async def drain(self) -> None:
        method = self.messages[-1].get("method") if self.messages else None
        await asyncio.sleep(self._delays.get(str(method), 0.0))


class _SlowReapProcess(_FakeProcess):
    def __init__(
        self,
        respond: Callable[[dict[str, object]], object | None],
    ) -> None:
        super().__init__(respond)
        self.terminate_started = asyncio.Event()
        self.reaped = asyncio.Event()
        self._killed = asyncio.Event()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.terminate_started.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.stdout.lines.put_nowait(b"")
        self.stderr.lines.put_nowait(b"")
        self._killed.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self._killed.wait()
        self.reaped.set()
        return -9


class _FakeFactory:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.commands: list[tuple[str, ...]] = []

    async def spawn(self, command: tuple[str, ...]) -> _FakeProcess:
        self.commands.append(command)
        return self.process


class _SequenceFactory:
    def __init__(self, processes: list[_FakeProcess]) -> None:
        self.processes = processes
        self.commands: list[tuple[str, ...]] = []

    async def spawn(self, command: tuple[str, ...]) -> _FakeProcess:
        self.commands.append(command)
        return self.processes[len(self.commands) - 1]


class _FailingFactory:
    def __init__(self) -> None:
        self.calls = 0

    async def spawn(self, command: tuple[str, ...]):
        self.calls += 1
        raise OSError("private-spawn-canary")


@pytest.mark.asyncio
async def test_asyncio_factory_spawns_exact_argv_with_bounded_reader(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_path: Path,
) -> None:
    process = _FakeProcess(lambda _: None)
    captured: dict[str, object] = {}

    async def create_subprocess_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    spawned = await AsyncioAppServerProcessFactory(
        environment={
            "HOME": "/safe/home",
            "CODEX_HOME": "/safe/codex",
            "PATH": "/safe/bin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "C.UTF-8",
            "SYSTEMROOT": "C:\\Windows",
            "OPENROUTER_API_KEY": "private-openrouter-canary",
            "BEAST_API_KEY": "private-beast-canary",
            "LANGSMITH_API_KEY": "private-langsmith-canary",
            "OPENAI_API_KEY": "private-openai-canary",
        },
        temp_root=project_tmp_path / "codex-runtime" / "tmp",
    ).spawn(("codex", "app-server"))

    assert spawned is process
    assert captured == {
        "argv": ("codex", "app-server"),
        "kwargs": {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "limit": 1_048_577,
            "env": {
                "HOME": "/safe/home",
                "CODEX_HOME": "/safe/codex",
                "PATH": "/safe/bin",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "C.UTF-8",
                "SYSTEMROOT": "C:\\Windows",
                "TMPDIR": str(project_tmp_path / "codex-runtime" / "tmp"),
            },
        },
    }


def _protocol_responder(
    event: object,
) -> Callable[[dict[str, object]], object | None]:
    def respond(
        message: dict[str, object],
    ) -> dict[str, object] | list[dict[str, object]] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return {
                "id": request_id,
                "result": {"thread": {"id": "thread-1"}, "model": "codex-exact"},
            }
        if method == "turn/start":
            events = event if isinstance(event, list) else [event]
            return [
                {"id": request_id, "result": {"turn": {"id": "turn-1"}}},
                *events,
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    return respond


async def _client_for_protocol_event(
    project_tmp_path: Path,
    event: object,
) -> tuple[CodexAppServerClient, _FakeProcess]:
    process = _FakeProcess(_protocol_responder(event))
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
    )
    await client.inspect()
    return client, process


@pytest.mark.asyncio
async def test_handshake_pages_catalog_once_and_gates_unproven_tool_safety(
    project_tmp_path: Path,
) -> None:
    def respond(message: dict[str, object]) -> dict[str, object] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {
                        "type": "chatgpt",
                        "email": None,
                        "planType": "free",
                    },
                },
            }
        if method == "model/list":
            cursor = message["params"]["cursor"]
            if cursor is None:
                return {
                    "id": request_id,
                    "result": {
                        "data": [_model("codex-exact", modalities=["text", "image"])],
                        "nextCursor": "page-2",
                    },
                }
            return {
                "id": request_id,
                "result": {"data": [_model("other-model")], "nextCursor": None},
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": True,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        raise AssertionError(f"unexpected method: {method}")

    process = _FakeProcess(respond)
    factory = _FakeFactory(process)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
    )

    first = await client.inspect()
    second = await client.inspect()

    assert factory.commands == [("codex", "app-server")]
    assert process.stdin.messages == [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "cook-mantra-api", "version": "0.1.0"},
                "capabilities": {
                    "optOutNotificationMethods": ["thread/status/changed"]
                },
            },
        },
        {"method": "initialized"},
        {"id": 2, "method": "account/read", "params": {}},
        {
            "id": 3,
            "method": "model/list",
            "params": {"cursor": None, "includeHidden": False},
        },
        {
            "id": 4,
            "method": "model/list",
            "params": {"cursor": "page-2", "includeHidden": False},
        },
        {
            "id": 5,
            "method": "modelProvider/capabilities/read",
            "params": {},
        },
    ]
    assert first == second
    assert first is not second
    assert first.provider is ProviderName.CODEX
    discovered = first.models["codex-exact"]
    assert discovered.available is False
    assert discovered.error is ProviderErrorKind.CAPABILITY_MISSING
    assert discovered.capabilities == (
        Capability.STRUCTURED_OUTPUT,
        Capability.TEXT,
        Capability.VISION,
    )

    await client.aclose()


def _discovery_responder(
    *,
    modalities: list[str],
    account_response_prefix: object | None = None,
    requires_openai_auth: bool = False,
) -> Callable[[dict[str, object]], object | None]:
    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            result = {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": requires_openai_auth,
                    "account": {"type": "apiKey"},
                },
            }
            if account_response_prefix is None:
                return result
            return [account_response_prefix, result]
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=modalities)],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        raise AssertionError(f"unexpected method: {method}")

    return respond


@pytest.mark.asyncio
async def test_explicit_tool_boundary_opt_in_exposes_text_and_vision(
    project_tmp_path: Path,
) -> None:
    process = _FakeProcess(
        _discovery_responder(modalities=["text", "image", "audio"])
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        allow_unverified_tool_boundary=True,
    )

    discovered = (await client.inspect()).models["codex-exact"]

    assert discovered.available is True
    assert discovered.error is None
    assert discovered.capabilities == (
        Capability.STRUCTURED_OUTPUT,
        Capability.TEXT,
        Capability.VISION,
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_required_openai_auth_is_ready_when_valid_account_is_present(
    project_tmp_path: Path,
) -> None:
    process = _FakeProcess(
        _discovery_responder(
            modalities=["text", "image"],
            requires_openai_auth=True,
        )
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        allow_unverified_tool_boundary=True,
    )

    discovered = (await client.inspect()).models["codex-exact"]

    assert discovered.available is True
    assert discovered.error is None
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "notification_method",
    ["config/warning", "remoteControl/status/changed"],
)
async def test_documented_non_content_notification_is_ignored_during_discovery(
    project_tmp_path: Path,
    notification_method: str,
) -> None:
    process = _FakeProcess(
        _discovery_responder(
            modalities=["text"],
            account_response_prefix={
                "method": notification_method,
                "params": {"summary": "safe redacted warning"},
            },
        )
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        allow_unverified_tool_boundary=True,
    )

    discovered = (await client.inspect()).models["codex-exact"]

    assert discovered.available is True
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unexpected_frame",
    [
        {"id": 99, "method": "config/warning", "params": {}},
        {"method": "item/completed", "params": {}},
        {"method": "config/warning", "params": "invalid"},
    ],
)
async def test_discovery_rejects_requests_content_events_and_malformed_notifications(
    project_tmp_path: Path,
    unexpected_frame: dict[str, object],
) -> None:
    process = _FakeProcess(
        _discovery_responder(
            modalities=["text"],
            account_response_prefix=unexpected_frame,
        )
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        allow_unverified_tool_boundary=True,
    )

    result = await client.inspect()

    failed_model = result.models["codex-exact"]
    assert failed_model.error is ProviderErrorKind.PROTOCOL_ERROR
    assert failed_model.available is False
    await client.aclose()


def _completed_notifications(
    text: str,
    *,
    status: str = "completed",
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
) -> list[dict[str, object]]:
    return [
        {
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "completedAtMs": 1,
                "item": {
                    "id": "message-1",
                    "type": "agentMessage",
                    "text": text,
                },
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "items": [], "status": status},
            },
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["not-json", '{"wrong":"shape"}'])
async def test_invalid_final_json_or_schema_fails_without_replay_and_deletes_thread(
    project_tmp_path: Path,
    text: str,
) -> None:
    client, process = await _client_for_protocol_event(
        project_tmp_path,
        _completed_notifications(text),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.INVALID_OUTPUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_current_safe_lifecycle_notifications_are_accepted(
    project_tmp_path: Path,
) -> None:
    events = [
        {
            "method": "mcpServer/startupStatus/updated",
            "params": {
                "threadId": None,
                "name": "configured-server",
                "status": "ready",
                "error": None,
                "failureReason": None,
            },
        },
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "startedAtMs": 1,
                "item": {
                    "id": "user-message-1",
                    "type": "userMessage",
                    "content": [],
                },
            },
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "completedAtMs": 2,
                "item": {
                    "id": "user-message-1",
                    "type": "userMessage",
                    "content": [],
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "startedAtMs": 3,
                "item": {
                    "id": "message-1",
                    "type": "agentMessage",
                    "text": "",
                },
            },
        },
        *_completed_notifications('{"value":"safe"}')[:-1],
        {
            "method": "account/rateLimits/updated",
            "params": {"rateLimits": {}},
        },
        _completed_notifications('{"value":"safe"}')[-1],
    ]
    client, _ = await _client_for_protocol_event(project_tmp_path, events)

    result = await client.invoke(
        [ModelMessage(role="user", content="private-prompt-canary")],
        _Answer,
        model="codex-exact",
        timeout_seconds=1,
    )

    assert result.output == _Answer(value="safe")
    await client.aclose()


@pytest.mark.asyncio
async def test_codex_output_schema_requires_and_closes_every_object(
    project_tmp_path: Path,
) -> None:
    client, process = await _client_for_protocol_event(
        project_tmp_path,
        _completed_notifications('{"details":{"note":null},"labels":[]}'),
    )

    await client.invoke(
        [ModelMessage(role="user", content="private-prompt-canary")],
        _StrictAnswer,
        model="codex-exact",
        timeout_seconds=1,
    )

    turn_request = next(
        item for item in process.stdin.messages if item.get("method") == "turn/start"
    )
    output_schema = turn_request["params"]["outputSchema"]
    assert isinstance(output_schema, dict)

    def assert_strict(node: object) -> None:
        if isinstance(node, dict):
            assert "default" not in node
            properties = node.get("properties")
            if isinstance(properties, dict):
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(properties)
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    assert_strict(output_schema)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "message-1",
                    "delta": "not authoritative",
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "items": [], "status": "completed"},
                },
            },
        ],
        [
            *_completed_notifications('{"value":"one"}')[:1],
            *_completed_notifications('{"value":"two"}')[:1],
        ],
        _completed_notifications('{"value":"ok"}', status="failed"),
        _completed_notifications('{"value":"ok"}', status="interrupted"),
        _completed_notifications('{"value":"ok"}', status="inProgress"),
        _completed_notifications(
            '{"value":"ok"}',
            thread_id="wrong-thread",
        ),
        _completed_notifications(
            '{"value":"ok"}',
            turn_id="wrong-turn",
        ),
    ],
    ids=lambda events: str(events[-1]).split("private")[0][:60],
)
async def test_invalid_terminal_sequences_fail_safely_and_delete_thread(
    project_tmp_path: Path,
    events: list[dict[str, object]],
) -> None:
    client, process = await _client_for_protocol_event(project_tmp_path, events)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [
        b'{"malformed":"private-frame-canary"',
        b'"private-frame-canary"',
        b'["private-frame-canary"]',
        b'{"id":999,"result":{}}',
        b'{"id":6,"result":{}}',
        b'{"id":6.0,"result":{}}',
        b'{"id":6,"error":{"message":"private-frame-canary"}}',
        b'{"padding":"' + (b"x" * 1_048_576) + b'"}',
    ],
)
async def test_malformed_oversized_and_unsolicited_frames_fail_safely(
    project_tmp_path: Path,
    frame: bytes,
) -> None:
    client, process = await _client_for_protocol_event(project_tmp_path, frame)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert "canary" not in str(raised.value)
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_mixed_notification_and_response_shape_is_rejected(
    project_tmp_path: Path,
) -> None:
    events = _completed_notifications('{"value":"unsafe"}')
    events[0]["result"] = {}
    client, _ = await _client_for_protocol_event(project_tmp_path, events)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", [1.0, True], ids=["float", "boolean"])
async def test_response_id_must_be_an_exact_non_boolean_integer(
    project_tmp_path: Path,
    bad_id: object,
) -> None:
    def respond(message: dict[str, object]) -> object | None:
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": bad_id, "result": {}}
        if method == "account/read":
            return {
                "id": bad_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": bad_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": bad_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        raise AssertionError(f"unexpected method: {method}")

    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(_FakeProcess(respond)),
        cleanup_timeout_seconds=0.01,
    )

    result = await client.inspect()

    assert result.models["codex-exact"].error is ProviderErrorKind.PROTOCOL_ERROR
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "drain"])
async def test_pipe_write_failures_are_normalized_and_detached(
    project_tmp_path: Path,
    phase: str,
) -> None:
    processes: list[_FakeProcess] = []
    for _ in range(2):
        responder = _protocol_responder(_completed_notifications('{"value":"x"}'))
        process = _FakeProcess(responder)
        process.stdin = _SelectiveFailWriter(responder, process.stdout)
        processes.append(process)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_SequenceFactory(processes),
        cleanup_timeout_seconds=0.01,
    )
    await client.inspect()
    for process in processes:
        assert isinstance(process.stdin, _SelectiveFailWriter)
        process.stdin.fail_method = "thread/start"
        process.stdin.fail_phase = phase

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.UNAVAILABLE
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The selected model provider is unavailable."
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_direct_invocation_spawn_failure_is_normalized_and_detached(
    project_tmp_path: Path,
) -> None:
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FailingFactory(),
        cleanup_timeout_seconds=0.01,
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.UNAVAILABLE
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The selected model provider is unavailable."
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_composed_owner_operation_spends_one_discovery_and_turn_deadline(
    project_tmp_path: Path,
) -> None:
    base = _protocol_responder(_completed_notifications('{"value":"late"}'))

    def responder(message: dict[str, object]) -> object | None:
        if message.get("method") == "turn/interrupt":
            return {"id": message.get("id"), "result": {}}
        return base(message)

    process = _FakeProcess(responder)
    process.stdin = _MethodDelayWriter(
        responder,
        process.stdout,
        {"account/read": 0.02, "turn/start": 0.02},
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.02,
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await asyncio.wait_for(
            client.invoke_admitted(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=0.03,
                admit=lambda _: None,
            ),
            timeout=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model operation timed out."
    ]
    assert process.terminate_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_composed_owner_rejects_cached_failed_discovery_before_thread_start(
    project_tmp_path: Path,
) -> None:
    process = _FakeProcess(
        _protocol_responder(_completed_notifications('{"value":"unsafe"}'))
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
    )
    await client.inspect()

    def reject(_: object) -> None:
        raise LookupError("safe-admission-rejection")

    with pytest.raises(LookupError, match="safe-admission-rejection"):
        await client.invoke_admitted(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
            admit=reject,
        )

    assert all(
        message.get("method") not in {"thread/start", "turn/start"}
        for message in process.stdin.messages
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_external_cancellation_during_composed_discovery_reaps_before_return(
    project_tmp_path: Path,
) -> None:
    first_responder = _protocol_responder(
        _completed_notifications('{"value":"first-unused"}')
    )
    first = _SlowReapProcess(first_responder)
    first.stdin = _DelayedStartWriter(
        first_responder,
        first.stdout,
        "account/read",
    )
    second = _FakeProcess(
        _protocol_responder(_completed_notifications('{"value":"fresh"}'))
    )
    factory = _SequenceFactory([first, second])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.04,
    )

    try:
        invocation = asyncio.create_task(
            client.invoke_admitted(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=1,
                admit=lambda _: None,
            )
        )
        assert isinstance(first.stdin, _DelayedStartWriter)
        await asyncio.wait_for(first.stdin.started.wait(), timeout=0.2)

        invocation.cancel()
        await asyncio.sleep(0.005)
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(invocation, timeout=0.2)

        assert first.terminate_calls == 1
        assert first.kill_calls == 1
        assert first.reaped.is_set()
        assert first.wait_calls >= 2
        assert len(factory.commands) == 1

        result = await client.invoke_admitted(
            [ModelMessage(role="user", content="second")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
            admit=lambda _: None,
        )

        assert result.output == _Answer(value="fresh")
        assert len(factory.commands) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_external_cancellation_during_lazy_initialization_reaps_before_return(
    project_tmp_path: Path,
) -> None:
    first_responder = _protocol_responder(
        _completed_notifications('{"value":"first-unused"}')
    )
    first = _SlowReapProcess(first_responder)
    first.stdin = _DelayedStartWriter(
        first_responder,
        first.stdout,
        "initialize",
    )
    second = _FakeProcess(
        _protocol_responder(_completed_notifications('{"value":"fresh"}'))
    )
    factory = _SequenceFactory([first, second])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.04,
    )

    try:
        invocation = asyncio.create_task(
            client.invoke(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=1,
            )
        )
        assert isinstance(first.stdin, _DelayedStartWriter)
        await asyncio.wait_for(first.stdin.started.wait(), timeout=0.2)

        invocation.cancel()
        await asyncio.sleep(0.005)
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(invocation, timeout=0.2)

        assert first.terminate_calls == 1
        assert first.kill_calls == 1
        assert first.reaped.is_set()
        assert first.wait_calls >= 2
        assert len(factory.commands) == 1

        result = await client.invoke_admitted(
            [ModelMessage(role="user", content="second")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
            admit=lambda _: None,
        )

        assert result.output == _Answer(value="fresh")
        assert len(factory.commands) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_stream_reader_over_limit_is_normalized_and_detached(
    project_tmp_path: Path,
) -> None:
    responder = _protocol_responder(b"x" * 1_025)
    process = _StreamProcess(responder, limit=1_024)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        max_frame_bytes=1_024,
        cleanup_timeout_seconds=0.01,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model provider returned an invalid response."
    ]
    await client.aclose()


def _blocking_turn_responder(
    turn_started: asyncio.Event,
) -> Callable[[dict[str, object]], object | None]:
    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return {
                "id": request_id,
                "result": {"thread": {"id": "thread-1"}, "model": "codex-exact"},
            }
        if method == "turn/start":
            turn_started.set()
            return {"id": request_id, "result": {"turn": {"id": "turn-1"}}}
        if method == "turn/interrupt":
            return [
                {"id": request_id, "result": {}},
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {
                            "id": "turn-1",
                            "items": [],
                            "status": "interrupted",
                        },
                    },
                },
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    return respond


async def _blocking_client(
    project_tmp_path: Path,
) -> tuple[CodexAppServerClient, _FakeProcess, asyncio.Event]:
    turn_started = asyncio.Event()
    process = _FakeProcess(_blocking_turn_responder(turn_started))
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        drain_timeout_seconds=0.1,
    )
    await client.inspect()
    return client, process, turn_started


@pytest.mark.asyncio
async def test_deadline_interrupts_drains_and_deletes_exactly_once(
    project_tmp_path: Path,
) -> None:
    client, process, _ = await _blocking_client(project_tmp_path)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.01,
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert [
        message
        for message in process.stdin.messages
        if message.get("method") == "turn/interrupt"
    ] == [
        {
            "id": 7,
            "method": "turn/interrupt",
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        }
    ]
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_silent_readiness_is_bounded_and_kills_child_ignoring_terminate(
    project_tmp_path: Path,
) -> None:
    process = _StubbornProcess()
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        readiness_timeout_seconds=0.02,
        cleanup_timeout_seconds=0.01,
    )

    result = await asyncio.wait_for(client.inspect(), timeout=0.2)

    assert result.models["codex-exact"].error is ProviderErrorKind.TIMED_OUT
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.wait_calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_invocation_timeout_includes_waiting_for_serialized_predecessor(
    project_tmp_path: Path,
) -> None:
    client, process, turn_started = await _blocking_client(project_tmp_path)
    first = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="first")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(turn_started.wait(), timeout=1)

    with pytest.raises(ProviderInvocationError) as raised:
        await asyncio.wait_for(
            client.invoke(
                [ModelMessage(role="user", content="queued")],
                _Answer,
                model="codex-exact",
                timeout_seconds=0.02,
            ),
            timeout=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/start"
            ]
        )
        == 1
    )
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_method", ["thread/start", "turn/start"])
async def test_cancellation_settles_delayed_start_response_before_cleanup(
    project_tmp_path: Path,
    delayed_method: str,
) -> None:
    responder = _blocking_turn_responder(asyncio.Event())
    process = _FakeProcess(responder)
    process.stdin = _DelayedStartWriter(responder, process.stdout, delayed_method)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        drain_timeout_seconds=0.02,
        cleanup_timeout_seconds=0.05,
    )
    await client.inspect()
    task = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(process.stdin.started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    process.stdin.release()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)

    methods = [item.get("method") for item in process.stdin.messages]
    if delayed_method == "turn/start":
        assert methods.count("turn/interrupt") == 1
    assert methods.count("thread/delete") == 1
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_method", ["thread/start", "turn/start"])
async def test_repeated_cancellation_cannot_abandon_delayed_start_ownership(
    project_tmp_path: Path,
    delayed_method: str,
) -> None:
    turn_start_calls = 0

    def respond(message: dict[str, object]) -> object | None:
        nonlocal turn_start_calls
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return {
                "id": request_id,
                "result": {
                    "thread": {"id": "thread-1"},
                    "model": "codex-exact",
                },
            }
        if method == "turn/start":
            turn_start_calls += 1
            return {"id": request_id, "result": {"turn": {"id": "turn-1"}}}
        if method == "turn/interrupt":
            return [
                {"id": request_id, "result": {}},
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {
                            "id": "turn-1",
                            "items": [],
                            "status": "interrupted",
                        },
                    },
                },
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    process = _FakeProcess(respond)
    process.stdin = _DelayedStartWriter(
        respond,
        process.stdout,
        delayed_method,
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.05,
    )
    await client.inspect()
    task = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    assert isinstance(process.stdin, _DelayedStartWriter)
    await asyncio.wait_for(process.stdin.started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    process.stdin.release()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.3)

    methods = [message.get("method") for message in process.stdin.messages]
    if delayed_method == "turn/start":
        assert turn_start_calls == 1
        assert methods.count("turn/interrupt") == 1
    assert methods.count("thread/delete") == 1
    assert process.terminate_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_unsettled_turn_start_skips_cleanup_after_generation_reap(
    project_tmp_path: Path,
) -> None:
    responder = _protocol_responder(
        _completed_notifications('{"value":"never-released"}')
    )
    process = _SlowReapProcess(responder)
    process.stdin = _DelayedStartWriter(
        responder,
        process.stdout,
        "turn/start",
    )
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.04,
    )
    await client.inspect()
    invocation = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    assert isinstance(process.stdin, _DelayedStartWriter)
    await asyncio.wait_for(process.stdin.started.wait(), timeout=0.2)

    invocation.cancel()
    await asyncio.sleep(0)
    invocation.cancel()
    await asyncio.wait_for(process.terminate_started.wait(), timeout=0.2)
    invocation.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(invocation, timeout=0.2)

        methods = [message.get("method") for message in process.stdin.messages]
        assert methods.count("turn/interrupt") == 0
        assert methods.count("thread/delete") == 0
        assert process.kill_calls == 1
        assert process.reaped.is_set()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ignored_interrupt_and_delete_are_bounded_by_cleanup_grace(
    project_tmp_path: Path,
) -> None:
    turn_started = asyncio.Event()

    def respond(message: dict[str, object]) -> object | None:
        if message.get("method") in {"turn/interrupt", "thread/delete"}:
            return None
        return _blocking_turn_responder(turn_started)(message)

    process = _StubbornProcess()
    process.stdin = _FakeWriter(respond, process.stdout)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        drain_timeout_seconds=0.01,
        cleanup_timeout_seconds=0.02,
    )
    await client.inspect()
    task = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(turn_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_forbidden_notification_during_cleanup_poisons_generation(
    project_tmp_path: Path,
) -> None:
    turn_started = asyncio.Event()

    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        if message.get("method") == "turn/interrupt":
            return [
                {
                    "method": "item/fileChange/outputDelta",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "delta": "private-cleanup-canary",
                    },
                },
                {"id": request_id, "result": {}},
            ]
        return _blocking_turn_responder(turn_started)(message)

    process = _FakeProcess(respond)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.02,
    )
    await client.inspect()
    task = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(turn_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)

    assert process.terminate_calls == 1
    assert process.returncode == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_terminal_items_during_interrupt_poison_generation(
    project_tmp_path: Path,
) -> None:
    turn_started = asyncio.Event()
    base = _blocking_turn_responder(turn_started)
    duplicate = {
        "id": "message-late",
        "type": "agentMessage",
        "text": '{"value":"private-late-canary"}',
    }

    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        if message.get("method") == "turn/interrupt":
            return [
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {
                            "id": "turn-1",
                            "items": [duplicate, duplicate],
                            "status": "interrupted",
                        },
                    },
                },
                {"id": request_id, "result": {}},
            ]
        return base(message)

    process = _FakeProcess(respond)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.03,
    )
    await client.inspect()
    task = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(turn_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)

    assert process.terminate_calls == 1
    assert process.wait_calls >= 1
    await client.aclose()


@pytest.mark.asyncio
async def test_ignored_normal_delete_prevents_success_and_poisons_generation(
    project_tmp_path: Path,
) -> None:
    def respond(message: dict[str, object]) -> object | None:
        if message.get("method") == "thread/delete":
            return None
        return _protocol_responder(_completed_notifications('{"value":"unsafe"}'))(
            message
        )

    process = _FakeProcess(respond)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.02,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await asyncio.wait_for(
            client.invoke(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=1,
            ),
            timeout=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert process.terminate_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_terminal_during_thread_delete_prevents_success(
    project_tmp_path: Path,
) -> None:
    base = _protocol_responder(_completed_notifications('{"value":"unsafe"}'))

    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        if message.get("method") == "thread/delete":
            return [
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {
                            "id": "turn-1",
                            "items": [
                                {
                                    "id": "message-1",
                                    "type": "agentMessage",
                                    "text": '{"value":"unsafe"}',
                                }
                            ],
                            "status": "completed",
                        },
                    },
                },
                {"id": request_id, "result": {}},
            ]
        return base(message)

    process = _FakeProcess(respond)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.03,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert process.terminate_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_repeated_cancellation_shields_one_interrupt_and_one_delete(
    project_tmp_path: Path,
) -> None:
    client, process, turn_started = await _blocking_client(project_tmp_path)
    task = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(turn_started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "turn/interrupt"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


def _generation_responder(
    terminal: object,
) -> Callable[[dict[str, object]], object | None]:
    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return {
                "id": request_id,
                "result": {"thread": {"id": "thread-1"}, "model": "codex-exact"},
            }
        if method == "turn/start":
            events = terminal if isinstance(terminal, list) else [terminal]
            return [
                {"id": request_id, "result": {"turn": {"id": "turn-1"}}},
                *events,
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    return respond


@pytest.mark.asyncio
async def test_eof_before_usable_output_restarts_once_and_rediscovers(
    project_tmp_path: Path,
) -> None:
    first = _FakeProcess(_generation_responder(b""))
    second = _FakeProcess(
        _generation_responder(_completed_notifications('{"value":"replayed"}'))
    )
    factory = _SequenceFactory([first, second])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
    )
    await client.inspect()

    result = await client.invoke(
        [ModelMessage(role="user", content="private-prompt-canary")],
        _Answer,
        model="codex-exact",
        timeout_seconds=1,
    )

    assert result.output == _Answer(value="replayed")
    assert factory.commands == [
        ("codex", "app-server"),
        ("codex", "app-server"),
    ]
    assert (
        len(
            [
                message
                for message in second.stdin.messages
                if message.get("method") == "account/read"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                message
                for message in second.stdin.messages
                if message.get("method") == "model/list"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_external_cancellation_during_replay_rediscovery_reaps_replacement(
    project_tmp_path: Path,
) -> None:
    first = _FakeProcess(_generation_responder(b""))
    second_responder = _generation_responder(
        _completed_notifications('{"value":"second-unused"}')
    )
    second = _SlowReapProcess(second_responder)
    second.stdin = _DelayedStartWriter(
        second_responder,
        second.stdout,
        "account/read",
    )
    third = _FakeProcess(
        _generation_responder(_completed_notifications('{"value":"fresh"}'))
    )
    factory = _SequenceFactory([first, second, third])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.04,
    )
    await client.inspect()

    try:
        invocation = asyncio.create_task(
            client.invoke(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=1,
            )
        )
        assert isinstance(second.stdin, _DelayedStartWriter)
        await asyncio.wait_for(second.stdin.started.wait(), timeout=0.2)

        invocation.cancel()
        await asyncio.sleep(0.005)
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(invocation, timeout=0.2)

        assert first.terminate_calls == 1
        assert second.terminate_calls == 1
        assert second.kill_calls == 1
        assert second.reaped.is_set()
        assert second.wait_calls >= 2
        assert len(factory.commands) == 2

        result = await client.invoke_admitted(
            [ModelMessage(role="user", content="second")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
            admit=lambda _: None,
        )

        assert result.output == _Answer(value="fresh")
        assert len(factory.commands) == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_internal_replay_spends_the_original_invocation_deadline(
    project_tmp_path: Path,
) -> None:
    first = _FakeProcess(_generation_responder(b""))
    second = _StubbornProcess()
    factory = _SequenceFactory([first, second])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.01,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await asyncio.wait_for(
            client.invoke(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=0.03,
            ),
            timeout=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert factory.commands == [
        ("codex", "app-server"),
        ("codex", "app-server"),
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_deadline_during_replay_restart_still_reaps_failed_generation(
    project_tmp_path: Path,
) -> None:
    first = _SlowReapProcess(_generation_responder(b""))
    factory = _SequenceFactory([first])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.06,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await asyncio.wait_for(
            client.invoke(
                [ModelMessage(role="user", content="private-prompt-canary")],
                _Answer,
                model="codex-exact",
                timeout_seconds=0.01,
            ),
            timeout=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert first.terminate_calls == 1
    assert first.kill_calls == 1
    assert first.reaped.is_set()
    await client.aclose()


@pytest.mark.asyncio
async def test_shutdown_joins_in_progress_generation_reap(
    project_tmp_path: Path,
) -> None:
    first = _SlowReapProcess(_generation_responder(b""))
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_SequenceFactory([first]),
        cleanup_timeout_seconds=0.06,
    )
    await client.inspect()
    invocation = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(first.terminate_started.wait(), timeout=0.2)

    await asyncio.wait_for(client.aclose(), timeout=0.3)
    await asyncio.gather(invocation, return_exceptions=True)

    assert first.terminate_calls == 1
    assert first.kill_calls == 1
    assert first.reaped.is_set()
    assert first.wait_calls >= 2


@pytest.mark.asyncio
async def test_exhausted_internal_replay_is_not_retried_by_outer_boundary(
    project_tmp_path: Path,
) -> None:
    processes = [
        _FakeProcess(_generation_responder(b"")),
        _FakeProcess(_generation_responder(b"")),
    ]
    factory = _SequenceFactory(processes)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.01,
    )
    await client.inspect()
    model = _ClientStructuredModel(client)

    with pytest.raises(AppError) as raised:
        await invoke_structured(
            model,
            [ModelMessage(role="user", content="private-prompt-canary")],
            max_attempts=2,
        )

    assert raised.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert model.calls == 1
    assert len(factory.commands) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_exhausted_child_loss_reaps_before_error_and_next_call_is_fresh(
    project_tmp_path: Path,
) -> None:
    first = _FakeProcess(_generation_responder(b""))
    second = _FakeProcess(_generation_responder(b""))
    third = _FakeProcess(
        _generation_responder(_completed_notifications('{"value":"fresh"}'))
    )
    factory = _SequenceFactory([first, second, third])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.02,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="first")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.UNAVAILABLE
    assert raised.value.error.retryable is False
    assert second.terminate_calls == 1
    assert second.wait_calls >= 1
    assert len(factory.commands) == 2

    result = await client.invoke(
        [ModelMessage(role="user", content="second")],
        _Answer,
        model="codex-exact",
        timeout_seconds=0.2,
    )

    assert result.output == _Answer(value="fresh")
    assert len(factory.commands) == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_forbidden_generation_is_reaped_before_same_client_second_call(
    project_tmp_path: Path,
) -> None:
    forbidden = {
        "method": "item/commandExecution/started",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "command": "private-command-canary",
        },
    }
    first = _FakeProcess(_generation_responder(forbidden))
    second = _FakeProcess(
        _generation_responder(_completed_notifications('{"value":"safe"}'))
    )
    factory = _SequenceFactory([first, second])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
        cleanup_timeout_seconds=0.01,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="first")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )
    second_result = await client.invoke(
        [ModelMessage(role="user", content="second")],
        _Answer,
        model="codex-exact",
        timeout_seconds=0.2,
    )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert second_result.output == _Answer(value="safe")
    assert len(factory.commands) == 2
    assert first.terminate_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_eof_after_usable_output_never_replays(
    project_tmp_path: Path,
) -> None:
    usable_then_eof = [
        _completed_notifications('{"value":"ambiguous"}')[0],
        b"",
    ]
    first = _FakeProcess(_generation_responder(usable_then_eof))
    factory = _SequenceFactory([first])
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert factory.commands == [("codex", "app-server")]
    await client.aclose()


@pytest.mark.asyncio
async def test_eof_primary_is_preserved_when_delete_write_would_fail(
    project_tmp_path: Path,
) -> None:
    usable_then_eof = [
        _completed_notifications('{"value":"private-output-canary"}')[0],
        b"",
    ]
    responder = _generation_responder(usable_then_eof)
    process = _FakeProcess(responder)
    process.stdin = _SelectiveFailWriter(responder, process.stdout)
    process.stdin.fail_method = "thread/delete"
    process.stdin.fail_phase = "write"
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
        cleanup_timeout_seconds=0.02,
    )
    await client.inspect()

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=0.2,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model provider returned an invalid response."
    ]
    assert process.terminate_calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_full_invocations_are_serialized_until_first_thread_cleanup(
    project_tmp_path: Path,
) -> None:
    first_started = asyncio.Event()
    thread_count = 0

    def respond(message: dict[str, object]) -> object | None:
        nonlocal thread_count
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            thread_count += 1
            return {
                "id": request_id,
                "result": {
                    "thread": {"id": f"thread-{thread_count}"},
                    "model": "codex-exact",
                },
            }
        if method == "turn/start":
            if thread_count == 1:
                first_started.set()
                return {"id": request_id, "result": {"turn": {"id": "turn-1"}}}
            return [
                {"id": request_id, "result": {"turn": {"id": "turn-2"}}},
                *_completed_notifications(
                    '{"value":"second"}',
                    thread_id="thread-2",
                    turn_id="turn-2",
                ),
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    process = _FakeProcess(respond)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
    )
    await client.inspect()
    first = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="first")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="second")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )
    )
    await asyncio.sleep(0)
    assert thread_count == 1

    await process.stdout.send(_completed_notifications('{"value":"first"}')[0])
    await process.stdout.send(_completed_notifications('{"value":"first"}')[1])

    assert (await first).output == _Answer(value="first")
    assert (await second).output == _Answer(value="second")
    assert thread_count == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_aclose_cleans_active_call_once_and_rejects_later_work(
    project_tmp_path: Path,
) -> None:
    client, process, turn_started = await _blocking_client(project_tmp_path)
    invocation = asyncio.create_task(
        client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=10,
        )
    )
    await asyncio.wait_for(turn_started.wait(), timeout=1)

    await asyncio.wait_for(client.aclose(), timeout=1)
    await client.aclose()

    with pytest.raises(asyncio.CancelledError):
        await invocation
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "turn/interrupt"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    assert process.terminate_calls == 1
    assert process.wait_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        await client.invoke(
            [ModelMessage(role="user", content="later")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_usage_requires_complete_last_and_total_breakdowns(
    project_tmp_path: Path,
) -> None:
    events = [
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 1,
                        "cachedInputTokens": 0,
                        "outputTokens": 1,
                        "reasoningOutputTokens": 0,
                        "totalTokens": 2,
                    }
                },
            },
        },
        *_completed_notifications('{"value":"unsafe"}'),
    ]
    client, process = await _client_for_protocol_event(project_tmp_path, events)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_turn_completed_rejects_forbidden_embedded_items(
    project_tmp_path: Path,
) -> None:
    events = _completed_notifications('{"value":"unsafe"}')
    events[1]["params"]["turn"]["items"] = [
        {"id": "web-1", "type": "webSearch", "query": "private-item-canary"}
    ]
    client, process = await _client_for_protocol_event(project_tmp_path, events)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert "canary" not in str(raised.value)
    assert (
        len(
            [
                message
                for message in process.stdin.messages
                if message.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_items",
    [
        [
            {
                "id": "message-1",
                "type": "agentMessage",
                "text": '{"value":"safe"}',
            },
            {
                "id": "message-1",
                "type": "agentMessage",
                "text": '{"value":"safe"}',
            },
        ],
        [
            {
                "id": "message-1",
                "type": "agentMessage",
                "text": '{"value":"conflict"}',
            }
        ],
        [
            {
                "id": "message-2",
                "type": "agentMessage",
                "text": '{"value":"safe"}',
            }
        ],
    ],
)
async def test_terminal_items_must_bind_to_the_single_completed_assistant(
    project_tmp_path: Path,
    terminal_items: list[dict[str, object]],
) -> None:
    events = _completed_notifications('{"value":"safe"}')
    events[1]["params"]["turn"]["items"] = terminal_items
    client, _ = await _client_for_protocol_event(project_tmp_path, events)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    await client.aclose()


@pytest.mark.asyncio
async def test_terminal_items_accept_exactly_the_completed_assistant_identity(
    project_tmp_path: Path,
) -> None:
    events = _completed_notifications('{"value":"safe"}')
    events[1]["params"]["turn"]["items"] = [
        {
            "id": "message-1",
            "type": "agentMessage",
            "text": '{"value":"safe"}',
        }
    ]
    client, _ = await _client_for_protocol_event(project_tmp_path, events)

    result = await client.invoke(
        [ModelMessage(role="user", content="private-prompt-canary")],
        _Answer,
        model="codex-exact",
        timeout_seconds=1,
    )

    assert result.output == _Answer(value="safe")
    await client.aclose()


@pytest.mark.asyncio
async def test_lifecycle_notifications_may_precede_matching_responses(
    project_tmp_path: Path,
) -> None:
    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return [
                {
                    "method": "thread/started",
                    "params": {"thread": {"id": "thread-early"}},
                },
                {
                    "id": request_id,
                    "result": {
                        "thread": {"id": "thread-early"},
                        "model": "codex-exact",
                    },
                },
            ]
        if method == "turn/start":
            return [
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread-early",
                        "turn": {"id": "turn-early"},
                    },
                },
                {"id": request_id, "result": {"turn": {"id": "turn-early"}}},
                *_completed_notifications(
                    '{"value":"ordered"}',
                    thread_id="thread-early",
                    turn_id="turn-early",
                ),
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    process = _FakeProcess(respond)
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(process),
    )
    await client.inspect()

    result = await client.invoke(
        [ModelMessage(role="user", content="private-prompt-canary")],
        _Answer,
        model="codex-exact",
        timeout_seconds=1,
    )

    assert result.output == _Answer(value="ordered")
    await client.aclose()


@pytest.mark.asyncio
async def test_spawn_failure_is_bounded_and_returns_safe_unavailable_discovery(
    project_tmp_path: Path,
) -> None:
    factory = _FailingFactory()
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=factory,
    )

    result = await client.inspect()

    assert factory.calls == 2
    assert result.models["codex-exact"].available is False
    assert result.models["codex-exact"].error is ProviderErrorKind.UNAVAILABLE
    assert "canary" not in repr(result)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account",
    [
        {"requiresOpenaiAuth": False, "account": None},
        {"requiresOpenaiAuth": False},
    ],
)
async def test_missing_or_unauthenticated_account_is_not_ready(
    project_tmp_path: Path,
    account: dict[str, object],
) -> None:
    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {"id": request_id, "result": account}
        raise AssertionError(f"unexpected method: {method}")

    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(_FakeProcess(respond)),
    )

    result = await client.inspect()

    assert result.models["codex-exact"].error is ProviderErrorKind.AUTHENTICATION_FAILED
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account",
    [
        {"account": {}},
        {"requiresOpenaiAuth": "false", "account": {}},
        {"requiresOpenaiAuth": False, "account": {}},
        {
            "requiresOpenaiAuth": False,
            "account": {"type": "chatgpt"},
        },
        {
            "requiresOpenaiAuth": False,
            "account": {
                "type": "chatgpt",
                "email": None,
                "planType": "private-plan-canary",
            },
        },
        {
            "requiresOpenaiAuth": False,
            "account": {
                "type": "amazonBedrock",
                "usesCodexManagedCredentials": "false",
            },
        },
        {
            "requiresOpenaiAuth": False,
            "account": {"type": "private-account-canary"},
        },
        {"requiresOpenaiAuth": False, "account": []},
        {"requiresOpenaiAuth": False, "account": "private-account-canary"},
        {"requiresOpenaiAuth": False, "account": 42},
    ],
)
async def test_malformed_account_payload_is_protocol_failure(
    project_tmp_path: Path,
    account: dict[str, object],
) -> None:
    def respond(message: dict[str, object]) -> object | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {"id": request_id, "result": account}
        raise AssertionError(f"unexpected method: {method}")

    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(_FakeProcess(respond)),
    )

    result = await client.inspect()

    assert result.models["codex-exact"].error is ProviderErrorKind.PROTOCOL_ERROR
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account",
    [
        {"type": "apiKey"},
        {"type": "chatgpt", "email": None, "planType": "free"},
        {
            "type": "chatgpt",
            "email": "person@example.test",
            "planType": "enterprise",
        },
        {"type": "amazonBedrock"},
        {"type": "amazonBedrock", "usesCodexManagedCredentials": True},
    ],
    ids=[
        "api-key",
        "chatgpt-null-email",
        "chatgpt-email",
        "bedrock-default",
        "bedrock-managed",
    ],
)
async def test_pinned_typed_account_variants_are_schema_valid(
    project_tmp_path: Path,
    account: dict[str, object],
) -> None:
    base = _protocol_responder(_completed_notifications('{"value":"unused"}'))

    def respond(message: dict[str, object]) -> object | None:
        if message.get("method") == "account/read":
            return {
                "id": message.get("id"),
                "result": {"requiresOpenaiAuth": False, "account": account},
            }
        return base(message)

    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=project_tmp_path / "codex-runtime",
        process_factory=_FakeFactory(_FakeProcess(respond)),
    )

    result = await client.inspect()

    assert result.models["codex-exact"].error is ProviderErrorKind.CAPABILITY_MISSING
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/call",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
        "applyPatchApproval",
        "execCommandApproval",
        "account/chatgptAuthTokens/refresh",
        "attestation/generate",
        "currentTime/read",
        "unknown/server/request",
    ],
)
async def test_every_server_request_is_rejected_and_thread_is_deleted(
    project_tmp_path: Path,
    method: str,
) -> None:
    canary = "private-server-request-canary"
    client, process = await _client_for_protocol_event(
        project_tmp_path,
        {"id": 900, "method": method, "params": {"value": canary}},
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert canary not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "command/exec/outputDelta",
        "item/commandExecution/started",
        "item/fileChange/outputDelta",
        "item/mcpToolCall/progress",
        "model/rerouted",
        "model/verification",
        "process/exited",
        "fs/changed",
        "mcpServer/status/updated",
        "thread/environment/changed",
        "thread/settings/updated",
        "skills/changed",
        "realtime/started",
        "warning",
    ],
)
async def test_non_allowlisted_notifications_are_rejected(
    project_tmp_path: Path,
    method: str,
) -> None:
    client, process = await _client_for_protocol_event(
        project_tmp_path,
        {
            "method": method,
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "message": "private-notification-canary",
            },
        },
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert "canary" not in str(raised.value)
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item_type",
    [
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "webSearch",
        "imageGeneration",
        "imageView",
        "reasoning",
        "plan",
        "userMessage",
        "collabAgentToolCall",
        "hookPrompt",
    ],
)
async def test_non_agent_completed_items_are_rejected(
    project_tmp_path: Path,
    item_type: str,
) -> None:
    client, process = await _client_for_protocol_event(
        project_tmp_path,
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "completedAtMs": 1,
                "item": {
                    "id": "forbidden-item",
                    "type": item_type,
                    "text": "private-item-canary",
                },
            },
        },
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [ModelMessage(role="user", content="private-prompt-canary")],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert "canary" not in str(raised.value)
    assert (
        len(
            [
                item
                for item in process.stdin.messages
                if item.get("method") == "thread/delete"
            ]
        )
        == 1
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_vision_uses_single_owned_file_and_maps_optional_usage(
    project_tmp_path: Path,
) -> None:
    observed_image_paths: list[Path] = []

    def respond(
        message: dict[str, object],
    ) -> dict[str, object] | list[dict[str, object]] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {"type": "apiKey"},
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-vision", modalities=["text", "image"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return {
                "id": request_id,
                "result": {
                    "thread": {"id": "thread-vision"},
                    "model": "codex-vision",
                },
            }
        if method == "turn/start":
            image_input = message["params"]["input"][1]
            image_path = Path(image_input["path"])
            observed_image_paths.append(image_path)
            assert image_input == {
                "type": "localImage",
                "path": str(image_path),
                "detail": "low",
            }
            assert image_path.read_bytes() == b"private-image"
            assert stat.S_IMODE(image_path.stat().st_mode) == 0o600
            return [
                {"id": request_id, "result": {"turn": {"id": "turn-vision"}}},
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": "thread-vision",
                        "turnId": "turn-vision",
                        "tokenUsage": {
                            "last": {
                                "inputTokens": 8,
                                "cachedInputTokens": 2,
                                "outputTokens": 3,
                                "reasoningOutputTokens": 1,
                                "totalTokens": 11,
                            },
                            "total": {
                                "inputTokens": 8,
                                "cachedInputTokens": 2,
                                "outputTokens": 3,
                                "reasoningOutputTokens": 1,
                                "totalTokens": 11,
                            },
                        },
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-vision",
                        "turnId": "turn-vision",
                        "completedAtMs": 1,
                        "item": {
                            "id": "message-vision",
                            "type": "agentMessage",
                            "text": '{"value":"seen"}',
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-vision",
                        "turn": {
                            "id": "turn-vision",
                            "items": [],
                            "status": "completed",
                        },
                    },
                },
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    process = _FakeProcess(respond)
    runtime_root = project_tmp_path / "codex-runtime"
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-vision"},
        sandbox_root=runtime_root,
        process_factory=_FakeFactory(process),
    )

    await client.inspect()
    result = await client.invoke(
        [
            ModelMessage(
                role="user",
                content="protected vision prompt",
                image=b"private-image",
                media_type="image/png",
            )
        ],
        _Answer,
        model="codex-vision",
        timeout_seconds=1,
    )

    assert result.output == _Answer(value="seen")
    assert result.usage is not None
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 3
    assert len(observed_image_paths) == 1
    assert observed_image_paths[0].is_relative_to(runtime_root / "media")
    assert observed_image_paths[0].exists() is False
    assert list((runtime_root / "cwd").iterdir()) == []

    await client.aclose()


@pytest.mark.asyncio
async def test_partial_media_write_failure_removes_sensitive_residue(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_path: Path,
) -> None:
    client, _ = await _client_for_protocol_event(
        project_tmp_path,
        _completed_notifications('{"value":"unused"}'),
    )
    real_write = os.write
    write_calls = 0

    def failing_write(descriptor: int, data: bytes | memoryview) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            return real_write(descriptor, bytes(data[:1]))
        raise OSError("private-media-write-canary")

    monkeypatch.setattr(os, "write", failing_write)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [
                ModelMessage(
                    role="user",
                    content="private-prompt-canary",
                    image=b"private-image-canary",
                    media_type="image/png",
                )
            ],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model request could not be prepared safely."
    ]
    assert list((project_tmp_path / "codex-runtime" / "media").iterdir()) == []
    await client.aclose()


@pytest.mark.asyncio
async def test_media_open_failure_is_normalized_and_detached(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_path: Path,
) -> None:
    client, _ = await _client_for_protocol_event(
        project_tmp_path,
        _completed_notifications('{"value":"unused"}'),
    )
    real_open = os.open

    def failing_open(*args: object, **kwargs: object) -> int:
        target = args[0] if args else None
        if isinstance(target, Path) and target.parent.name == "media":
            raise OSError("private-media-open-canary")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [
                ModelMessage(
                    role="user",
                    content="private-prompt-canary",
                    image=b"private-image-canary",
                    media_type="image/png",
                )
            ],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model request could not be prepared safely."
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_final_media_unlink_prevents_success_and_shutdown_retries(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_path: Path,
) -> None:
    client, _ = await _client_for_protocol_event(
        project_tmp_path,
        _completed_notifications('{"value":"unsafe-success"}'),
    )
    real_unlink = Path.unlink
    failed_once = False

    def flaky_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        nonlocal failed_once
        if path.parent.name == "media" and not failed_once:
            failed_once = True
            raise OSError("private-media-unlink-canary")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    with pytest.raises(ProviderInvocationError) as raised:
        await client.invoke(
            [
                ModelMessage(
                    role="user",
                    content="private-prompt-canary",
                    image=b"private-image-canary",
                    media_type="image/png",
                )
            ],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: Sensitive model media cleanup failed."
    ]
    media_root = project_tmp_path / "codex-runtime" / "media"
    assert len(list(media_root.iterdir())) == 1

    await client.aclose()

    assert list(media_root.iterdir()) == []


@pytest.mark.asyncio
async def test_persistent_owned_media_unlink_failure_surfaces_at_shutdown_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_path: Path,
) -> None:
    client, _ = await _client_for_protocol_event(
        project_tmp_path,
        _completed_notifications('{"value":"unsafe-success"}'),
    )
    real_unlink = Path.unlink
    allow_cleanup = False

    def controlled_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path.parent.name == "media" and not allow_cleanup:
            raise OSError("private-persistent-unlink-canary")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", controlled_unlink)

    with pytest.raises(ProviderInvocationError):
        await client.invoke(
            [
                ModelMessage(
                    role="user",
                    content="private-prompt-canary",
                    image=b"private-image-canary",
                    media_type="image/png",
                )
            ],
            _Answer,
            model="codex-exact",
            timeout_seconds=1,
        )

    media_root = project_tmp_path / "codex-runtime" / "media"
    assert len(list(media_root.iterdir())) == 1
    with pytest.raises(ProviderInvocationError) as raised:
        await client.aclose()

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.retryable is False
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: Sensitive model media cleanup failed."
    ]
    assert "canary" not in repr(raised.value)
    assert len(list(media_root.iterdir())) == 1

    allow_cleanup = True
    await client.aclose()

    assert list(media_root.iterdir()) == []


@pytest.mark.asyncio
async def test_structured_text_invocation_hardens_thread_and_parses_only_final_item(
    project_tmp_path: Path,
) -> None:
    def respond(
        message: dict[str, object],
    ) -> dict[str, object] | list[dict[str, object]] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return None
        if method == "initialize":
            return {"id": request_id, "result": {}}
        if method == "account/read":
            return {
                "id": request_id,
                "result": {
                    "requiresOpenaiAuth": False,
                    "account": {
                        "type": "chatgpt",
                        "email": None,
                        "planType": "free",
                    },
                },
            }
        if method == "model/list":
            return {
                "id": request_id,
                "result": {
                    "data": [_model("codex-exact", modalities=["text"])],
                    "nextCursor": None,
                },
            }
        if method == "modelProvider/capabilities/read":
            return {
                "id": request_id,
                "result": {
                    "imageGeneration": False,
                    "namespaceTools": False,
                    "webSearch": False,
                },
            }
        if method == "thread/start":
            return [
                {
                    "id": request_id,
                    "result": {
                        "thread": {"id": "thread-1"},
                        "model": "codex-exact",
                    },
                },
                {
                    "method": "thread/started",
                    "params": {"thread": {"id": "thread-1"}},
                },
            ]
        if method == "turn/start":
            return [
                {
                    "id": request_id,
                    "result": {"turn": {"id": "turn-1"}},
                },
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1"},
                    },
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "message-1",
                        "delta": "ignored",
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "completedAtMs": 1,
                        "item": {
                            "id": "message-1",
                            "type": "agentMessage",
                            "text": '{"value":"safe"}',
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {
                            "id": "turn-1",
                            "items": [],
                            "status": "completed",
                        },
                    },
                },
            ]
        if method == "thread/delete":
            return {"id": request_id, "result": {}}
        raise AssertionError(f"unexpected method: {method}")

    process = _FakeProcess(respond)
    runtime_root = project_tmp_path / "codex-runtime"
    client = CodexAppServerClient(
        command=("codex", "app-server"),
        configured_models={"codex-exact"},
        sandbox_root=runtime_root,
        process_factory=_FakeFactory(process),
    )

    await client.inspect()
    result = await client.invoke(
        [ModelMessage(role="user", content="protected prompt")],
        _Answer,
        model="codex-exact",
        timeout_seconds=1,
    )

    assert result.output == _Answer(value="safe")
    assert result.usage is None
    thread_request = next(
        item for item in process.stdin.messages if item.get("method") == "thread/start"
    )
    assert thread_request["params"] == {
        "model": "codex-exact",
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "cwd": str(runtime_root / "cwd"),
    }
    turn_request = next(
        item for item in process.stdin.messages if item.get("method") == "turn/start"
    )
    assert turn_request["params"] == {
        "threadId": "thread-1",
        "input": [{"type": "text", "text": "protected prompt"}],
        "outputSchema": {
            **_Answer.model_json_schema(),
            "additionalProperties": False,
        },
        "effort": "none",
        "summary": "none",
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
    }
    assert [
        item for item in process.stdin.messages if item.get("method") == "thread/delete"
    ] == [
        {
            "id": 7,
            "method": "thread/delete",
            "params": {"threadId": "thread-1"},
        }
    ]

    await client.aclose()
