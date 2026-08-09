"""OpenRouter Images API runtime."""

import asyncio
import re
import unicodedata
from urllib.parse import quote, urlsplit

import httpx
from pydantic import SecretStr

from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
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

_ENCODED_PATH_TRICK = re.compile(r"%(?:25)*(?:2f|5c|2e)", re.IGNORECASE)


class OpenRouterImageRuntime:
    """Discover and invoke one exact configured OpenRouter image endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: SecretStr,
        model: str,
        provider_tag: str | None,
        configured_capabilities: set[Capability] | frozenset[Capability],
        tuning: ImageTuning,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._official_endpoint = _is_official_endpoint(endpoint)
        self._endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
        self._api_key = api_key
        self._model = model
        self._provider_tag = provider_tag
        self._configured_capabilities = frozenset(configured_capabilities)
        self._tuning = tuning
        self._transport = transport
        self._inspection: ProviderDiscoveryResult | None = None
        self._lock = asyncio.Lock()

    async def inspect(self) -> ProviderDiscoveryResult:
        """Return one detached process-cached exact endpoint inspection."""
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
        """Invoke only the one endpoint admitted by cached discovery."""
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
                _message(kind),
                retryable=_retryable(kind),
            )
        provider_tag = self._provider_tag
        assert provider_tag is not None
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "n": 1,
            "stream": False,
            "size": f"{tuning.width}x{tuning.height}",
            "quality": tuning.quality.value,
            "output_format": tuning.output_format.value,
            "provider": {
                "only": [provider_tag],
                "allow_fallbacks": False,
            },
        }
        failure: ProviderInvocationError | None = None
        body: dict[str, object] | None = None
        status_code: int | None = None
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=tuning.timeout_seconds,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    "POST",
                    f"{self._endpoint}/images",
                    headers={"Authorization": self._authorization_header()},
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
            raise _http_failure(status_code)
        return _parse_generation_body(body)

    async def _discover(self) -> ProviderDiscoveryResult:
        semantic_error = self._semantic_error()
        if semantic_error is not None:
            return self._failed(semantic_error)
        model_segments = _model_segments(self._model)
        if model_segments is None:
            return self._failed(ProviderErrorKind.CAPABILITY_MISSING)

        failure: ProviderInvocationError | None = None
        try:
            models = await self._request_json("/images/models")
            records = _model_records(models)
            matches = [record for record in records if record["id"] == self._model]
            if not matches:
                return self._failed(ProviderErrorKind.CAPABILITY_MISSING)
            if len(matches) != 1:
                return self._failed(ProviderErrorKind.PROTOCOL_ERROR)
            capability_error = _model_capability_error(matches[0])
            if capability_error is not None:
                return self._failed(capability_error)

            author, slug = model_segments
            endpoint_catalog = await self._request_json(
                "/images/models/"
                f"{quote(author, safe='')}/{quote(slug, safe='')}/endpoints"
            )
            endpoint_error, parameter_names = _endpoint_capability(
                endpoint_catalog,
                model=self._model,
                provider_tag=self._provider_tag,
                tuning=self._tuning,
            )
            if endpoint_error is not None:
                return self._failed(endpoint_error)
            return self._result(
                DiscoveredModel(
                    model=self._model,
                    available=True,
                    capabilities=(Capability.IMAGE_OUTPUT,),
                    supported_parameters=parameter_names,
                )
            )
        except ProviderInvocationError as error:
            failure = error
        assert failure is not None
        return self._failed(failure.error.kind)

    def _semantic_error(self) -> ProviderErrorKind | None:
        if Capability.IMAGE_OUTPUT not in self._configured_capabilities:
            return ProviderErrorKind.CAPABILITY_MISSING
        if not self._official_endpoint:
            return ProviderErrorKind.CAPABILITY_MISSING
        if not self._authorization_header():
            return ProviderErrorKind.AUTHENTICATION_FAILED
        tag = self._provider_tag
        if (
            tag is None
            or tag != tag.strip()
            or not 1 <= len(tag) <= 200
            or any(unicodedata.category(character) == "Cc" for character in tag)
        ):
            return ProviderErrorKind.CAPABILITY_MISSING
        return None

    async def _request_json(self, path: str) -> dict[str, object]:
        failure: ProviderInvocationError | None = None
        body: dict[str, object] | None = None
        status_code: int | None = None
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._tuning.timeout_seconds,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    "GET",
                    f"{self._endpoint}{path}",
                    headers={"Authorization": self._authorization_header()},
                ) as response,
            ):
                body = await read_bounded_json_object(
                    response,
                    success_limit=DISCOVERY_BODY_LIMIT,
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
            raise _http_failure(status_code)
        return body

    def _authorization_header(self) -> str:
        value = self._api_key.get_secret_value()
        return f"Bearer {value}" if value.strip() else ""

    def _failed(self, kind: ProviderErrorKind) -> ProviderDiscoveryResult:
        return self._result(
            DiscoveredModel(model=self._model, available=False, error=kind)
        )

    def _result(self, model: DiscoveredModel) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.OPENROUTER,
            models={self._model: model},
            available_models=(self._model,) if model.available else (),
        )


def _is_official_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "openrouter.ai"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.path in {"/api/v1", "/api/v1/"}
            and parsed.netloc.casefold() == "openrouter.ai"
        )
    except ValueError:
        return False


def _model_segments(model: str) -> tuple[str, str] | None:
    segments = model.split("/")
    if len(segments) != 2:
        return None
    if any(
        not segment
        or segment in {".", ".."}
        or "\\" in segment
        or _ENCODED_PATH_TRICK.search(segment) is not None
        or any(unicodedata.category(character) == "Cc" for character in segment)
        for segment in segments
    ):
        return None
    return segments[0], segments[1]


def _model_records(body: dict[str, object]) -> list[dict[str, object]]:
    data = body.get("data")
    if not isinstance(data, list):
        raise _protocol_error()
    records: list[dict[str, object]] = []
    for record in data:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise _protocol_error()
        records.append(record)
    return records


def _model_capability_error(
    record: dict[str, object],
) -> ProviderErrorKind | None:
    architecture = record.get("architecture")
    if not isinstance(architecture, dict):
        return ProviderErrorKind.PROTOCOL_ERROR
    outputs = architecture.get("output_modalities")
    if not isinstance(outputs, list) or any(
        not isinstance(modality, str) for modality in outputs
    ):
        return ProviderErrorKind.PROTOCOL_ERROR
    if "image" not in outputs:
        return ProviderErrorKind.CAPABILITY_MISSING
    return None


def _endpoint_capability(
    body: dict[str, object],
    *,
    model: str,
    provider_tag: str | None,
    tuning: ImageTuning,
) -> tuple[ProviderErrorKind | None, tuple[str, ...]]:
    if body.get("id") != model:
        return ProviderErrorKind.PROTOCOL_ERROR, ()
    endpoints = body.get("endpoints")
    if not isinstance(endpoints, list):
        return ProviderErrorKind.PROTOCOL_ERROR, ()
    matches: list[dict[str, object]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            return ProviderErrorKind.PROTOCOL_ERROR, ()
        returned_tag = endpoint.get("provider_tag")
        if returned_tag is not None and not isinstance(returned_tag, str):
            return ProviderErrorKind.PROTOCOL_ERROR, ()
        if returned_tag == provider_tag:
            matches.append(endpoint)
    if len(matches) != 1:
        return ProviderErrorKind.CAPABILITY_MISSING, ()
    parameters = matches[0].get("supported_parameters")
    if not isinstance(parameters, dict):
        return ProviderErrorKind.CAPABILITY_MISSING, ()
    required = {
        "size": f"{tuning.width}x{tuning.height}",
        "quality": tuning.quality.value,
        "output_format": tuning.output_format.value,
    }
    if any(
        not _enum_admits(parameters.get(name), value)
        for name, value in required.items()
    ):
        return ProviderErrorKind.CAPABILITY_MISSING, ()
    if not _one_is_admitted(parameters.get("n")):
        return ProviderErrorKind.CAPABILITY_MISSING, ()
    return None, ("n", "output_format", "quality", "size")


def _enum_admits(descriptor: object, value: str) -> bool:
    if not isinstance(descriptor, dict) or descriptor.get("type") != "enum":
        return False
    values = descriptor.get("values")
    return (
        isinstance(values, list)
        and all(isinstance(candidate, str) for candidate in values)
        and value in values
    )


def _one_is_admitted(descriptor: object) -> bool:
    if not isinstance(descriptor, dict):
        return False
    descriptor_type = descriptor.get("type")
    if descriptor_type == "enum":
        values = descriptor.get("values")
        return (
            isinstance(values, list)
            and all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in values
            )
            and 1 in values
        )
    if descriptor_type == "range":
        minimum = descriptor.get("min")
        maximum = descriptor.get("max")
        return (
            isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and minimum <= 1 <= maximum
        )
    return False


def _parse_generation_body(
    body: dict[str, object],
) -> ModelResult[ProviderImageOutput]:
    created = body.get("created")
    if (
        not isinstance(created, int)
        or isinstance(created, bool)
        or not 0 <= created <= (2**63 - 1)
    ):
        raise _protocol_error()
    data = body.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise _protocol_error()
    item = data[0]
    if not isinstance(item, dict):
        raise _protocol_error()
    encoded = item.get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise _protocol_error()
    media_type: str | None = None
    if "media_type" in item:
        raw_media_type = item["media_type"]
        if not isinstance(raw_media_type, str) or not 1 <= len(raw_media_type) <= 100:
            raise _protocol_error()
        media_type = raw_media_type
    return ModelResult(
        output=ProviderImageOutput(
            base64_data=encoded,
            media_type=media_type,
        ),
        usage=_usage(body),
    )


def _usage(body: dict[str, object]) -> ModelUsage | None:
    raw_usage = body.get("usage")
    if raw_usage is None:
        return None
    if not isinstance(raw_usage, dict):
        raise _protocol_error()
    values: list[int] = []
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw_usage.get(name)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= (2**63 - 1)
        ):
            raise _protocol_error()
        values.append(value)
    prompt, completion, total = values
    if total != prompt + completion or total > (2**63 - 1):
        raise _protocol_error()
    return ModelUsage(input_tokens=prompt, output_tokens=completion)


def _http_failure(status_code: int) -> ProviderInvocationError:
    kind = {
        401: ProviderErrorKind.AUTHENTICATION_FAILED,
        402: ProviderErrorKind.PAYMENT_REQUIRED,
        404: ProviderErrorKind.CAPABILITY_MISSING,
        429: ProviderErrorKind.RATE_LIMITED,
        524: ProviderErrorKind.TIMED_OUT,
    }.get(status_code)
    if kind is None:
        kind = (
            ProviderErrorKind.UNAVAILABLE
            if status_code >= 500
            else ProviderErrorKind.PROTOCOL_ERROR
        )
    messages = {
        ProviderErrorKind.AUTHENTICATION_FAILED: (
            "Model provider authentication failed."
        ),
        ProviderErrorKind.PAYMENT_REQUIRED: "The model provider requires payment.",
        ProviderErrorKind.CAPABILITY_MISSING: "The selected model is unavailable.",
        ProviderErrorKind.RATE_LIMITED: "The selected model provider is rate limited.",
        ProviderErrorKind.TIMED_OUT: "The model operation timed out.",
        ProviderErrorKind.UNAVAILABLE: "The selected model provider is unavailable.",
        ProviderErrorKind.PROTOCOL_ERROR: (
            "The model provider returned an invalid response."
        ),
    }
    return _normalized(
        kind,
        messages[kind],
        retryable=kind
        in {
            ProviderErrorKind.RATE_LIMITED,
            ProviderErrorKind.TIMED_OUT,
            ProviderErrorKind.UNAVAILABLE,
        },
    )


def _message(kind: ProviderErrorKind) -> str:
    return {
        ProviderErrorKind.AUTHENTICATION_FAILED: (
            "Model provider authentication failed."
        ),
        ProviderErrorKind.PAYMENT_REQUIRED: "The model provider requires payment.",
        ProviderErrorKind.CAPABILITY_MISSING: (
            "The selected model lacks a required capability."
        ),
        ProviderErrorKind.RATE_LIMITED: "The selected model provider is rate limited.",
        ProviderErrorKind.TIMED_OUT: "The model operation timed out.",
        ProviderErrorKind.UNAVAILABLE: "The selected model provider is unavailable.",
        ProviderErrorKind.PROTOCOL_ERROR: (
            "The model provider returned an invalid response."
        ),
        ProviderErrorKind.INVALID_OUTPUT: "The model returned invalid output.",
    }[kind]


def _retryable(kind: ProviderErrorKind) -> bool:
    return kind in {
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.TIMED_OUT,
        ProviderErrorKind.UNAVAILABLE,
    }


def _protocol_error() -> ProviderInvocationError:
    return _normalized(
        ProviderErrorKind.PROTOCOL_ERROR,
        "The model provider returned an invalid response.",
        retryable=False,
    )


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
            provider=ProviderName.OPENROUTER,
            role=AgentRole.IMAGE_GENERATOR,
        )
    )
