"""Native Ollama image-generation runtime."""

import asyncio
import re
from dataclasses import dataclass

import httpx

from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ImageOutputFormat,
    ImageQuality,
    ImageTuning,
    ModelResult,
    ModelUsage,
    ProviderDiscoveryResult,
    ProviderError,
    ProviderErrorKind,
    ProviderImageOutput,
    ProviderName,
)
from services.provider_errors import ProviderInvocationError
from services.providers._http_bodies import (
    DISCOVERY_BODY_LIMIT,
    IMAGE_BODY_LIMIT,
    ResponseBodyError,
    read_bounded_json_object,
)

_STABLE_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)


@dataclass(frozen=True, slots=True)
class _NativeImageContract:
    request_parameters: frozenset[str]
    qualities: frozenset[ImageQuality]
    output_formats: frozenset[ImageOutputFormat]


# Every entry is individually audited. Never infer compatibility for a range.
_AUDITED_CONTRACTS = {
    "0.32.5": _NativeImageContract(
        request_parameters=frozenset({"width", "height"}),
        qualities=frozenset(),
        output_formats=frozenset(),
    )
}


class OllamaImageRuntime:
    """Discover and invoke one exact configured Ollama image model."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        configured_capabilities: set[Capability] | frozenset[Capability],
        tuning: ImageTuning,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._configured_capabilities = frozenset(configured_capabilities)
        self._tuning = tuning
        self._transport = transport
        self._inspection: ProviderDiscoveryResult | None = None
        self._selected_contract: _NativeImageContract | None = None
        self._lock = asyncio.Lock()

    async def inspect(self) -> ProviderDiscoveryResult:
        """Return one detached process-cached exact-model inspection."""
        if self._inspection is None:
            async with self._lock:
                if self._inspection is None:
                    self._inspection = await self._discover()
        return self._inspection.model_copy(deep=True)

    async def generate_image(
        self,
        prompt: str,
        *,
        tuning: ImageTuning,
    ) -> ModelResult[ProviderImageOutput]:
        """Invoke only after the exact cached discovery admits all tuning."""
        if tuning != self._tuning:
            raise _normalized(
                ProviderErrorKind.CAPABILITY_MISSING,
                "The selected model lacks a required capability.",
                retryable=False,
            )
        inspection = await self.inspect()
        discovered = inspection.models[self._model]
        if not discovered.available or discovered.error is not None:
            kind = discovered.error or ProviderErrorKind.CAPABILITY_MISSING
            raise _normalized(
                kind,
                "The selected model lacks a required capability.",
                retryable=_retryable(kind),
            )
        contract = self._selected_contract
        if contract is None or not _supports_tuning(contract, tuning):
            raise _normalized(
                ProviderErrorKind.CAPABILITY_MISSING,
                "The selected model lacks a required capability.",
                retryable=False,
            )
        return await _invoke_native_image(
            endpoint=self._endpoint,
            model=self._model,
            prompt=prompt,
            tuning=tuning,
            contract=contract,
            transport=self._transport,
        )

    async def _discover(self) -> ProviderDiscoveryResult:
        if Capability.IMAGE_OUTPUT not in self._configured_capabilities:
            return self._result(
                DiscoveredModel(
                    model=self._model,
                    available=False,
                    error=ProviderErrorKind.CAPABILITY_MISSING,
                )
            )
        failure: ProviderInvocationError | None = None
        try:
            version_body = await self._request_json("GET", "/api/version")
            version = version_body.get("version")
            if (
                not isinstance(version, str)
                or _STABLE_VERSION.fullmatch(version) is None
            ):
                return self._failed(ProviderErrorKind.PROTOCOL_ERROR)
            contract = _AUDITED_CONTRACTS.get(version)
            if contract is None:
                return self._failed(ProviderErrorKind.CAPABILITY_MISSING)

            tags = await self._request_json("GET", "/api/tags")
            installed = _installed_models(tags)
            if self._model not in installed:
                return self._failed(ProviderErrorKind.CAPABILITY_MISSING)

            shown = await self._request_json(
                "POST",
                "/api/show",
                payload={"model": self._model},
            )
            raw_capabilities = shown.get("capabilities")
            if not isinstance(raw_capabilities, list) or any(
                not isinstance(capability, str) for capability in raw_capabilities
            ):
                return self._failed(ProviderErrorKind.PROTOCOL_ERROR)
            if "image" not in raw_capabilities:
                return self._failed(ProviderErrorKind.CAPABILITY_MISSING)

            capabilities = (Capability.IMAGE_OUTPUT,)
            supported_parameters = tuple(sorted(contract.request_parameters))
            if not _supports_tuning(contract, self._tuning):
                return self._result(
                    DiscoveredModel(
                        model=self._model,
                        available=False,
                        capabilities=capabilities,
                        supported_parameters=supported_parameters,
                        error=ProviderErrorKind.CAPABILITY_MISSING,
                    )
                )
            self._selected_contract = contract
            return self._result(
                DiscoveredModel(
                    model=self._model,
                    available=True,
                    capabilities=capabilities,
                    supported_parameters=supported_parameters,
                )
            )
        except ProviderInvocationError as error:
            failure = error
        assert failure is not None
        return self._failed(failure.error.kind)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        transport_failure: ProviderInvocationError | None = None
        body: dict[str, object] | None = None
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._tuning.timeout_seconds,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    method,
                    f"{self._endpoint}{path}",
                    json=payload,
                ) as response,
            ):
                body = await read_bounded_json_object(
                    response,
                    success_limit=DISCOVERY_BODY_LIMIT,
                )
                status_code = response.status_code
        except httpx.TimeoutException:
            transport_failure = _normalized(
                ProviderErrorKind.TIMED_OUT,
                "The model operation timed out.",
                retryable=True,
            )
        except httpx.RequestError:
            transport_failure = _normalized(
                ProviderErrorKind.UNAVAILABLE,
                "The selected model provider is unavailable.",
                retryable=True,
            )
        except ResponseBodyError:
            transport_failure = _protocol_error()
        if transport_failure is not None:
            raise transport_failure
        assert body is not None
        if status_code != 200:
            raise _http_failure(status_code, path=path)
        return body

    def _failed(self, kind: ProviderErrorKind) -> ProviderDiscoveryResult:
        return self._result(
            DiscoveredModel(model=self._model, available=False, error=kind)
        )

    def _result(self, model: DiscoveredModel) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.OLLAMA,
            models={self._model: model},
            available_models=(self._model,) if model.available else (),
        )


def _installed_models(body: dict[str, object]) -> set[str]:
    raw_models = body.get("models")
    if not isinstance(raw_models, list):
        raise _protocol_error()
    installed: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            raise _protocol_error()
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise _protocol_error()
        if name in installed:
            raise _protocol_error()
        installed.add(name)
    return installed


def _supports_tuning(contract: _NativeImageContract, tuning: ImageTuning) -> bool:
    return (
        {"width", "height"}.issubset(contract.request_parameters)
        and tuning.quality in contract.qualities
        and tuning.output_format in contract.output_formats
    )


async def _invoke_native_image(
    *,
    endpoint: str,
    model: str,
    prompt: str,
    tuning: ImageTuning,
    contract: _NativeImageContract,
    transport: httpx.AsyncBaseTransport | None,
) -> ModelResult[ProviderImageOutput]:
    if not _supports_tuning(contract, tuning):
        raise _normalized(
            ProviderErrorKind.CAPABILITY_MISSING,
            "The selected model lacks a required capability.",
            retryable=False,
        )
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "width": tuning.width,
        "height": tuning.height,
        "stream": False,
    }
    failure: ProviderInvocationError | None = None
    body: dict[str, object] | None = None
    status_code: int | None = None
    try:
        async with (
            httpx.AsyncClient(
                transport=transport,
                timeout=tuning.timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client,
            client.stream(
                "POST",
                f"{endpoint.rstrip('/')}/api/generate",
                json=payload,
            ) as response,
        ):
            body = await read_bounded_json_object(
                response,
                success_limit=IMAGE_BODY_LIMIT,
            )
            status_code = response.status_code
    except httpx.TimeoutException:
        failure = _normalized(
            ProviderErrorKind.TIMED_OUT,
            "The model operation timed out.",
            retryable=True,
        )
    except httpx.RequestError:
        failure = _normalized(
            ProviderErrorKind.UNAVAILABLE,
            "The selected model provider is unavailable.",
            retryable=True,
        )
    except ResponseBodyError:
        failure = _protocol_error()
    if failure is not None:
        raise failure
    assert status_code is not None and body is not None
    if status_code != 200:
        raise _generation_http_failure(status_code)
    return _parse_generation_body(body, model=model)


def _parse_generation_body(
    body: dict[str, object],
    *,
    model: str,
) -> ModelResult[ProviderImageOutput]:
    if body.get("model") != model or body.get("done") is not True:
        raise _protocol_error()
    image = body.get("image")
    if not isinstance(image, str) or not image:
        raise _protocol_error()
    return ModelResult(
        output=ProviderImageOutput(base64_data=image, media_type=None),
        usage=_usage(body),
    )


def _usage(body: dict[str, object]) -> ModelUsage | None:
    prompt = body.get("prompt_eval_count")
    completion = body.get("eval_count")
    if prompt is None and completion is None:
        return None
    if (
        not isinstance(prompt, int)
        or isinstance(prompt, bool)
        or not 0 <= prompt <= (2**63 - 1)
        or not isinstance(completion, int)
        or isinstance(completion, bool)
        or not 0 <= completion <= (2**63 - 1)
    ):
        raise _protocol_error()
    return ModelUsage(input_tokens=prompt, output_tokens=completion)


def _http_failure(status_code: int, *, path: str) -> ProviderInvocationError:
    if status_code == 404 and path == "/api/show":
        return _normalized(
            ProviderErrorKind.CAPABILITY_MISSING,
            "The selected model is unavailable.",
            retryable=False,
        )
    if status_code == 429:
        return _normalized(
            ProviderErrorKind.RATE_LIMITED,
            "The selected model provider is rate limited.",
            retryable=True,
        )
    if status_code >= 500:
        return _normalized(
            ProviderErrorKind.UNAVAILABLE,
            "The selected model provider is unavailable.",
            retryable=True,
        )
    return _protocol_error()


def _generation_http_failure(status_code: int) -> ProviderInvocationError:
    if status_code == 404:
        return _normalized(
            ProviderErrorKind.CAPABILITY_MISSING,
            "The selected model is unavailable.",
            retryable=False,
        )
    if status_code == 429:
        return _normalized(
            ProviderErrorKind.RATE_LIMITED,
            "The selected model provider is rate limited.",
            retryable=True,
        )
    if status_code >= 500:
        return _normalized(
            ProviderErrorKind.UNAVAILABLE,
            "The selected model provider is unavailable.",
            retryable=True,
        )
    return _protocol_error()


def _protocol_error() -> ProviderInvocationError:
    return _normalized(
        ProviderErrorKind.PROTOCOL_ERROR,
        "The model provider returned an invalid response.",
        retryable=False,
    )


def _retryable(kind: ProviderErrorKind) -> bool:
    return kind in {
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.TIMED_OUT,
        ProviderErrorKind.UNAVAILABLE,
    }


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
            provider=ProviderName.OLLAMA,
            role=AgentRole.IMAGE_GENERATOR,
        )
    )
