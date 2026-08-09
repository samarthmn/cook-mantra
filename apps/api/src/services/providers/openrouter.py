"""OpenRouter structured text and vision transport."""

import asyncio
import json
from base64 import b64encode
from collections.abc import Sequence

import httpx
from pydantic import BaseModel, ValidationError

from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ModelMessage,
    ModelResult,
    ModelUsage,
    ProviderDiscoveryResult,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
    ReasoningEffort,
    TextTuning,
)
from services.provider_errors import ProviderInvocationError

_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}


class OpenRouterTextVisionAdapter:
    """Call one exact OpenRouter model without provider fallback."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        role: AgentRole,
        tuning: TextTuning,
        supported_parameters: set[str] | frozenset[str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._role = role
        self._tuning = tuning
        self._parameters = frozenset(supported_parameters)
        self._transport = transport

    async def invoke[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
    ) -> ModelResult[OutputT]:
        """Request strict JSON schema output and validate it again locally."""
        self._require_supported_tuning()
        payload: dict[str, object] = {
            "model": self._model,
            "stream": False,
            "messages": [_message_payload(message) for message in messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "provider": {
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "temperature": self._tuning.temperature,
        }
        if self._tuning.max_output_tokens is not None:
            payload["max_tokens"] = self._tuning.max_output_tokens
        if self._tuning.reasoning_effort is ReasoningEffort.NONE:
            payload["reasoning"] = {"enabled": False}
        elif self._tuning.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._tuning.reasoning_effort.value}
        transport_failure: ProviderInvocationError | None = None
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._tuning.timeout_seconds,
                trust_env=self._role is not AgentRole.INGREDIENT_EXTRACTOR,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self._endpoint}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            transport_failure = _normalized(
                ProviderErrorKind.TIMED_OUT,
                "The model operation timed out.",
                retryable=True,
                role=self._role,
            )
        except httpx.RequestError:
            transport_failure = _normalized(
                ProviderErrorKind.UNAVAILABLE,
                "The selected model provider is unavailable.",
                retryable=True,
                role=self._role,
            )
        if transport_failure is not None:
            raise transport_failure

        body = (
            _error_body(response)
            if response.status_code >= 400
            else _safe_json(response)
        )
        canonical_kind = _canonical_error_kind(body)
        if canonical_kind is not None or response.status_code >= 400:
            raise _http_failure(
                response.status_code,
                canonical_kind=canonical_kind,
                role=self._role,
            )
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _protocol_error(self._role)
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise _protocol_error(self._role)
        validation_failure: ProviderInvocationError | None = None
        try:
            output = schema.model_validate_json(content)
        except (json.JSONDecodeError, ValidationError, ValueError):
            validation_failure = _normalized(
                ProviderErrorKind.INVALID_OUTPUT,
                "The model returned invalid structured output.",
                retryable=True,
                role=self._role,
            )
        if validation_failure is not None:
            raise validation_failure
        return ModelResult(output=output, usage=_usage(body, self._role))

    def _require_supported_tuning(self) -> None:
        required = {"structured_outputs", "temperature"}
        if self._tuning.max_output_tokens is not None:
            required.add("max_tokens")
        if self._tuning.reasoning_effort is not None:
            required.add("reasoning")
        if not required.issubset(self._parameters):
            raise _normalized(
                ProviderErrorKind.CAPABILITY_MISSING,
                "The selected model lacks a required capability.",
                retryable=False,
                role=self._role,
            )


class OpenRouterModelDiscovery:
    """Discover only exact configured OpenRouter model records."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        configured_models: set[str] | frozenset[str],
        timeout_seconds: float,
        media_bearing: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._models = tuple(sorted(configured_models))
        self._timeout_seconds = timeout_seconds
        self._media_bearing = media_bearing
        self._transport = transport
        self._inspection: ProviderDiscoveryResult | None = None
        self._lock = asyncio.Lock()

    async def inspect(self) -> ProviderDiscoveryResult:
        """Return a detached view of one process-lifetime snapshot."""
        if self._inspection is None:
            async with self._lock:
                if self._inspection is None:
                    self._inspection = await self._discover()
        return self._inspection.model_copy(deep=True)

    async def _discover(self) -> ProviderDiscoveryResult:
        try:
            try:
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    trust_env=not self._media_bearing,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(
                        f"{self._endpoint}/models",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
            except httpx.TimeoutException:
                raise _normalized(
                    ProviderErrorKind.TIMED_OUT,
                    "The model operation timed out.",
                    retryable=True,
                ) from None
            except httpx.RequestError:
                raise _normalized(
                    ProviderErrorKind.UNAVAILABLE,
                    "The selected model provider is unavailable.",
                    retryable=True,
                ) from None
            body = (
                _error_body(response)
                if response.status_code >= 400
                else _safe_json(response)
            )
            canonical_kind = _canonical_error_kind(body)
            if canonical_kind is not None or response.status_code >= 400:
                role = AgentRole.MASTER_CHEF
                raise _http_failure(
                    response.status_code,
                    canonical_kind=canonical_kind,
                    role=role,
                )
            records = _model_records(body)
            discovered: dict[str, DiscoveredModel] = {}
            for model in self._models:
                record = records.get(model)
                if record is None:
                    discovered[model] = DiscoveredModel(
                        model=model,
                        available=False,
                        error=ProviderErrorKind.CAPABILITY_MISSING,
                    )
                else:
                    discovered[model] = _discovered_model(model, record)
        except ProviderInvocationError as error:
            discovered = {
                model: DiscoveredModel(
                    model=model,
                    available=False,
                    error=error.error.kind,
                )
                for model in self._models
            }
        available = tuple(
            model for model in self._models if discovered[model].available
        )
        return ProviderDiscoveryResult(
            provider=ProviderName.OPENROUTER,
            models=discovered,
            available_models=available,
        )


def _model_records(body: dict[str, object]) -> dict[str, dict[str, object]]:
    data = body.get("data")
    if not isinstance(data, list):
        raise _protocol_error()
    records: dict[str, dict[str, object]] = {}
    for record in data:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise _protocol_error()
        records[record["id"]] = record
    return records


def _discovered_model(model: str, record: dict[str, object]) -> DiscoveredModel:
    architecture = record.get("architecture")
    parameters = record.get("supported_parameters")
    context_window = record.get("context_length")
    if (
        not isinstance(architecture, dict)
        or not isinstance(parameters, list)
        or any(not isinstance(parameter, str) for parameter in parameters)
        or not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window < 1
    ):
        return DiscoveredModel(
            model=model,
            available=False,
            error=ProviderErrorKind.PROTOCOL_ERROR,
        )
    inputs = architecture.get("input_modalities")
    outputs = architecture.get("output_modalities", record.get("output_modalities"))
    if (
        not isinstance(inputs, list)
        or any(not isinstance(modality, str) for modality in inputs)
        or not isinstance(outputs, list)
        or any(not isinstance(modality, str) for modality in outputs)
    ):
        return DiscoveredModel(
            model=model,
            available=False,
            error=ProviderErrorKind.PROTOCOL_ERROR,
        )
    capabilities: set[Capability] = set()
    if "text" in inputs and "text" in outputs:
        capabilities.add(Capability.TEXT)
    if "image" in inputs and "text" in outputs:
        capabilities.add(Capability.VISION)
    if "structured_outputs" in parameters and "text" in outputs:
        capabilities.add(Capability.STRUCTURED_OUTPUT)
    return DiscoveredModel(
        model=model,
        available=True,
        capabilities=tuple(sorted(capabilities)),
        supported_parameters=tuple(sorted(set(parameters))),
        context_window=context_window,
    )


def _message_payload(message: ModelMessage) -> dict[str, object]:
    if message.image is None:
        return {"role": message.role, "content": message.content}
    if message.media_type not in _MEDIA_TYPES:
        raise _normalized(
            ProviderErrorKind.CAPABILITY_MISSING,
            "The supplied image type is not supported.",
            retryable=False,
        )
    encoded = b64encode(message.image).decode("ascii")
    return {
        "role": message.role,
        "content": [
            {"type": "text", "text": message.content},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{message.media_type};base64,{encoded}",
                },
            },
        ],
    }


def _safe_json(response: httpx.Response) -> dict[str, object]:
    failure: ProviderInvocationError | None = None
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        failure = _protocol_error()
    if failure is not None:
        raise failure
    if not isinstance(body, dict):
        raise _protocol_error()
    return body


def _error_body(response: httpx.Response) -> dict[str, object]:
    """Parse canonical error metadata when present; status remains the fallback."""
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _canonical_error_kind(body: dict[str, object]) -> ProviderErrorKind | None:
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    metadata = error.get("metadata")
    error_type = metadata.get("error_type") if isinstance(metadata, dict) else None
    if not isinstance(error_type, str):
        return None
    normalized = error_type.casefold().replace("-", "_")
    if "auth" in normalized:
        return ProviderErrorKind.AUTHENTICATION_FAILED
    if "payment" in normalized or "credit" in normalized:
        return ProviderErrorKind.PAYMENT_REQUIRED
    if "rate" in normalized:
        return ProviderErrorKind.RATE_LIMITED
    if "timeout" in normalized or "timed_out" in normalized:
        return ProviderErrorKind.TIMED_OUT
    if any(part in normalized for part in ("unavailable", "overload", "provider")):
        return ProviderErrorKind.UNAVAILABLE
    return None


def _http_failure(
    status_code: int,
    *,
    canonical_kind: ProviderErrorKind | None,
    role: AgentRole,
) -> ProviderInvocationError:
    kind = canonical_kind
    if kind is None:
        kind = {
            401: ProviderErrorKind.AUTHENTICATION_FAILED,
            402: ProviderErrorKind.PAYMENT_REQUIRED,
            429: ProviderErrorKind.RATE_LIMITED,
            502: ProviderErrorKind.UNAVAILABLE,
            503: ProviderErrorKind.UNAVAILABLE,
        }.get(status_code, ProviderErrorKind.PROTOCOL_ERROR)
    retryable = kind in {
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.UNAVAILABLE,
        ProviderErrorKind.TIMED_OUT,
    }
    messages = {
        ProviderErrorKind.AUTHENTICATION_FAILED: (
            "Model provider authentication failed."
        ),
        ProviderErrorKind.PAYMENT_REQUIRED: "The model provider requires payment.",
        ProviderErrorKind.RATE_LIMITED: "The selected model provider is rate limited.",
        ProviderErrorKind.UNAVAILABLE: "The selected model provider is unavailable.",
        ProviderErrorKind.TIMED_OUT: "The model operation timed out.",
        ProviderErrorKind.PROTOCOL_ERROR: (
            "The model provider returned an invalid response."
        ),
    }
    return _normalized(kind, messages[kind], retryable=retryable, role=role)


def _usage(body: dict[str, object], role: AgentRole) -> ModelUsage | None:
    usage = body.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise _protocol_error(role)
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if (
        not isinstance(prompt, int)
        or isinstance(prompt, bool)
        or prompt < 0
        or not isinstance(completion, int)
        or isinstance(completion, bool)
        or completion < 0
    ):
        raise _protocol_error(role)
    return ModelUsage(input_tokens=prompt, output_tokens=completion)


def _protocol_error(role: AgentRole | None = None) -> ProviderInvocationError:
    return _normalized(
        ProviderErrorKind.PROTOCOL_ERROR,
        "The model provider returned an invalid response.",
        retryable=True,
        role=role,
    )


def _normalized(
    kind: ProviderErrorKind,
    message: str,
    *,
    retryable: bool,
    role: AgentRole | None = None,
) -> ProviderInvocationError:
    return ProviderInvocationError(
        ProviderError(
            kind=kind,
            message=message,
            retryable=retryable,
            provider=ProviderName.OPENROUTER,
            role=role,
        )
    )
