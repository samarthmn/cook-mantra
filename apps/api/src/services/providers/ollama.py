"""Native Ollama structured text/vision transport and legacy normalization."""

import asyncio
import json
from base64 import b64encode
from collections.abc import Sequence

import httpx
from langchain_core.exceptions import OutputParserException
from ollama import ResponseError
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
from services.structured_output import StructuredModel


class OllamaTextVisionAdapter:
    """Native Ollama structured text and vision transport."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        role: AgentRole,
        tuning: TextTuning,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._role = role
        self._tuning = tuning
        self._transport = transport

    async def invoke[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
    ) -> ModelResult[OutputT]:
        """Call native `/api/chat`, then JSON-decode and validate locally."""
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [_message_payload(message) for message in messages],
            "format": schema.model_json_schema(),
            "stream": False,
            "options": _options_payload(self._tuning),
        }
        if self._tuning.reasoning_effort is not None:
            payload["think"] = (
                False
                if self._tuning.reasoning_effort is ReasoningEffort.NONE
                else self._tuning.reasoning_effort.value
            )
        transport_failure: ProviderInvocationError | None = None
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._tuning.timeout_seconds,
                trust_env=self._role is not AgentRole.INGREDIENT_EXTRACTOR,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self._endpoint}/api/chat",
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

        if response.status_code >= 400:
            raise _http_failure(response.status_code, role=self._role)
        parse_failure: ProviderInvocationError | None = None
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            parse_failure = _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
                role=self._role,
            )
        if parse_failure is not None:
            raise parse_failure
        if not isinstance(body, dict):
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
                role=self._role,
            )
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
                role=self._role,
            )
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
        usage = _usage(body, role=self._role)
        return ModelResult(output=output, usage=usage)


class OllamaModelDiscovery:
    """Discover exact configured Ollama models without pulling them."""

    def __init__(
        self,
        *,
        endpoint: str,
        configured_models: set[str] | frozenset[str],
        timeout_seconds: float,
        media_bearing: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._models = tuple(sorted(configured_models))
        self._timeout_seconds = timeout_seconds
        self._media_bearing = media_bearing
        self._transport = transport
        self._inspection: ProviderDiscoveryResult | None = None
        self._lock = asyncio.Lock()

    async def inspect(self) -> ProviderDiscoveryResult:
        """Return a detached view of one process-cached inspection."""
        if self._inspection is None:
            async with self._lock:
                if self._inspection is None:
                    self._inspection = await self._discover()
        return self._inspection.model_copy(deep=True)

    async def _discover(self) -> ProviderDiscoveryResult:
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
                trust_env=not self._media_bearing,
                follow_redirects=False,
            ) as client:
                version = await self._get_json(client, "/api/version")
                if not isinstance(version.get("version"), str):
                    raise _normalized(
                        ProviderErrorKind.PROTOCOL_ERROR,
                        "The model provider returned an invalid response.",
                        retryable=True,
                    )
                tags = await self._get_json(client, "/api/tags")
                installed = _installed_models(tags)
                discovered: dict[str, DiscoveredModel] = {}
                for model in self._models:
                    if model not in installed:
                        discovered[model] = DiscoveredModel(
                            model=model,
                            available=False,
                            error=ProviderErrorKind.CAPABILITY_MISSING,
                        )
                        continue
                    try:
                        shown = await self._post_json(
                            client,
                            "/api/show",
                            {"model": model},
                        )
                    except ProviderInvocationError as error:
                        discovered[model] = DiscoveredModel(
                            model=model,
                            available=False,
                            error=error.error.kind,
                        )
                    else:
                        discovered[model] = _shown_model(model, shown)
        except ProviderInvocationError as error:
            discovered = {
                model: DiscoveredModel(
                    model=model,
                    available=False,
                    error=error.error.kind,
                )
                for model in self._models
            }
            installed = set()
        return ProviderDiscoveryResult(
            provider=ProviderName.OLLAMA,
            models=discovered,
            available_models=tuple(sorted(installed.intersection(self._models))),
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
    ) -> dict[str, object]:
        return await self._request_json(client, "GET", path)

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return await self._request_json(client, "POST", path, json_payload=payload)

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = await client.request(
                method,
                f"{self._endpoint}{path}",
                json=json_payload,
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
        if response.status_code >= 400:
            if response.status_code == 404 and path != "/api/show":
                raise _normalized(
                    ProviderErrorKind.PROTOCOL_ERROR,
                    "The model provider returned an invalid response.",
                    retryable=False,
                )
            raise _http_failure(response.status_code)
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
            ) from None
        if not isinstance(payload, dict):
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
            )
        return payload


def _installed_models(payload: dict[str, object]) -> set[str]:
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise _normalized(
            ProviderErrorKind.PROTOCOL_ERROR,
            "The model provider returned an invalid response.",
            retryable=True,
        )
    installed: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
            )
        name = item.get("name") or item.get("model")
        if not isinstance(name, str):
            raise _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
            )
        installed.add(name)
    return installed


def _shown_model(model: str, payload: dict[str, object]) -> DiscoveredModel:
    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list) or any(
        not isinstance(capability, str) for capability in raw_capabilities
    ):
        return DiscoveredModel(
            model=model,
            available=False,
            error=ProviderErrorKind.PROTOCOL_ERROR,
        )
    capability_names = set(raw_capabilities)
    capabilities: set[Capability] = set()
    if "completion" in capability_names:
        capabilities.update({Capability.TEXT, Capability.STRUCTURED_OUTPUT})
    if "vision" in capability_names:
        capabilities.add(Capability.VISION)
    return DiscoveredModel(
        model=model,
        available=True,
        capabilities=tuple(sorted(capabilities)),
        supported_parameters=("reasoning",) if "thinking" in capability_names else (),
    )


def _message_payload(message: ModelMessage) -> dict[str, object]:
    payload: dict[str, object] = {
        "role": message.role,
        "content": message.content,
    }
    if message.image is not None:
        payload["images"] = [b64encode(message.image).decode("ascii")]
    return payload


def _options_payload(tuning: TextTuning) -> dict[str, object]:
    options: dict[str, object] = {"temperature": tuning.temperature}
    if tuning.max_output_tokens is not None:
        options["num_predict"] = tuning.max_output_tokens
    if tuning.context_window is not None:
        options["num_ctx"] = tuning.context_window
    return options


def _usage(
    body: dict[str, object],
    *,
    role: AgentRole | None = None,
) -> ModelUsage | None:
    prompt = body.get("prompt_eval_count")
    completion = body.get("eval_count")
    if prompt is None and completion is None:
        return None
    if (
        not isinstance(prompt, int)
        or isinstance(prompt, bool)
        or prompt < 0
        or not isinstance(completion, int)
        or isinstance(completion, bool)
        or completion < 0
    ):
        raise _normalized(
            ProviderErrorKind.PROTOCOL_ERROR,
            "The model provider returned an invalid response.",
            retryable=True,
            role=role,
        )
    return ModelUsage(input_tokens=prompt, output_tokens=completion)


def _http_failure(
    status_code: int,
    *,
    role: AgentRole | None = None,
) -> ProviderInvocationError:
    if status_code == 404:
        return _normalized(
            ProviderErrorKind.CAPABILITY_MISSING,
            "The selected model is unavailable.",
            retryable=False,
            role=role,
        )
    if status_code == 429:
        return _normalized(
            ProviderErrorKind.RATE_LIMITED,
            "The selected model provider is rate limited.",
            retryable=True,
            role=role,
        )
    if status_code >= 500:
        return _normalized(
            ProviderErrorKind.UNAVAILABLE,
            "The selected model provider is unavailable.",
            retryable=True,
            role=role,
        )
    return _normalized(
        ProviderErrorKind.PROTOCOL_ERROR,
        "The model provider rejected the request.",
        retryable=False,
        role=role,
    )


class OllamaStructuredModel[ResultT]:
    """Adapt a LangChain Ollama runnable to the provider-neutral model protocol."""

    def __init__(self, delegate: StructuredModel[ResultT]) -> None:
        self._delegate = delegate

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> ResultT:
        failure: ProviderInvocationError | None = None
        try:
            if config:
                return await self._delegate.ainvoke(messages, config=config)
            return await self._delegate.ainvoke(messages)
        except (httpx.TimeoutException, TimeoutError):
            failure = _normalized(
                ProviderErrorKind.TIMED_OUT,
                "The model operation timed out.",
                retryable=True,
            )
        except (httpx.HTTPError, ResponseError):
            failure = _normalized(
                ProviderErrorKind.UNAVAILABLE,
                "The selected model provider is unavailable.",
                retryable=True,
            )
        except (OutputParserException, ValidationError):
            failure = _normalized(
                ProviderErrorKind.INVALID_OUTPUT,
                "The model returned invalid structured output.",
                retryable=True,
            )
        except ValueError:
            failure = _normalized(
                ProviderErrorKind.PROTOCOL_ERROR,
                "The model provider returned an invalid response.",
                retryable=True,
            )
        assert failure is not None
        raise failure


def adapt_structured_model[ResultT](
    model: StructuredModel[ResultT],
) -> OllamaStructuredModel[ResultT]:
    return OllamaStructuredModel(model)


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
            provider=ProviderName.OLLAMA,
            role=role,
        )
    )
