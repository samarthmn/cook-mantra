"""Narrow, redaction-safe owner for one local Codex app-server child."""

import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from domain.model_runtime import (
    Capability,
    DiscoveredModel,
    ModelMessage,
    ModelResult,
    ModelUsage,
    ProviderDiscoveryResult,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
)
from services.provider_errors import ProviderInvocationError

_MAX_FRAME_BYTES = 1_048_576
_MAX_CATALOG_PAGES = 100
_CHATGPT_PLAN_TYPES = frozenset(
    {
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "team",
        "self_serve_business_usage_based",
        "business",
        "ent26",
        "enterprise_cbp_usage_based",
        "enterprise",
        "edu",
        "unknown",
    }
)
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SYSTEMROOT",
    }
)
_INVOCATION_NOTIFICATIONS = frozenset(
    {
        "account/rateLimits/updated",
        "item/started",
        "thread/started",
        "turn/started",
        "item/agentMessage/delta",
        "item/completed",
        "mcpServer/startupStatus/updated",
        "thread/tokenUsage/updated",
        "turn/completed",
        "error",
    }
)
_DISCOVERY_NOTIFICATIONS = frozenset(
    {
        "account/rateLimits/updated",
        "account/updated",
        "config/warning",
        "deprecationNotice",
        "model/verification",
        "remoteControl/status/changed",
        "warning",
    }
)


class AppServerReader(Protocol):
    async def readline(self) -> bytes: ...


class AppServerWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...


class AppServerProcess(Protocol):
    stdin: AppServerWriter
    stdout: AppServerReader
    stderr: AppServerReader
    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


class AppServerProcessFactory(Protocol):
    async def spawn(self, command: tuple[str, ...]) -> AppServerProcess: ...


class AsyncioAppServerProcessFactory:
    """Spawn the configured argv directly, never through a shell."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str],
        temp_root: Path,
    ) -> None:
        self._environment = {
            key: value
            for key, value in environment.items()
            if key in _CHILD_ENV_ALLOWLIST
        }
        self._temp_root = temp_root.absolute()

    async def spawn(self, command: tuple[str, ...]) -> AppServerProcess:
        self._temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._temp_root, 0o700)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_MAX_FRAME_BYTES + 1,
            env={**self._environment, "TMPDIR": str(self._temp_root)},
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.terminate()
            await process.wait()
            raise RuntimeError("Codex app-server pipes are unavailable.")
        return cast(AppServerProcess, process)


class _ProtocolViolation(Exception):
    pass


class _ChildLost(Exception):
    pass


class _ReplayableChildLoss(Exception):
    pass


def _codex_output_schema(schema: type[BaseModel]) -> dict[str, object]:
    """Convert Pydantic JSON Schema to Codex's strict structured-output subset."""
    document = schema.model_json_schema()

    def make_strict(node: object) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                make_strict(value)
        elif isinstance(node, list):
            for value in node:
                make_strict(value)

    make_strict(document)
    return cast(dict[str, object], document)


@dataclass(frozen=True, slots=True)
class _CatalogModel:
    model: str
    modalities: frozenset[str]


@dataclass(slots=True)
class _Invocation:
    thread_id: str | None = None
    turn_id: str | None = None
    final_item_id: str | None = None
    final_text: str | None = None
    usage: ModelUsage | None = None
    completed: bool = False
    cleanup_task: asyncio.Task[None] | None = None
    thread_deleted: bool = False
    generation_invalidated: bool = False
    pending_notifications: list[dict[str, object]] | None = None


class CodexAppServerClient:
    """Own one lazy child and expose provider-neutral discovery snapshots."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        configured_models: set[str] | frozenset[str],
        sandbox_root: Path,
        process_factory: AppServerProcessFactory | None = None,
        max_frame_bytes: int = _MAX_FRAME_BYTES,
        drain_timeout_seconds: float = 1.0,
        cleanup_timeout_seconds: float | None = None,
        readiness_timeout_seconds: float = 5.0,
        allow_unverified_tool_boundary: bool = False,
    ) -> None:
        self._command = tuple(command)
        self._configured_models = tuple(sorted(configured_models))
        self._sandbox_root = sandbox_root.absolute()
        self._process_factory = process_factory or AsyncioAppServerProcessFactory(
            environment=os.environ,
            temp_root=self._sandbox_root / "tmp",
        )
        self._max_frame_bytes = max_frame_bytes
        self._cleanup_timeout_seconds = (
            drain_timeout_seconds
            if cleanup_timeout_seconds is None
            else cleanup_timeout_seconds
        )
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._allow_unverified_tool_boundary = allow_unverified_tool_boundary
        self._process: AppServerProcess | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._restart_task: asyncio.Task[None] | None = None
        self._generation_poisoned = False
        self._next_request_id = 1
        self._inspection: ProviderDiscoveryResult | None = None
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._active_task: asyncio.Task[object] | None = None
        self._pending_media_cleanup: set[Path] = set()
        self._closed = False

    async def inspect(self) -> ProviderDiscoveryResult:
        """Discover once per child generation and return a defensive snapshot."""
        acquired = False
        try:
            async with asyncio.timeout(self._readiness_timeout_seconds):
                await self._lock.acquire()
                acquired = True
                return await self._inspect_locked()
        except TimeoutError:
            if acquired:
                with suppress(Exception):
                    await self._restart_child()
            return self._failed_inspection(ProviderErrorKind.TIMED_OUT)
        except asyncio.CancelledError:
            if acquired:
                with suppress(asyncio.CancelledError, Exception):
                    await self._restart_child()
            raise
        finally:
            if acquired:
                self._lock.release()

    async def _inspect_locked(self) -> ProviderDiscoveryResult:
        self._raise_if_closed()
        if self._inspection is None:
            failure_kind = ProviderErrorKind.UNAVAILABLE
            for attempt in range(2):
                try:
                    await self._ensure_initialized()
                    self._inspection = await self._discover()
                    break
                except asyncio.CancelledError:
                    raise
                except (_ChildLost, OSError, RuntimeError):
                    failure_kind = ProviderErrorKind.UNAVAILABLE
                except _ProtocolViolation:
                    failure_kind = ProviderErrorKind.PROTOCOL_ERROR
                    await self._restart_child()
                    self._inspection = self._failed_inspection(failure_kind)
                    break
                try:
                    await self._restart_child()
                except Exception:
                    failure_kind = ProviderErrorKind.UNAVAILABLE
                if attempt == 1:
                    self._inspection = self._failed_inspection(failure_kind)
            if self._inspection is None:
                self._inspection = self._failed_inspection(failure_kind)
        return self._inspection.model_copy(deep=True)

    async def _ensure_initialized(self) -> None:
        if self._restart_task is not None or self._generation_poisoned:
            await self._restart_child()
        if self._process is not None:
            return
        self._prepare_owned_directories()
        self._process = await self._process_factory.spawn(self._command)
        self._stderr_task = asyncio.create_task(self._discard_stderr(self._process))
        await self._request(
            "initialize",
            {
                "clientInfo": {"name": "cook-mantra-api", "version": "0.1.0"},
                "capabilities": {
                    "optOutNotificationMethods": ["thread/status/changed"]
                },
            },
        )
        await self._notify("initialized")

    async def _discover(self) -> ProviderDiscoveryResult:
        account = await self._request("account/read", {})
        raw_account = account.get("account")
        if not isinstance(account.get("requiresOpenaiAuth"), bool) or not (
            raw_account is None or self._valid_account(raw_account)
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        if raw_account is None:
            return self._failed_inspection(ProviderErrorKind.AUTHENTICATION_FAILED)

        catalog: list[_CatalogModel] = []
        cursor: str | None = None
        for _ in range(_MAX_CATALOG_PAGES):
            page = await self._request(
                "model/list",
                {"cursor": cursor, "includeHidden": False},
            )
            data = page.get("data")
            next_cursor = page.get("nextCursor")
            if not isinstance(data, list) or not (
                next_cursor is None or isinstance(next_cursor, str)
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            catalog.extend(self._parse_catalog_page(data))
            if next_cursor is None:
                break
            cursor = next_cursor
        else:
            raise _ProtocolViolation("Codex app-server protocol failure.")

        capabilities = await self._request("modelProvider/capabilities/read", {})
        if any(
            not isinstance(capabilities.get(field), bool)
            for field in ("imageGeneration", "namespaceTools", "webSearch")
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")

        models: dict[str, DiscoveredModel] = {}
        for configured_model in self._configured_models:
            matches = [item for item in catalog if item.model == configured_model]
            if len(matches) != 1:
                models[configured_model] = DiscoveredModel(
                    model=configured_model,
                    available=False,
                    error=ProviderErrorKind.CAPABILITY_MISSING,
                )
                continue
            discovered_capabilities: set[Capability] = set()
            if "text" in matches[0].modalities:
                discovered_capabilities.update(
                    {Capability.STRUCTURED_OUTPUT, Capability.TEXT}
                )
            if "image" in matches[0].modalities:
                discovered_capabilities.add(Capability.VISION)
            models[configured_model] = DiscoveredModel(
                model=configured_model,
                available=self._allow_unverified_tool_boundary,
                capabilities=tuple(sorted(discovered_capabilities)),
                error=(
                    None
                    if self._allow_unverified_tool_boundary
                    else ProviderErrorKind.CAPABILITY_MISSING
                ),
            )
        return ProviderDiscoveryResult(provider=ProviderName.CODEX, models=models)

    async def invoke[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        model: str,
        timeout_seconds: float,
    ) -> ModelResult[OutputT]:
        """Run one serialized temporary turn and validate its final message."""
        return await self._invoke_operation(
            messages,
            schema,
            model=model,
            timeout_seconds=timeout_seconds,
            admit=None,
        )

    async def invoke_admitted[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        model: str,
        timeout_seconds: float,
        admit: Callable[[ProviderDiscoveryResult], None],
    ) -> ModelResult[OutputT]:
        """Discover, admit, and invoke on one generation and absolute deadline."""
        return await self._invoke_operation(
            messages,
            schema,
            model=model,
            timeout_seconds=timeout_seconds,
            admit=admit,
        )

    async def _invoke_operation[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        model: str,
        timeout_seconds: float,
        admit: Callable[[ProviderDiscoveryResult], None] | None,
    ) -> ModelResult[OutputT]:
        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 0.0)
        acquired = False
        active_task = asyncio.current_task()
        try:
            async with asyncio.timeout_at(deadline):
                await self._lock.acquire()
                acquired = True
                self._raise_if_closed()
                self._active_task = cast(asyncio.Task[object] | None, active_task)
                if admit is not None:
                    inspection = await self._inspect_locked()
                    admit(inspection.model_copy(deep=True))
                return await self._invoke_locked(
                    messages,
                    schema,
                    model=model,
                )
        except TimeoutError:
            if acquired and self._process is not None:
                with suppress(Exception):
                    await self._restart_child()
            raise _normalized(
                ProviderErrorKind.TIMED_OUT,
                "The model operation timed out.",
                retryable=False,
            ) from None
        except asyncio.CancelledError as cancellation:
            if acquired:
                with suppress(asyncio.CancelledError, Exception):
                    await self._restart_child()
            raise cancellation
        finally:
            if self._active_task is active_task:
                self._active_task = None
            if acquired:
                self._lock.release()

    async def _invoke_locked[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        model: str,
    ) -> ModelResult[OutputT]:
        if (
            len(messages) != 1
            or not isinstance(messages[0], ModelMessage)
            or messages[0].role != "user"
        ):
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model request is invalid.",
                retryable=False,
            )
        if not self._retry_pending_media_cleanup():
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "Sensitive model media cleanup failed.",
                retryable=False,
            )
        try:
            await self._ensure_initialized()
        except _ProtocolViolation:
            await self._restart_child()
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=False,
            ) from None
        except (_ChildLost, OSError, RuntimeError):
            await self._restart_child()
            raise _normalized(
                ProviderErrorKind.UNAVAILABLE,
                "The selected model provider is unavailable.",
                retryable=False,
            ) from None
        image_path: Path | None = None
        try:
            if messages[0].image is not None:
                image_path = self._write_owned_media(
                    messages[0].image,
                    messages[0].media_type,
                )
            for attempt in range(2):
                invocation = _Invocation()
                try:
                    return await self._invoke_turn(
                        invocation,
                        messages[0].content,
                        schema,
                        model,
                        image_path,
                    )
                except _ReplayableChildLoss:
                    if attempt == 1:
                        await self._restart_child()
                        raise _normalized(
                            ProviderErrorKind.UNAVAILABLE,
                            "The selected model provider is unavailable.",
                            retryable=False,
                        ) from None
                    await self._restart_child()
                    try:
                        await self._ensure_initialized()
                        replacement = await self._discover()
                    except _ProtocolViolation:
                        await self._restart_child()
                        raise _normalized(
                            ProviderErrorKind.PROTOCOL_ERROR,
                            "The model provider returned an invalid response.",
                            retryable=False,
                        ) from None
                    except (_ChildLost, OSError, RuntimeError):
                        await self._restart_child()
                        raise _normalized(
                            ProviderErrorKind.UNAVAILABLE,
                            "The selected model provider is unavailable.",
                            retryable=False,
                        ) from None
                    self._inspection = replacement
                    replaced_model = replacement.models.get(model)
                    if (
                        replaced_model is not None
                        and replaced_model.error
                        is ProviderErrorKind.AUTHENTICATION_FAILED
                    ):
                        raise _normalized(
                            ProviderErrorKind.AUTHENTICATION_FAILED,
                            "Model provider authentication failed.",
                            retryable=False,
                        ) from None
        finally:
            if image_path is not None:
                primary_active = sys.exc_info()[0] is not None
                if not self._remove_owned_media(image_path) and not primary_active:
                    raise _normalized(
                        ProviderErrorKind.PROTOCOL_ERROR,
                        "Sensitive model media cleanup failed.",
                        retryable=False,
                    ) from None

    async def _invoke_turn[OutputT: BaseModel](
        self,
        invocation: _Invocation,
        prompt: str,
        schema: type[OutputT],
        model: str,
        image_path: Path | None,
    ) -> ModelResult[OutputT]:
        thread_confirmed = False
        protocol_failure = False
        child_lost = False
        cancellation: asyncio.CancelledError | None = None
        try:
            await self._settled_start_request(
                "thread/start",
                {
                    "model": model,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "cwd": str(self._sandbox_root / "cwd"),
                },
                invocation,
                lambda result: self._accept_thread_start(
                    result,
                    invocation,
                    model,
                ),
            )
            assert invocation.thread_id is not None
            thread_id = invocation.thread_id
            thread_confirmed = True
            self._flush_pending_notifications(invocation, thread_only=True)

            inputs: list[dict[str, object]] = [{"type": "text", "text": prompt}]
            if image_path is not None:
                inputs.append(
                    {
                        "type": "localImage",
                        "path": str(image_path),
                        "detail": "low",
                    }
                )
            await self._settled_start_request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": inputs,
                    "outputSchema": _codex_output_schema(schema),
                    "effort": "none",
                    "summary": "none",
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "readOnly",
                        "networkAccess": False,
                    },
                },
                invocation,
                lambda result: self._accept_turn_start(result, invocation),
            )
            self._flush_pending_notifications(invocation, thread_only=False)
            while not invocation.completed:
                message = await self._read_message()
                if "method" not in message or "id" in message:
                    raise _ProtocolViolation("Codex app-server protocol failure.")
                self._handle_notification(message, invocation)
            if invocation.final_text is None:
                raise _ProtocolViolation("Codex app-server protocol failure.")
            invalid_output = False
            try:
                output = schema.model_validate_json(invocation.final_text)
            except (ValidationError, ValueError, TypeError):
                invalid_output = True
            if invalid_output:
                raise _normalized(
                    ProviderErrorKind.INVALID_OUTPUT,
                    "The model returned invalid structured output.",
                    retryable=True,
                )
            return ModelResult(output=output, usage=invocation.usage)
        except asyncio.CancelledError as error:
            cancellation = error
            if (
                invocation.thread_id is not None
                and not invocation.generation_invalidated
            ):
                if invocation.cleanup_task is None:
                    invocation.cleanup_task = asyncio.create_task(
                        self._cleanup_cancelled_invocation(invocation)
                    )
                while not invocation.cleanup_task.done():
                    try:
                        await asyncio.shield(invocation.cleanup_task)
                    except asyncio.CancelledError:
                        continue
                with suppress(
                    _ChildLost,
                    _ProtocolViolation,
                    ProviderInvocationError,
                ):
                    invocation.cleanup_task.result()
        except _ChildLost:
            child_lost = True
        except _ProtocolViolation:
            protocol_failure = True
        finally:
            primary_active = sys.exc_info()[0] is not None
            if (
                thread_confirmed
                and invocation.thread_id is not None
                and not invocation.thread_deleted
                and not invocation.generation_invalidated
                and not child_lost
                and self._process is not None
            ):
                try:
                    async with asyncio.timeout(self._cleanup_timeout_seconds):
                        await self._delete_thread(invocation)
                except (
                    TimeoutError,
                    _ChildLost,
                    _ProtocolViolation,
                    ProviderInvocationError,
                ):
                    protocol_failure = True
                    await self._restart_child()
                    if not primary_active and cancellation is None:
                        raise _normalized(
                            ProviderErrorKind.PROTOCOL_ERROR,
                            "The model provider returned an invalid response.",
                            retryable=False,
                        ) from None
        if cancellation is not None:
            raise cancellation
        if child_lost:
            if invocation.final_text is None:
                raise _ReplayableChildLoss
            await self._restart_child()
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=False,
            )
        if protocol_failure:
            await self._restart_child()
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=False,
            )
        raise RuntimeError("Codex invocation completed without a result.")

    async def _settled_start_request(
        self,
        method: str,
        params: dict[str, object],
        invocation: _Invocation,
        accept: Callable[[dict[str, object]], None],
    ) -> None:
        request_task = asyncio.create_task(
            self._request(method, params, invocation=invocation)
        )
        try:
            result = await asyncio.shield(request_task)
        except asyncio.CancelledError as cancellation:
            recovery_task = asyncio.create_task(
                self._recover_cancelled_start(request_task, invocation, accept)
            )
            while not recovery_task.done():
                try:
                    await asyncio.shield(recovery_task)
                except asyncio.CancelledError:
                    continue
            with suppress(Exception):
                recovery_task.result()
            raise cancellation
        accept(result)

    async def _recover_cancelled_start(
        self,
        request_task: asyncio.Task[dict[str, object]],
        invocation: _Invocation,
        accept: Callable[[dict[str, object]], None],
    ) -> None:
        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                result = await asyncio.shield(request_task)
            accept(result)
            return
        except (
            TimeoutError,
            _ChildLost,
            _ProtocolViolation,
            ProviderInvocationError,
            OSError,
            RuntimeError,
        ):
            invocation.generation_invalidated = True
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
        await self._restart_child()

    def _accept_thread_start(
        self,
        result: dict[str, object],
        invocation: _Invocation,
        model: str,
    ) -> None:
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        returned_model = result.get("model")
        if (
            not isinstance(thread_id, str)
            or not thread_id
            or (returned_model is not None and returned_model != model)
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        invocation.thread_id = thread_id

    def _accept_turn_start(
        self,
        result: dict[str, object],
        invocation: _Invocation,
    ) -> None:
        turn = result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise _ProtocolViolation("Codex app-server protocol failure.")
        invocation.turn_id = turn_id

    async def _cleanup_cancelled_invocation(self, invocation: _Invocation) -> None:
        cleanup_failed = False
        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                if invocation.turn_id is not None:
                    await self._request(
                        "turn/interrupt",
                        {
                            "threadId": invocation.thread_id,
                            "turnId": invocation.turn_id,
                        },
                        invocation=invocation,
                        cleanup=True,
                    )
                    if not invocation.completed:
                        self._handle_cleanup_notification(
                            await self._read_message(),
                            invocation,
                        )
                await self._delete_thread(invocation)
        except (
            TimeoutError,
            _ChildLost,
            _ProtocolViolation,
            ProviderInvocationError,
        ):
            cleanup_failed = True
        if cleanup_failed:
            await self._restart_child()

    async def _delete_thread(self, invocation: _Invocation) -> None:
        if invocation.thread_deleted or invocation.thread_id is None:
            return
        await self._request(
            "thread/delete",
            {"threadId": invocation.thread_id},
            invocation=invocation,
            cleanup=True,
        )
        invocation.thread_deleted = True

    def _valid_account(self, raw: object) -> bool:
        if not isinstance(raw, Mapping):
            return False
        account_type = raw.get("type")
        if account_type == "apiKey":
            return True
        if account_type == "chatgpt":
            email = raw.get("email")
            plan_type = raw.get("planType")
            return (
                "email" in raw
                and (email is None or isinstance(email, str))
                and isinstance(plan_type, str)
                and plan_type in _CHATGPT_PLAN_TYPES
            )
        if account_type == "amazonBedrock":
            return "usesCodexManagedCredentials" not in raw or isinstance(
                raw.get("usesCodexManagedCredentials"),
                bool,
            )
        return False

    def _parse_catalog_page(self, data: list[object]) -> list[_CatalogModel]:
        parsed: list[_CatalogModel] = []
        required = {
            "id": str,
            "model": str,
            "displayName": str,
            "hidden": bool,
            "isDefault": bool,
            "description": str,
            "defaultReasoningEffort": str,
            "supportedReasoningEfforts": list,
        }
        for raw in data:
            if not isinstance(raw, Mapping) or any(
                not isinstance(raw.get(field), expected)
                for field, expected in required.items()
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            modalities = raw.get("inputModalities", ["text", "image"])
            if not isinstance(modalities, list) or any(
                modality not in {"text", "image", "audio"}
                for modality in modalities
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            parsed.append(
                _CatalogModel(
                    model=cast(str, raw["model"]),
                    modalities=frozenset(cast(list[str], modalities)),
                )
            )
        return parsed

    async def _request(
        self,
        method: str,
        params: dict[str, object],
        *,
        invocation: _Invocation | None = None,
        cleanup: bool = False,
    ) -> dict[str, object]:
        request_id = self._next_request_id
        self._next_request_id += 1
        await self._write({"id": request_id, "method": method, "params": params})
        while True:
            message = await self._read_message()
            if "method" in message:
                if "id" in message:
                    raise _ProtocolViolation("Codex app-server protocol failure.")
                if invocation is None:
                    notification_method = message.get("method")
                    if (
                        notification_method not in _DISCOVERY_NOTIFICATIONS
                        or not isinstance(message.get("params"), Mapping)
                    ):
                        raise _ProtocolViolation(
                            "Codex app-server protocol failure."
                        )
                    continue
                if cleanup:
                    self._handle_cleanup_notification(message, invocation)
                else:
                    method_name = message.get("method")
                    if method_name not in _INVOCATION_NOTIFICATIONS:
                        raise _ProtocolViolation("Codex app-server protocol failure.")
                    if self._notification_needs_buffering(method_name, invocation):
                        if invocation.pending_notifications is None:
                            invocation.pending_notifications = []
                        invocation.pending_notifications.append(message)
                    else:
                        self._handle_notification(message, invocation)
                continue
            if (
                message.get("id") != request_id
                or isinstance(message.get("id"), bool)
                or "error" in message
                or not isinstance(message.get("result"), dict)
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            return cast(dict[str, object], message["result"])

    def _handle_cleanup_notification(
        self,
        message: dict[str, object],
        invocation: _Invocation,
    ) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(params, Mapping):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        if method == "turn/completed":
            turn = params.get("turn")
            status = turn.get("status") if isinstance(turn, Mapping) else None
            self._accept_terminal(
                params,
                invocation,
                allowed_statuses={"completed", "interrupted", "failed"},
                require_final=status == "completed",
            )
            return
        if method in {"item/completed", "thread/tokenUsage/updated"}:
            self._handle_notification(message, invocation)
            return
        raise _ProtocolViolation("Codex app-server protocol failure.")

    def _notification_needs_buffering(
        self,
        method: object,
        invocation: _Invocation,
    ) -> bool:
        if method == "thread/started":
            return invocation.thread_id is None
        return invocation.turn_id is None

    def _flush_pending_notifications(
        self,
        invocation: _Invocation,
        *,
        thread_only: bool,
    ) -> None:
        pending = invocation.pending_notifications or []
        retained: list[dict[str, object]] = []
        for message in pending:
            if thread_only and message.get("method") != "thread/started":
                retained.append(message)
                continue
            self._handle_notification(message, invocation)
        invocation.pending_notifications = retained or None

    def _handle_notification(
        self,
        message: dict[str, object],
        invocation: _Invocation,
    ) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, Mapping):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        if method == "thread/started":
            thread = params.get("thread")
            thread_id = thread.get("id") if isinstance(thread, Mapping) else None
            if invocation.thread_id is None or thread_id != invocation.thread_id:
                raise _ProtocolViolation("Codex app-server protocol failure.")
            return
        if method == "turn/started":
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            if (
                invocation.thread_id is None
                or params.get("threadId") != invocation.thread_id
                or invocation.turn_id is None
                or turn_id != invocation.turn_id
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            return
        if method == "item/agentMessage/delta":
            self._require_active_ids(params, invocation)
            if not isinstance(params.get("itemId"), str) or not isinstance(
                params.get("delta"), str
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            return
        if method == "item/started":
            self._accept_started_item(params, invocation)
            return
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping) and item.get("type") == "userMessage":
                self._accept_user_message_item(params, invocation)
            else:
                self._accept_completed_item(params, invocation)
            return
        if method == "mcpServer/startupStatus/updated":
            self._accept_mcp_startup_status(params, invocation)
            return
        if method == "account/rateLimits/updated":
            if not isinstance(params.get("rateLimits"), Mapping):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            return
        if method == "thread/tokenUsage/updated":
            self._require_active_ids(params, invocation)
            token_usage = params.get("tokenUsage")
            if not isinstance(token_usage, Mapping):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            last_values = self._validate_usage_breakdown(token_usage.get("last"))
            self._validate_usage_breakdown(token_usage.get("total"))
            context_window = token_usage.get("modelContextWindow")
            if context_window is not None and (
                not isinstance(context_window, int)
                or isinstance(context_window, bool)
                or context_window < 1
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
            invocation.usage = ModelUsage(
                input_tokens=last_values["inputTokens"],
                output_tokens=last_values["outputTokens"],
            )
            return
        if method == "turn/completed":
            self._accept_terminal(
                params,
                invocation,
                allowed_statuses={"completed"},
                require_final=True,
            )
            return
        if method == "error":
            self._require_active_ids(params, invocation)
            raise _normalized(
                ProviderErrorKind.UNAVAILABLE,
                "The selected model provider is unavailable.",
                retryable=False,
            )
        raise _ProtocolViolation("Codex app-server protocol failure.")

    def _accept_started_item(
        self,
        params: Mapping[str, object],
        invocation: _Invocation,
    ) -> None:
        self._require_active_ids(params, invocation)
        item = params.get("item")
        started_at = params.get("startedAtMs")
        if (
            not isinstance(item, Mapping)
            or item.get("type") not in {"userMessage", "agentMessage"}
            or not isinstance(item.get("id"), str)
            or not isinstance(started_at, int)
            or isinstance(started_at, bool)
            or started_at < 0
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")

    def _accept_user_message_item(
        self,
        params: Mapping[str, object],
        invocation: _Invocation,
    ) -> None:
        self._require_active_ids(params, invocation)
        item = params.get("item")
        completed_at = params.get("completedAtMs")
        if (
            not isinstance(item, Mapping)
            or item.get("type") != "userMessage"
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("content"), list)
            or not isinstance(completed_at, int)
            or isinstance(completed_at, bool)
            or completed_at < 0
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")

    def _accept_mcp_startup_status(
        self,
        params: Mapping[str, object],
        invocation: _Invocation,
    ) -> None:
        error = params.get("error")
        failure_reason = params.get("failureReason")
        thread_id = params.get("threadId")
        if (
            thread_id not in {None, invocation.thread_id}
            or not isinstance(params.get("name"), str)
            or params.get("status")
            not in {"starting", "ready", "failed", "cancelled"}
            or not (error is None or isinstance(error, str))
            or failure_reason not in {None, "reauthenticationRequired"}
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")

    def _accept_completed_item(
        self,
        params: Mapping[str, object],
        invocation: _Invocation,
    ) -> None:
        self._require_active_ids(params, invocation)
        item = params.get("item")
        if (
            invocation.completed
            or not isinstance(item, Mapping)
            or item.get("type") != "agentMessage"
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("text"), str)
            or invocation.final_text is not None
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        invocation.final_item_id = cast(str, item["id"])
        invocation.final_text = cast(str, item["text"])

    def _accept_terminal(
        self,
        params: Mapping[str, object],
        invocation: _Invocation,
        *,
        allowed_statuses: set[str],
        require_final: bool,
    ) -> None:
        turn = params.get("turn")
        items = turn.get("items") if isinstance(turn, Mapping) else None
        if (
            invocation.completed
            or params.get("threadId") != invocation.thread_id
            or not isinstance(turn, Mapping)
            or turn.get("id") != invocation.turn_id
            or turn.get("status") not in allowed_statuses
            or not isinstance(items, list)
            or len(items) > 1
            or (require_final and invocation.final_text is None)
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        if items:
            item = items[0]
            if (
                not isinstance(item, Mapping)
                or item.get("type") != "agentMessage"
                or item.get("id") != invocation.final_item_id
                or item.get("text") != invocation.final_text
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
        invocation.completed = True

    def _validate_usage_breakdown(self, raw: object) -> dict[str, int]:
        if not isinstance(raw, Mapping):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        fields = (
            "inputTokens",
            "cachedInputTokens",
            "outputTokens",
            "reasoningOutputTokens",
            "totalTokens",
        )
        values = {field: raw.get(field) for field in fields}
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values.values()
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        cache_write = raw.get("cacheWriteInputTokens", 0)
        if (
            not isinstance(cache_write, int)
            or isinstance(cache_write, bool)
            or cache_write < 0
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        return cast(dict[str, int], values)

    def _require_active_ids(
        self,
        params: Mapping[str, object],
        invocation: _Invocation,
    ) -> None:
        if (
            params.get("threadId") != invocation.thread_id
            or params.get("turnId") != invocation.turn_id
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")

    async def _notify(self, method: str) -> None:
        await self._write({"method": method})

    async def _write(self, message: dict[str, object]) -> None:
        process = self._require_process()
        encoded = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
        try:
            process.stdin.write(encoded.encode("utf-8") + b"\n")
            await process.stdin.drain()
        except OSError:
            raise _ChildLost("Codex app-server child ended.") from None

    async def _read_message(self) -> dict[str, object]:
        try:
            line = await self._require_process().stdout.readline()
        except OSError:
            raise _ChildLost("Codex app-server child ended.") from None
        except ValueError:
            raise _ProtocolViolation("Codex app-server protocol failure.") from None
        if not line:
            raise _ChildLost("Codex app-server child ended.")
        if len(line) > self._max_frame_bytes or not line.endswith(b"\n"):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        try:
            raw = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _ProtocolViolation("Codex app-server protocol failure.") from None
        if not isinstance(raw, dict):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        has_method = "method" in raw
        if has_method:
            if "result" in raw or "error" in raw:
                raise _ProtocolViolation("Codex app-server protocol failure.")
            if "id" in raw and (
                not isinstance(raw["id"], int) or isinstance(raw["id"], bool)
            ):
                raise _ProtocolViolation("Codex app-server protocol failure.")
        elif (
            not isinstance(raw.get("id"), int)
            or isinstance(raw.get("id"), bool)
            or ("result" in raw) == ("error" in raw)
            or "params" in raw
        ):
            raise _ProtocolViolation("Codex app-server protocol failure.")
        return cast(dict[str, object], raw)

    async def _discard_stderr(self, process: AppServerProcess) -> None:
        while await process.stderr.readline():
            pass

    def _prepare_owned_directories(self) -> None:
        self._sandbox_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._sandbox_root, 0o700)
        cwd = self._sandbox_root / "cwd"
        cwd.mkdir(mode=0o700, exist_ok=True)
        os.chmod(cwd, 0o700)
        media_root = self._sandbox_root / "media"
        media_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(media_root, 0o700)
        if not self._retry_pending_media_cleanup():
            raise RuntimeError("Codex app-server media cleanup failed.")

    def _write_owned_media(self, data: bytes, media_type: str | None) -> Path:
        suffixes = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        suffix = suffixes.get(media_type or "")
        if suffix is None or not data:
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model request is invalid.",
                retryable=False,
            )
        path = self._sandbox_root / "media" / f"{uuid4()}{suffix}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError:
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model request could not be prepared safely.",
                retryable=False,
            ) from None
        self._pending_media_cleanup.add(path)
        write_failed = False
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
        except OSError:
            write_failed = True
        finally:
            try:
                os.close(descriptor)
            except OSError:
                write_failed = True
        if write_failed:
            self._remove_owned_media(path)
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model request could not be prepared safely.",
                retryable=False,
            ) from None
        return path

    def _remove_owned_media(self, path: Path) -> bool:
        if path not in self._pending_media_cleanup:
            return True
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
        self._pending_media_cleanup.discard(path)
        return True

    def _retry_pending_media_cleanup(self) -> bool:
        cleaned = True
        for path in tuple(self._pending_media_cleanup):
            cleaned = self._remove_owned_media(path) and cleaned
        return cleaned

    def _failed_inspection(
        self,
        error: ProviderErrorKind,
    ) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.CODEX,
            models={
                model: DiscoveredModel(model=model, available=False, error=error)
                for model in self._configured_models
            },
        )

    def _require_process(self) -> AppServerProcess:
        if self._process is None:
            raise RuntimeError("Codex app-server child is unavailable.")
        return self._process

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError("Codex app-server client is closed.")

    async def _restart_child(self) -> None:
        task = self._restart_task
        if task is None:
            process = self._process
            stderr_task = self._stderr_task
            self._inspection = None
            if process is None:
                self._generation_poisoned = False
                return
            self._generation_poisoned = True
            task = asyncio.create_task(self._reap_generation(process, stderr_task))
            self._restart_task = task

        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancellation = error
                continue

        failure: Exception | None = None
        try:
            task.result()
        except Exception as error:
            failure = error
        finally:
            if self._restart_task is task:
                self._restart_task = None
        if cancellation is not None:
            raise cancellation
        if failure is not None:
            raise failure

    async def _reap_generation(
        self,
        process: AppServerProcess,
        stderr_task: asyncio.Task[None] | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cleanup_timeout_seconds

        def remaining() -> float:
            return max(deadline - loop.time(), 0.000_001)

        with suppress(OSError, RuntimeError):
            process.stdin.close()
        reaped = process.returncode is not None
        if process.returncode is None:
            with suppress(OSError, ProcessLookupError, RuntimeError):
                process.terminate()
            terminate_grace = max(remaining() / 2, 0.000_001)
            try:
                async with asyncio.timeout(terminate_grace):
                    await process.wait()
                reaped = True
            except (TimeoutError, OSError, RuntimeError):
                with suppress(OSError, ProcessLookupError, RuntimeError):
                    process.kill()
                try:
                    async with asyncio.timeout(remaining()):
                        await process.wait()
                    reaped = True
                except (TimeoutError, OSError, RuntimeError):
                    reaped = False
        stderr_settled = stderr_task is None
        if stderr_task is not None:
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                async with asyncio.timeout(remaining()):
                    await stderr_task
                stderr_settled = True
            except asyncio.CancelledError:
                stderr_settled = True
            except Exception:
                stderr_settled = stderr_task.done()
        if reaped and stderr_settled:
            if self._process is process:
                self._process = None
            if self._stderr_task is stderr_task:
                self._stderr_task = None
            self._generation_poisoned = False
            return
        raise RuntimeError("Codex app-server child teardown did not settle.")

    async def aclose(self) -> None:
        """Terminate and reap the child once; later calls are no-ops."""
        async with self._close_lock:
            if (
                self._closed
                and self._process is None
                and self._restart_task is None
                and not self._pending_media_cleanup
            ):
                return
            self._closed = True
            active_task = self._active_task
            if active_task is not None and active_task is not asyncio.current_task():
                active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)
            async with self._lock:
                await self._restart_child()
                if not self._retry_pending_media_cleanup():
                    raise _normalized(
                        ProviderErrorKind.PROTOCOL_ERROR,
                        "Sensitive model media cleanup failed.",
                        retryable=False,
                    ) from None


def _normalized(
    kind: ProviderErrorKind,
    message: str,
    *,
    retryable: bool,
) -> ProviderInvocationError:
    return ProviderInvocationError(
        ProviderError(
            kind=kind,
            message=message,
            retryable=retryable,
            provider=ProviderName.CODEX,
        )
    )
