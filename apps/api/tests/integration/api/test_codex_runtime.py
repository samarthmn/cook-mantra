import asyncio
import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import BaseModel

from api.app import create_app
from core.config import Settings
from core.runtime_config import ProviderConfiguration, get_runtime_snapshot
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ModelMessage,
    ModelResult,
    ModelUsage,
    ProviderDiscoveryResult,
    ProviderName,
)


class _Answer(BaseModel):
    value: str


def _codex_snapshot(*, allow_unverified_tool_boundary: bool = False):
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = ProviderConfiguration(
        command=("codex", "app-server"),
        allow_unverified_tool_boundary=allow_unverified_tool_boundary,
    )
    roles = {
        role: selection.model_copy(
            update={"provider": ProviderName.CODEX, "model": "codex-vision"}
        )
        if role.required
        else selection
        for role, selection in config.roles.items()
    }
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {**config.providers, ProviderName.CODEX: provider},
                    "roles": roles,
                }
            )
        }
    )


def _openrouter_snapshot():
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = ProviderConfiguration(
        endpoint="https://openrouter.test/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    )
    roles = {
        role: selection.model_copy(
            update={
                "provider": ProviderName.OPENROUTER,
                "model": f"vendor/{role.value}",
            }
        )
        if role.required
        else selection
        for role, selection in config.roles.items()
    }
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: provider,
                    },
                    "roles": roles,
                }
            )
        }
    )


def _ollama_snapshot():
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = ProviderConfiguration(
        endpoint="http://127.0.0.1:11434",
    )
    roles = {
        role: selection.model_copy(
            update={"provider": ProviderName.OLLAMA, "model": "local-model"}
        )
        if role.required
        else selection
        for role, selection in config.roles.items()
    }
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {**config.providers, ProviderName.OLLAMA: provider},
                    "roles": roles,
                }
            )
        }
    )


def _all_codex_snapshot():
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = ProviderConfiguration(
        command=("codex", "app-server"),
        allow_unverified_tool_boundary=True,
    )
    roles = {
        role: selection.model_copy(
            update={"provider": ProviderName.CODEX, "model": "codex-exact"}
        )
        if role.required
        else selection
        for role, selection in config.roles.items()
    }
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {**config.providers, ProviderName.CODEX: provider},
                    "roles": roles,
                }
            )
        }
    )


def _codex_image_only_snapshot(*, enabled: bool):
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = ProviderConfiguration(
        command=("codex", "app-server"),
    )
    ollama_provider = ProviderConfiguration(
        endpoint="http://127.0.0.1:11434",
    )
    image_role = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={
            "enabled": enabled,
            "provider": ProviderName.CODEX,
            "model": "codex-image",
        }
    )
    roles = {
        role: selection.model_copy(
            update={"provider": ProviderName.OLLAMA, "model": "local-model"}
        )
        if role.required
        else image_role
        for role, selection in config.roles.items()
    }
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.CODEX: provider,
                        ProviderName.OLLAMA: ollama_provider,
                    },
                    "roles": roles,
                }
            )
        }
    )


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(output, format="PNG")
    return output.getvalue()


class _Reader:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.queue.get()


class _Writer:
    def __init__(self, process: "_Process") -> None:
        self.process = process
        self.messages: list[dict[str, object]] = []

    def write(self, data: bytes) -> None:
        message = json.loads(data)
        self.messages.append(message)
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialized":
            return
        if method == "initialize":
            result: dict[str, object] = {}
        elif method == "account/read":
            result = {
                "requiresOpenaiAuth": False,
                "account": {"type": "apiKey"},
            }
        elif method == "model/list":
            result = {
                "data": [
                    {
                        "id": "catalog-codex-vision",
                        "model": "codex-vision",
                        "displayName": "Codex Vision",
                        "hidden": False,
                        "isDefault": False,
                        "description": "test",
                        "defaultReasoningEffort": "low",
                        "supportedReasoningEfforts": [],
                        "inputModalities": ["text", "image"],
                    }
                ],
                "nextCursor": None,
            }
        elif method == "modelProvider/capabilities/read":
            result = {
                "imageGeneration": False,
                "namespaceTools": False,
                "webSearch": False,
            }
        else:
            raise AssertionError(f"unexpected method: {method}")
        self.process.stdout.queue.put_nowait(
            json.dumps({"id": request_id, "result": result}).encode() + b"\n"
        )

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _Process:
    def __init__(self) -> None:
        self.stdout = _Reader()
        self.stderr = _Reader()
        self.stdin = _Writer(self)
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0
        self.stdout.queue.put_nowait(b"")
        self.stderr.queue.put_nowait(b"")

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        self.wait_calls += 1
        return 0


class _Factory:
    def __init__(self) -> None:
        self.process = _Process()
        self.commands: list[tuple[str, ...]] = []

    async def spawn(self, command: tuple[str, ...]) -> _Process:
        self.commands.append(command)
        return self.process


class _MustNotSpawn:
    async def spawn(self, command: tuple[str, ...]):
        raise AssertionError(f"non-Codex configuration spawned {command}")


class _ReadyDiscovery:
    async def inspect(self) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.CODEX,
            models={
                "codex-vision": DiscoveredModel(
                    model="codex-vision",
                    available=True,
                    capabilities=(
                        Capability.STRUCTURED_OUTPUT,
                        Capability.TEXT,
                        Capability.VISION,
                    ),
                )
            },
        )


class _WholeOwner:
    def __init__(self) -> None:
        self.inspect_calls = 0
        self.invoke_calls = 0
        self.composed_calls = 0
        self.close_calls = 0

    def _inspection(self) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.CODEX,
            models={
                "codex-exact": DiscoveredModel(
                    model="codex-exact",
                    available=True,
                    capabilities=(
                        Capability.STRUCTURED_OUTPUT,
                        Capability.TEXT,
                        Capability.VISION,
                    ),
                )
            },
        )

    async def inspect(self) -> ProviderDiscoveryResult:
        self.inspect_calls += 1
        return self._inspection()

    async def invoke(
        self,
        messages,
        schema,
        *,
        model: str,
        timeout_seconds: float,
    ):
        self.invoke_calls += 1
        assert model == "codex-exact"
        assert timeout_seconds == 300
        assert messages == [ModelMessage(role="user", content="private-prompt-canary")]
        return ModelResult(
            output=schema.model_validate({"value": "codex"}),
            usage=ModelUsage(input_tokens=4, output_tokens=2),
        )

    async def invoke_admitted(
        self,
        messages,
        schema,
        *,
        model: str,
        timeout_seconds: float,
        admit,
    ):
        self.composed_calls += 1
        admit(self._inspection())
        return await self.invoke(
            messages,
            schema,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    async def aclose(self) -> None:
        self.close_calls += 1


def test_codex_owner_is_shared_lazy_gated_and_closed_after_admission(
    project_tmp_path: Path,
) -> None:
    factory = _Factory()
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        runtime_snapshot=_codex_snapshot(),
        codex_process_factory=factory,
    )
    assert factory.commands == []

    with TestClient(app) as client:
        ready = client.get("/api/v1/ready")
        upload = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", _png(), "image/png")},
        )

        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "model_capability_missing"
        assert upload.status_code == 503
        assert upload.json()["error"]["code"] == "model_capability_missing"
        assert app.state.session_store._sessions == {}
        assert app.state.job_store._jobs == {}
        assert factory.commands == [("codex", "app-server")]
        assert app.state.codex_app_server_client is not None

    assert factory.process.terminate_calls == 1
    assert factory.process.wait_calls == 1


def test_codex_opt_in_makes_matching_required_roles_ready(
    project_tmp_path: Path,
) -> None:
    factory = _Factory()
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        runtime_snapshot=_codex_snapshot(allow_unverified_tool_boundary=True),
        codex_process_factory=factory,
    )

    with TestClient(app) as client:
        ready = client.get("/api/v1/ready")
        status = client.get("/api/v1/runtime-status")

    assert ready.status_code == 200
    assert status.status_code == 200
    runtime = status.json()["model_runtime"]
    assert runtime["ready"] is True
    assert all(
        runtime["roles"][role.value]["ready"] is True
        for role in AgentRole
        if role.required
    )
    assert runtime["roles"]["image_generator"]["enabled"] is False
    assert factory.commands == [("codex", "app-server")]


@pytest.mark.parametrize(
    "runtime_snapshot",
    [_ollama_snapshot(), _openrouter_snapshot()],
    ids=["ollama", "openrouter"],
)
def test_non_codex_configuration_never_creates_or_spawns_codex(
    project_tmp_path: Path,
    runtime_snapshot,
) -> None:
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        runtime_snapshot=runtime_snapshot,
        codex_process_factory=_MustNotSpawn(),
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert app.state.codex_app_server_client is None


def test_alternate_ready_discovery_cannot_bypass_production_owner_gate(
    project_tmp_path: Path,
) -> None:
    factory = _Factory()
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        runtime_snapshot=_codex_snapshot(),
        provider_discoveries={ProviderName.CODEX: _ReadyDiscovery()},
        codex_process_factory=factory,
    )

    with TestClient(app) as client:
        ready = client.get("/api/v1/ready")

        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "model_capability_missing"
        assert all(
            message.get("method") not in {"thread/start", "turn/start"}
            for message in factory.process.stdin.messages
        )


def test_whole_owner_composes_readiness_invocation_usage_and_shutdown(
    project_tmp_path: Path,
) -> None:
    owner = _WholeOwner()
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        runtime_snapshot=_all_codex_snapshot(),
        codex_owner=owner,
        codex_process_factory=_MustNotSpawn(),
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/ready").status_code == 200
        model = app.state.structured_model_factory.build(
            AgentRole.MASTER_CHEF,
            _Answer,
        )
        result = asyncio.run(
            model.ainvoke(
                [ModelMessage(role="user", content="private-prompt-canary")],
                config={
                    "metadata": {
                        "session_id": "session-safe",
                        "job_id": "job-safe",
                        "private_prompt": "private-prompt-canary",
                    }
                },
            )
        )
        usage = asyncio.run(app.state.model_usage_journal.snapshot())

        assert result == _Answer(value="codex")
        assert owner.inspect_calls == 1
        assert owner.composed_calls == 1
        assert owner.invoke_calls == 1
        assert len(usage) == 1
        assert usage[0].usage == ModelUsage(input_tokens=4, output_tokens=2)
        assert usage[0].session_id == "session-safe"
        assert usage[0].job_id == "job-safe"
        assert "private-prompt-canary" not in repr(usage[0])

    assert owner.close_calls == 1


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
def test_codex_image_only_configuration_never_creates_owner_or_invokes_transport(
    project_tmp_path: Path,
    enabled: bool,
) -> None:
    owner = _WholeOwner()
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        runtime_snapshot=_codex_image_only_snapshot(enabled=enabled),
        codex_owner=owner,
        codex_process_factory=_MustNotSpawn(),
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert app.state.codex_app_server_client is None

    assert owner.inspect_calls == 0
    assert owner.composed_calls == 0
    assert owner.invoke_calls == 0
    assert owner.close_calls == 0
