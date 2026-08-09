"""Provider-neutral runtime readiness and route admission."""

import asyncio
import ipaddress
import os
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from core.config import LLM_TIMEOUT_SECONDS
from core.errors import AppError, ErrorCode
from core.logging import current_log_context
from core.runtime_config import (
    RoleConfiguration,
    RuntimeConfiguration,
    RuntimeConfigurationSnapshot,
)
from domain.model_runtime import (
    IMAGE_PROVIDER_NAMES,
    AgentRole,
    DiscoveredModel,
    ModelMessage,
    ModelResult,
    ModelRuntimeReadiness,
    ModelUsage,
    ModelUsageEvent,
    ProviderDiscoveryResult,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
    ReasoningEffort,
    RoleReadiness,
)
from services.provider_errors import ProviderInvocationError
from services.providers.codex import CodexStructuredAdapter, CodexStructuredInvoker
from services.providers.ollama import OllamaTextVisionAdapter
from services.providers.openrouter import OpenRouterTextVisionAdapter
from services.structured_output import StructuredModel
from services.tracing import invoke_trace_safe_text_model


class ProviderHealth(Protocol):
    """Compatibility boundary for the current Ollama discovery adapter."""

    async def inspect(self) -> dict[str, object]: ...


class ProviderDiscovery(Protocol):
    """Return a safe exact-model discovery snapshot."""

    async def inspect(self) -> ProviderDiscoveryResult: ...


class ModelUsageEventSink(Protocol):
    """Receive content-free usage events at the invocation boundary."""

    async def record(self, event: ModelUsageEvent) -> None: ...


class InProcessModelUsageJournal:
    """Bounded process-local model usage event journal."""

    def __init__(self, max_events: int = 1_000) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int):
            raise TypeError("max_events must be an integer")
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._max_events = max_events
        self._events: deque[ModelUsageEvent] = deque(maxlen=max_events)
        self._lock = asyncio.Lock()

    async def record(self, event: ModelUsageEvent) -> None:
        """Append one detached event without blocking concurrent provider calls."""
        detached = event.model_copy(deep=True)
        async with self._lock:
            self._events.append(detached)

    async def snapshot(self) -> tuple[ModelUsageEvent, ...]:
        """Return detached events in invocation-completion order."""
        async with self._lock:
            return tuple(event.model_copy(deep=True) for event in self._events)


class UnavailableModelDiscovery:
    """Return a cached safe failure for an intentionally unsupported provider."""

    def __init__(
        self,
        provider: ProviderName,
        models: set[str],
        error: ProviderErrorKind,
    ) -> None:
        self._inspection = _failed_discovery(provider, models, error)

    async def inspect(self) -> ProviderDiscoveryResult:
        return self._inspection.model_copy(deep=True)


class StructuredModelFactory(Protocol):
    """Build a structured model for one exact application role and schema."""

    def build[OutputT: BaseModel](
        self,
        role: AgentRole,
        schema: type[OutputT],
    ) -> StructuredModel[OutputT]: ...


class RuntimeStructuredModelFactory:
    """Build structured models from exact role-level runtime selections."""

    def __init__(
        self,
        snapshot: RuntimeConfigurationSnapshot,
        *,
        environ: Mapping[str, str] | None = None,
        transports: Mapping[ProviderName, httpx.AsyncBaseTransport] | None = None,
        discoveries: Mapping[ProviderName, ProviderDiscovery] | None = None,
        usage_journal: ModelUsageEventSink | None = None,
        codex_client: CodexStructuredInvoker | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._environ = environ if environ is not None else os.environ
        self._transports = transports or {}
        self._discoveries = discoveries if discoveries is not None else {}
        self._usage_journal = usage_journal
        self._codex_client = codex_client

    def build[OutputT: BaseModel](
        self,
        role: AgentRole,
        schema: type[OutputT],
    ) -> StructuredModel[OutputT]:
        """Build only the provider/model selected for `role`; never fall back."""
        config = self._snapshot.config
        if config is None:
            return _UnavailableStructuredModel(
                _provider_error(
                    ProviderErrorKind.PROTOCOL_ERROR,
                    "The model runtime configuration is invalid.",
                    retryable=False,
                    role=role,
                )
            )
        selection = config.roles[role]
        tuning = selection.text_tuning
        if tuning is None:
            return _UnavailableStructuredModel(
                _provider_error(
                    ProviderErrorKind.CAPABILITY_MISSING,
                    "The selected model lacks a required capability.",
                    retryable=False,
                    provider=selection.provider,
                    role=role,
                )
            )
        definition = config.providers[selection.provider]
        if not media_endpoint_supported(config, role, selection):
            return _UnavailableStructuredModel(
                _provider_error(
                    ProviderErrorKind.CAPABILITY_MISSING,
                    "The selected model lacks a required capability.",
                    retryable=False,
                    provider=selection.provider,
                    role=role,
                )
            )
        transport = self._transports.get(selection.provider)
        if selection.provider is ProviderName.CODEX:
            if self._codex_client is None:
                return _UnavailableStructuredModel(
                    _provider_error(
                        ProviderErrorKind.CAPABILITY_MISSING,
                        "The selected provider is not available for this role.",
                        retryable=False,
                        provider=ProviderName.CODEX,
                        role=role,
                    )
                )
            discovery = self._discoveries.get(ProviderName.CODEX)
            if discovery is None:
                return _UnavailableStructuredModel(
                    _provider_error(
                        ProviderErrorKind.UNAVAILABLE,
                        "The selected model provider is unavailable.",
                        retryable=True,
                        provider=ProviderName.CODEX,
                        role=role,
                    )
                )

            codex_adapter = CodexStructuredAdapter(
                self._codex_client,
                model=selection.model,
                timeout_seconds=(tuning.timeout_seconds or LLM_TIMEOUT_SECONDS),
            )

            def build_codex(_: DiscoveredModel) -> _StructuredAdapter[OutputT]:
                return cast(_StructuredAdapter[OutputT], codex_adapter)

            return _CatalogStructuredModel(
                discovery=discovery,
                selection=selection,
                role=role,
                schema=schema,
                adapter_factory=build_codex,
                composed_adapter=cast(
                    _ComposedStructuredAdapter[OutputT],
                    codex_adapter,
                ),
                usage_journal=self._usage_journal,
            )
        discovery = self._discoveries.get(selection.provider)
        if discovery is None:
            return _UnavailableStructuredModel(
                _provider_error(
                    ProviderErrorKind.UNAVAILABLE,
                    "The selected model provider is unavailable.",
                    retryable=True,
                    provider=selection.provider,
                    role=role,
                )
            )
        if selection.provider is ProviderName.OLLAMA:
            assert definition.endpoint is not None

            def build_ollama(_: DiscoveredModel) -> _StructuredAdapter[OutputT]:
                return cast(
                    _StructuredAdapter[OutputT],
                    OllamaTextVisionAdapter(
                        endpoint=str(definition.endpoint),
                        model=selection.model,
                        role=role,
                        tuning=tuning,
                        transport=transport,
                    ),
                )

            return _CatalogStructuredModel(
                discovery=discovery,
                selection=selection,
                role=role,
                schema=schema,
                adapter_factory=build_ollama,
                usage_journal=self._usage_journal,
            )
        if selection.provider is ProviderName.OPENROUTER:
            assert definition.endpoint is not None
            api_key = (
                self._environ.get(definition.api_key_env, "")
                if definition.api_key_env is not None
                else ""
            )
            if not api_key:
                return _UnavailableStructuredModel(
                    _provider_error(
                        ProviderErrorKind.AUTHENTICATION_FAILED,
                        "Model provider authentication failed.",
                        retryable=False,
                        provider=ProviderName.OPENROUTER,
                        role=role,
                    )
                )

            def build_openrouter(
                discovered: DiscoveredModel,
            ) -> _StructuredAdapter[OutputT]:
                return cast(
                    _StructuredAdapter[OutputT],
                    OpenRouterTextVisionAdapter(
                        endpoint=str(definition.endpoint),
                        api_key=api_key,
                        model=selection.model,
                        role=role,
                        tuning=tuning,
                        supported_parameters=frozenset(discovered.supported_parameters),
                        transport=transport,
                    ),
                )

            return _CatalogStructuredModel(
                discovery=discovery,
                selection=selection,
                role=role,
                schema=schema,
                adapter_factory=build_openrouter,
                usage_journal=self._usage_journal,
            )
        raise AssertionError("unsupported configured provider")


class _StructuredAdapter[OutputT: BaseModel](Protocol):
    async def invoke(
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
    ) -> ModelResult[OutputT]: ...


class _ComposedStructuredAdapter[OutputT: BaseModel](Protocol):
    async def invoke_admitted(
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        admit: Callable[[ProviderDiscoveryResult], None],
    ) -> ModelResult[OutputT]: ...


class _CatalogStructuredModel[OutputT: BaseModel]:
    def __init__(
        self,
        *,
        discovery: ProviderDiscovery,
        selection: RoleConfiguration,
        role: AgentRole,
        schema: type[OutputT],
        adapter_factory: Callable[[DiscoveredModel], _StructuredAdapter[OutputT]],
        usage_journal: ModelUsageEventSink | None,
        composed_adapter: _ComposedStructuredAdapter[OutputT] | None = None,
    ) -> None:
        self._discovery = discovery
        self._selection = selection
        self._role = role
        self._schema = schema
        self._adapter_factory = adapter_factory
        self._composed_adapter = composed_adapter
        self._usage_journal = usage_journal

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> OutputT:
        if not isinstance(messages, Sequence) or any(
            not isinstance(message, ModelMessage) for message in messages
        ):
            raise _provider_error(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model request is invalid.",
                retryable=False,
            )

        async def invoke_adapter() -> ModelResult[OutputT]:
            if self._composed_adapter is not None:
                return await self._composed_adapter.invoke_admitted(
                    messages,
                    self._schema,
                    admit=self._admit,
                )
            inspection = await self._discovery.inspect()
            discovered = self._admit(inspection)
            adapter = self._adapter_factory(discovered)
            return await adapter.invoke(messages, self._schema)

        if config:
            result = await invoke_trace_safe_text_model(
                role=self._role,
                provider=self._selection.provider,
                model=self._selection.model,
                operation=invoke_adapter,
            )
        else:
            result = await invoke_adapter()
        if result.usage is not None and self._usage_journal is not None:
            correlation = _usage_correlation(config)
            await self._usage_journal.record(
                ModelUsageEvent(
                    role=self._role,
                    provider=self._selection.provider,
                    model=self._selection.model,
                    usage=ModelUsage(
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                    ),
                    **correlation,
                )
            )
        return result.output

    def _admit(self, inspection: ProviderDiscoveryResult) -> DiscoveredModel:
        discovered = inspection.models.get(self._selection.model)
        if (
            discovered is None
            or not discovered.available
            or discovered.error is not None
        ):
            kind = (
                discovered.error
                if discovered is not None and discovered.error is not None
                else ProviderErrorKind.CAPABILITY_MISSING
            )
            raise _provider_error(
                kind,
                "The selected model lacks a required capability.",
                retryable=kind
                in {
                    ProviderErrorKind.RATE_LIMITED,
                    ProviderErrorKind.TIMED_OUT,
                    ProviderErrorKind.UNAVAILABLE,
                },
                provider=self._selection.provider,
                role=self._role,
            )
        configured_capabilities = self._selection.capabilities or frozenset()
        available = configured_capabilities.intersection(discovered.capabilities)
        if not self._role.required_capabilities.issubset(
            available
        ) or not _tuning_supported(self._selection, discovered):
            raise _provider_error(
                ProviderErrorKind.CAPABILITY_MISSING,
                "The selected model lacks a required capability.",
                retryable=False,
                provider=self._selection.provider,
                role=self._role,
            )
        return discovered


class _UnavailableStructuredModel[OutputT]:
    def __init__(self, error: ProviderInvocationError) -> None:
        self._error = error

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> OutputT:
        raise self._error


def _usage_correlation(config: dict[str, object] | None) -> dict[str, object]:
    metadata = config.get("metadata") if isinstance(config, dict) else None
    safe_metadata = metadata if isinstance(metadata, Mapping) else {}
    log_correlation = current_log_context()
    correlation: dict[str, object] = {}
    for field in ("session_id", "job_id"):
        value = safe_metadata.get(field, log_correlation.get(field))
        if isinstance(value, str) and 0 < len(value) <= 128:
            correlation[field] = value
    batch_number = safe_metadata.get("batch_number")
    if (
        isinstance(batch_number, int)
        and not isinstance(batch_number, bool)
        and batch_number >= 1
    ):
        correlation["batch_number"] = batch_number
    return correlation


def _provider_error(
    kind: ProviderErrorKind,
    message: str,
    *,
    retryable: bool,
    provider: ProviderName | None = None,
    role: AgentRole | None = None,
) -> ProviderInvocationError:
    return ProviderInvocationError(
        ProviderError(
            kind=kind,
            message=message,
            retryable=retryable,
            provider=provider,
            role=role,
        )
    )


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    """Authoritative roles plus deprecated provider-specific inventory."""

    model_runtime: ModelRuntimeReadiness
    ollama: dict[str, object] | None


def _copy_runtime_inspection(inspection: RuntimeInspection) -> RuntimeInspection:
    return RuntimeInspection(
        model_runtime=inspection.model_runtime.model_copy(deep=True),
        ollama=deepcopy(inspection.ollama),
    )


class ModelRuntimeReadinessService:
    """Discover configured roles once and retain the result for process lifetime."""

    def __init__(
        self,
        snapshot: RuntimeConfigurationSnapshot,
        ollama_health: ProviderHealth | None = None,
        *,
        discoveries: Mapping[ProviderName, ProviderDiscovery] | None = None,
        image_runtimes: Mapping[ProviderName, ProviderDiscovery] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._ollama_health = ollama_health
        self._discoveries = discoveries or {}
        self._image_runtimes = image_runtimes or {}
        self._inspection: RuntimeInspection | None = None
        self._lock = asyncio.Lock()

    async def inspect(self) -> RuntimeInspection:
        """Return a detached view of one process-cached discovery result."""
        if self._inspection is None:
            async with self._lock:
                if self._inspection is None:
                    self._inspection = await self._discover()
        return _copy_runtime_inspection(self._inspection)

    async def require(self, role: AgentRole) -> RoleReadiness:
        """Reject a dependent route before it mutates session or job state."""
        if self._snapshot.config is None:
            raise self._configuration_error()
        inspection = await self.inspect()
        readiness = inspection.model_runtime.roles[role]
        if readiness.ready:
            return readiness
        raise _readiness_app_error(role, readiness.error)

    async def _discover(self) -> RuntimeInspection:
        config = self._snapshot.config
        if config is None:
            roles = {
                role: RoleReadiness(
                    role=role,
                    provider=None,
                    model=None,
                    enabled=True,
                    ready=False,
                    required_capabilities=tuple(sorted(role.required_capabilities)),
                    available_capabilities=(),
                    error=ProviderErrorKind.PROTOCOL_ERROR,
                )
                for role in AgentRole
            }
            return RuntimeInspection(
                model_runtime=ModelRuntimeReadiness(ready=False, roles=roles),
                ollama=None,
            )

        configured_by_provider: dict[ProviderName, set[str]] = {}
        for role, selection in config.roles.items():
            if (
                not selection.enabled
                or role is AgentRole.IMAGE_GENERATOR
                or not media_endpoint_supported(config, role, selection)
            ):
                continue
            configured_by_provider.setdefault(selection.provider, set()).add(
                selection.model
            )

        inspections: dict[ProviderName, ProviderDiscoveryResult] = {}
        raw_ollama: dict[str, object] | None = None
        ollama_selected = any(
            role is not AgentRole.IMAGE_GENERATOR
            and selection.enabled
            and selection.provider is ProviderName.OLLAMA
            and media_endpoint_supported(config, role, selection)
            for role, selection in config.roles.items()
        )
        if ollama_selected and self._ollama_health is not None:
            try:
                raw_ollama = await self._ollama_health.inspect()
            except AppError:
                raw_ollama = None
        for provider, configured_models in configured_by_provider.items():
            discovery = self._discoveries.get(provider)
            if discovery is not None:
                inspections[provider] = await discovery.inspect()
                continue
            if provider is ProviderName.OLLAMA and self._ollama_health is not None:
                if raw_ollama is None:
                    inspections[provider] = _failed_discovery(
                        provider,
                        configured_models,
                        ProviderErrorKind.UNAVAILABLE,
                    )
                else:
                    inspections[provider] = _legacy_ollama_discovery(
                        raw_ollama,
                        configured_models,
                        config,
                    )
                continue
            inspections[provider] = _failed_discovery(
                provider,
                configured_models,
                (
                    ProviderErrorKind.CAPABILITY_MISSING
                    if provider is ProviderName.CODEX
                    else ProviderErrorKind.UNAVAILABLE
                ),
            )

        if raw_ollama is None and ProviderName.OLLAMA in inspections:
            ollama_result = inspections[ProviderName.OLLAMA]
            configured_ollama = configured_by_provider.get(ProviderName.OLLAMA, set())
            raw_ollama = {
                "reachable": any(
                    model.available for model in ollama_result.models.values()
                ),
                "available_models": list(ollama_result.available_models),
                "missing": sorted(
                    configured_ollama.difference(ollama_result.available_models)
                ),
            }
        image_selection = config.roles[AgentRole.IMAGE_GENERATOR]
        image_inspection: ProviderDiscoveryResult | None = None
        if image_selection.enabled and image_selection.provider in IMAGE_PROVIDER_NAMES:
            image_runtime = self._image_runtimes.get(image_selection.provider)
            if image_runtime is not None:
                image_inspection = await image_runtime.inspect()
        roles: dict[AgentRole, RoleReadiness] = {}
        for role, selection in config.roles.items():
            configured_capabilities = selection.capabilities or frozenset()
            enabled = selection.enabled
            error: ProviderErrorKind | None = None
            if not enabled:
                ready = True
                available_capabilities = configured_capabilities
            elif role is AgentRole.IMAGE_GENERATOR:
                discovered = (
                    image_inspection.models.get(selection.model)
                    if image_inspection is not None
                    else None
                )
                if (
                    image_inspection is not None
                    and image_inspection.provider is not selection.provider
                ):
                    ready = False
                    available_capabilities = frozenset()
                    error = ProviderErrorKind.PROTOCOL_ERROR
                elif discovered is None or discovered.model != selection.model:
                    ready = False
                    available_capabilities = frozenset()
                    error = ProviderErrorKind.CAPABILITY_MISSING
                else:
                    available_capabilities = configured_capabilities.intersection(
                        discovered.capabilities
                    )
                    error = discovered.error
                    ready = discovered.available and error is None
                    if ready and not role.required_capabilities.issubset(
                        available_capabilities
                    ):
                        ready = False
                        error = ProviderErrorKind.CAPABILITY_MISSING
            else:
                if not media_endpoint_supported(config, role, selection):
                    ready = False
                    available_capabilities = frozenset()
                    error = ProviderErrorKind.CAPABILITY_MISSING
                else:
                    inspection = inspections[selection.provider]
                    discovered = inspection.models.get(selection.model)
                    if discovered is None:
                        ready = False
                        available_capabilities = frozenset()
                        error = ProviderErrorKind.CAPABILITY_MISSING
                    else:
                        available_capabilities = configured_capabilities.intersection(
                            discovered.capabilities
                        )
                        error = discovered.error
                        ready = discovered.available and error is None
                        if ready and not role.required_capabilities.issubset(
                            available_capabilities
                        ):
                            ready = False
                            error = ProviderErrorKind.CAPABILITY_MISSING
                        if ready and not _tuning_supported(selection, discovered):
                            ready = False
                            error = ProviderErrorKind.CAPABILITY_MISSING
            if enabled and not ready and error is None:
                ready = False
                error = ProviderErrorKind.CAPABILITY_MISSING
            roles[role] = RoleReadiness(
                role=role,
                provider=selection.provider,
                model=selection.model,
                enabled=enabled,
                ready=ready,
                required_capabilities=tuple(sorted(role.required_capabilities)),
                available_capabilities=tuple(sorted(available_capabilities)),
                error=error,
            )
        runtime_ready = all(
            status.ready for role, status in roles.items() if role.required
        )
        return RuntimeInspection(
            model_runtime=ModelRuntimeReadiness(ready=runtime_ready, roles=roles),
            ollama=raw_ollama,
        )

    def _configuration_error(self) -> AppError:
        return AppError(
            code=ErrorCode.MODEL_CONFIGURATION_INVALID,
            message="The model runtime configuration is invalid.",
            status_code=503,
            retryable=False,
            details={"diagnostics": list(self._snapshot.diagnostics)},
        )

    def readiness_error(self, readiness: ModelRuntimeReadiness) -> AppError:
        """Map the first unavailable required role to a generic public error."""
        if self._snapshot.config is None:
            return self._configuration_error()
        for role in AgentRole:
            status = readiness.roles[role]
            if role.required and not status.ready:
                if status.error is ProviderErrorKind.CAPABILITY_MISSING:
                    error = _readiness_app_error(role, status.error)
                    error.message = "A selected model lacks a required capability."
                    return error
                error = _readiness_app_error(role, status.error)
                if status.error is ProviderErrorKind.UNAVAILABLE:
                    error.message = "A selected model provider is unavailable."
                return error
        raise RuntimeError("readiness_error called for a ready runtime")


def media_endpoint_supported(
    config: RuntimeConfiguration,
    role: AgentRole,
    selection: RoleConfiguration,
) -> bool:
    """Fail closed before ingredient bytes can leave an approved destination."""
    if role is not AgentRole.INGREDIENT_EXTRACTOR:
        return True
    if selection.provider is ProviderName.CODEX:
        return True
    endpoint = config.providers[selection.provider].endpoint
    if endpoint is None:
        return False
    endpoint_value = str(endpoint)
    if selection.provider is ProviderName.OPENROUTER:
        return endpoint_value in {
            "https://openrouter.ai/api/v1",
            "https://openrouter.ai/api/v1/",
        }
    host = urlsplit(endpoint_value).hostname
    if host == "localhost":
        return True
    if host is None:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _legacy_ollama_discovery(
    raw: dict[str, object],
    configured_models: set[str],
    config: object,
) -> ProviderDiscoveryResult:
    from core.runtime_config import RuntimeConfiguration
    from domain.model_runtime import DiscoveredModel

    runtime_config = cast(RuntimeConfiguration, config)
    available = {
        model for model in (raw.get("available_models") or []) if isinstance(model, str)
    }
    models = {}
    for model in configured_models:
        capabilities = set()
        supported_parameters = set()
        for selection in runtime_config.roles.values():
            if (
                selection.provider is ProviderName.OLLAMA
                and selection.model == model
                and selection.capabilities is not None
            ):
                capabilities.update(selection.capabilities)
                tuning = selection.text_tuning
                if tuning is not None and tuning.reasoning_effort in {
                    ReasoningEffort.LOW,
                    ReasoningEffort.MEDIUM,
                    ReasoningEffort.HIGH,
                }:
                    supported_parameters.add("reasoning")
        models[model] = DiscoveredModel(
            model=model,
            available=model in available,
            capabilities=tuple(sorted(capabilities)) if model in available else (),
            supported_parameters=(
                tuple(sorted(supported_parameters)) if model in available else ()
            ),
            error=(
                None if model in available else ProviderErrorKind.CAPABILITY_MISSING
            ),
        )
    return ProviderDiscoveryResult(
        provider=ProviderName.OLLAMA,
        models=models,
        available_models=tuple(sorted(available.intersection(configured_models))),
    )


def _failed_discovery(
    provider: ProviderName,
    models: set[str],
    error: ProviderErrorKind,
) -> ProviderDiscoveryResult:
    from domain.model_runtime import DiscoveredModel

    return ProviderDiscoveryResult(
        provider=provider,
        models={
            model: DiscoveredModel(model=model, available=False, error=error)
            for model in models
        },
    )


def _tuning_supported(selection: object, discovered: object) -> bool:
    from core.runtime_config import RoleConfiguration
    from domain.model_runtime import DiscoveredModel

    role_selection = cast(RoleConfiguration, selection)
    model = cast(DiscoveredModel, discovered)
    tuning = role_selection.text_tuning
    if tuning is None:
        return False
    if role_selection.provider is ProviderName.OLLAMA:
        return not (
            tuning.reasoning_effort
            in {ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH}
            and "reasoning" not in model.supported_parameters
        )
    if role_selection.provider is not ProviderName.OPENROUTER:
        return True
    required = {"temperature", "structured_outputs"}
    if tuning.max_output_tokens is not None:
        required.add("max_tokens")
    if tuning.reasoning_effort is not None:
        required.add("reasoning")
    if not required.issubset(model.supported_parameters):
        return False
    return not (
        tuning.context_window is not None
        and (
            model.context_window is None or model.context_window < tuning.context_window
        )
    )


def _readiness_app_error(
    role: AgentRole,
    kind: ProviderErrorKind | None,
) -> AppError:
    mappings: dict[ProviderErrorKind, tuple[ErrorCode, int, bool, str]] = {
        ProviderErrorKind.CAPABILITY_MISSING: (
            ErrorCode.MODEL_CAPABILITY_MISSING,
            503,
            False,
            "The selected model lacks a required capability.",
        ),
        ProviderErrorKind.AUTHENTICATION_FAILED: (
            ErrorCode.PROVIDER_AUTHENTICATION_FAILED,
            401,
            False,
            "Model provider authentication failed.",
        ),
        ProviderErrorKind.PAYMENT_REQUIRED: (
            ErrorCode.PROVIDER_PAYMENT_REQUIRED,
            402,
            False,
            "The model provider requires payment.",
        ),
        ProviderErrorKind.RATE_LIMITED: (
            ErrorCode.PROVIDER_RATE_LIMITED,
            429,
            True,
            "The selected model provider is rate limited.",
        ),
        ProviderErrorKind.PROTOCOL_ERROR: (
            ErrorCode.PROVIDER_PROTOCOL_ERROR,
            502,
            True,
            "The model provider returned an invalid response.",
        ),
        ProviderErrorKind.TIMED_OUT: (
            ErrorCode.OPERATION_TIMED_OUT,
            504,
            True,
            "The model operation timed out.",
        ),
        ProviderErrorKind.UNAVAILABLE: (
            ErrorCode.PROVIDER_UNAVAILABLE,
            503,
            True,
            "The selected model provider is unavailable.",
        ),
        ProviderErrorKind.INVALID_OUTPUT: (
            ErrorCode.MODEL_OUTPUT_INVALID,
            502,
            True,
            "The model returned invalid structured output.",
        ),
    }
    code, status, retryable, message = mappings[kind or ProviderErrorKind.UNAVAILABLE]
    return AppError(
        code=code,
        message=message,
        status_code=status,
        retryable=retryable,
        details={"role": role.value},
    )
