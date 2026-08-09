import asyncio
import importlib
import json

import httpx
import pytest
from pydantic import SecretStr

from core.logging import cause_chain
from domain.model_runtime import (
    AgentRole,
    Capability,
    ImageTuning,
    ProviderErrorKind,
)
from services.provider_errors import ProviderInvocationError
from services.providers._http_bodies import ERROR_BODY_LIMIT


def test_openrouter_image_runtime_is_available() -> None:
    module = importlib.import_module("services.providers.openrouter_images")

    assert callable(getattr(module, "OpenRouterImageRuntime", None))


_MODEL = "author/image-exact"
_TAG = "provider/tag-exact"


def _runtime(
    transport: httpx.AsyncBaseTransport,
    *,
    endpoint: str = "https://openrouter.ai/api/v1",
    api_key: str = "secret-image-key",
    model: str = _MODEL,
    provider_tag: str | None = _TAG,
    capabilities: set[Capability] | frozenset[Capability] = frozenset(
        {Capability.IMAGE_OUTPUT}
    ),
    tuning: ImageTuning | None = None,
) -> object:
    module = importlib.import_module("services.providers.openrouter_images")
    return module.OpenRouterImageRuntime(
        endpoint=endpoint,
        api_key=SecretStr(api_key),
        model=model,
        provider_tag=provider_tag,
        configured_capabilities=capabilities,
        tuning=tuning or ImageTuning(),
        transport=transport,
    )


def test_openrouter_image_runtime_retains_api_key_as_masked_secret() -> None:
    secret_canary = "openrouter-image-secret-canary"

    runtime = _runtime(
        httpx.MockTransport(lambda _: httpx.Response(500)),
        api_key=secret_canary,
    )

    assert isinstance(vars(runtime)["_api_key"], SecretStr)
    assert secret_canary not in repr(vars(runtime))


def _model_catalog(model: str = _MODEL) -> dict[str, object]:
    return {
        "data": [
            {
                "id": model,
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["image"],
                },
            }
        ]
    }


def _parameters(*, include_size: bool = True) -> dict[str, object]:
    descriptors: dict[str, object] = {
        "quality": {"type": "enum", "values": ["low", "medium", "high"]},
        "output_format": {
            "type": "enum",
            "values": ["png", "jpeg", "webp"],
        },
        "n": {"type": "range", "min": 1, "max": 4},
    }
    if include_size:
        descriptors["size"] = {
            "type": "enum",
            "values": ["512x512", "1024x1024"],
        }
    return descriptors


def _endpoint_catalog(
    *,
    model: str = _MODEL,
    provider_tag: object = _TAG,
    parameters: object | None = None,
    duplicate: bool = False,
) -> dict[str, object]:
    record: dict[str, object] = {
        "provider_tag": provider_tag,
        "provider_slug": "must-not-be-used",
    }
    if parameters is not None:
        record["supported_parameters"] = parameters
    endpoints = [record, {"provider_tag": "unselected/tag"}]
    if duplicate:
        endpoints.append(dict(record))
    return {"id": model, "endpoints": endpoints}


@pytest.mark.asyncio
async def test_openrouter_image_discovery_uses_exact_catalog_paths_and_caches() -> None:
    calls: list[tuple[str, bytes]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.raw_path))
        assert request.headers["authorization"] == "Bearer secret-image-key"
        if request.url.path == "/api/v1/images/models":
            return httpx.Response(200, json=_model_catalog())
        return httpx.Response(
            200,
            json=_endpoint_catalog(parameters=_parameters()),
        )

    runtime = _runtime(httpx.MockTransport(respond))

    first, second = await asyncio.gather(runtime.inspect(), runtime.inspect())

    assert first is not second
    discovered = first.models[_MODEL]
    assert discovered.available is True
    assert discovered.error is None
    assert discovered.capabilities == (Capability.IMAGE_OUTPUT,)
    assert discovered.supported_parameters == (
        "n",
        "output_format",
        "quality",
        "size",
    )
    assert first.available_models == (_MODEL,)
    assert calls == [
        ("GET", b"/api/v1/images/models"),
        ("GET", b"/api/v1/images/models/author/image-exact/endpoints"),
    ]

    first.models[_MODEL] = discovered.model_copy(
        update={"error": ProviderErrorKind.PROTOCOL_ERROR}
    )
    later = await runtime.inspect()
    assert later.models[_MODEL].error is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_openrouter_image_discovery_encodes_segments_independently() -> None:
    model = "author name/image+exact"
    paths: list[bytes] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path)
        if request.url.path == "/api/v1/images/models":
            return httpx.Response(200, json=_model_catalog(model))
        return httpx.Response(
            200,
            json=_endpoint_catalog(model=model, parameters=_parameters()),
        )

    result = await _runtime(httpx.MockTransport(respond), model=model).inspect()

    assert result.models[model].available is True
    assert paths == [
        b"/api/v1/images/models",
        b"/api/v1/images/models/author%20name/image%2Bexact/endpoints",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "one-segment",
        "author/slug/extra",
        "/slug",
        "author/",
        "./slug",
        "author/..",
        "author\\escape/slug",
        "author/%2f",
        "author/%2Fetc",
        "author/%252fetc",
        "author/%25252Fetc",
        "author/%5cetc",
        "author/%2e%2e",
        "author/%252e%252e",
        "author/control\n",
    ],
)
async def test_openrouter_rejects_unsafe_model_paths_before_http(model: str) -> None:
    async def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("unsafe configured model must not reach HTTP")

    result = await _runtime(httpx.MockTransport(unexpected), model=model).inspect()

    assert result.models[model].available is False
    assert result.models[model].error is ProviderErrorKind.CAPABILITY_MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "provider_tag", "capabilities", "expected"),
    [
        (
            "https://proxy.invalid/api/v1",
            "secret",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "http://openrouter.ai/api/v1",
            "secret",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "https://openrouter.ai:443/api/v1",
            "secret",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "https://openrouter.ai/api/v1/extra",
            "secret",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "https://openrouter.ai/api/v1//",
            "secret",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "https://openrouter.ai/api/v1",
            "",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.AUTHENTICATION_FAILED,
        ),
        (
            "https://openrouter.ai/api/v1",
            "   ",
            _TAG,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.AUTHENTICATION_FAILED,
        ),
        (
            "https://openrouter.ai/api/v1",
            "secret",
            None,
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "https://openrouter.ai/api/v1",
            "secret",
            " ",
            frozenset({Capability.IMAGE_OUTPUT}),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
        (
            "https://openrouter.ai/api/v1",
            "secret",
            _TAG,
            frozenset(),
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
    ],
)
async def test_openrouter_semantic_admission_fails_before_http(
    endpoint: str,
    api_key: str,
    provider_tag: str | None,
    capabilities: frozenset[Capability],
    expected: ProviderErrorKind,
) -> None:
    async def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid image selection must use zero HTTP")

    result = await _runtime(
        httpx.MockTransport(unexpected),
        endpoint=endpoint,
        api_key=api_key,
        provider_tag=provider_tag,
        capabilities=capabilities,
    ).inspect()

    assert result.models[_MODEL].available is False
    assert result.models[_MODEL].error is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("models", "endpoints", "expected", "calls"),
    [
        ({"data": []}, None, ProviderErrorKind.CAPABILITY_MISSING, 1),
        (
            {"data": [_model_catalog()["data"][0], _model_catalog()["data"][0]]},
            None,
            ProviderErrorKind.PROTOCOL_ERROR,
            1,
        ),
        (
            {
                "data": [
                    {
                        "id": _MODEL,
                        "architecture": {"output_modalities": ["text"]},
                    }
                ]
            },
            None,
            ProviderErrorKind.CAPABILITY_MISSING,
            1,
        ),
        (
            {"data": [{"id": _MODEL, "architecture": {"output_modalities": [7]}}]},
            None,
            ProviderErrorKind.PROTOCOL_ERROR,
            1,
        ),
        (
            _model_catalog(),
            _endpoint_catalog(model="wrong/model", parameters=_parameters()),
            ProviderErrorKind.PROTOCOL_ERROR,
            2,
        ),
        (
            _model_catalog(),
            _endpoint_catalog(provider_tag=None, parameters=_parameters()),
            ProviderErrorKind.CAPABILITY_MISSING,
            2,
        ),
        (
            _model_catalog(),
            _endpoint_catalog(provider_tag="other/tag", parameters=_parameters()),
            ProviderErrorKind.CAPABILITY_MISSING,
            2,
        ),
        (
            _model_catalog(),
            _endpoint_catalog(parameters=_parameters(), duplicate=True),
            ProviderErrorKind.CAPABILITY_MISSING,
            2,
        ),
        (
            _model_catalog(),
            {"id": _MODEL, "endpoints": "bad"},
            ProviderErrorKind.PROTOCOL_ERROR,
            2,
        ),
    ],
)
async def test_openrouter_requires_exact_model_image_and_unique_endpoint(
    models: object,
    endpoints: object | None,
    expected: ProviderErrorKind,
    calls: int,
) -> None:
    seen = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        if seen == 1:
            return httpx.Response(200, json=models)
        return httpx.Response(200, json=endpoints)

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models[_MODEL].available is False
    assert result.models[_MODEL].error is expected
    assert seen == calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [
        _parameters(include_size=False),
        {**_parameters(), "size": True},
        {
            **_parameters(),
            "size": {"type": "enum", "values": ["1024-by-1024"]},
        },
        {
            **_parameters(),
            "quality": {"type": "enum", "values": ["high"]},
        },
        {
            **_parameters(),
            "output_format": {"type": "enum", "values": ["png"]},
        },
        {**_parameters(), "n": {"type": "range", "min": 2, "max": 4}},
        {**_parameters(), "n": {"type": "enum", "values": [2, 3]}},
        {**_parameters(), "n": True},
    ],
)
async def test_openrouter_missing_or_inexact_descriptor_fails_closed(
    parameters: object,
) -> None:
    paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v1/images/models":
            return httpx.Response(200, json=_model_catalog())
        if request.url.path.endswith("/endpoints"):
            return httpx.Response(
                200,
                json=_endpoint_catalog(parameters=parameters),
            )
        pytest.fail("unsupported tuning must make zero generation calls")

    runtime = _runtime(httpx.MockTransport(respond))
    result = await runtime.inspect()

    assert result.models[_MODEL].available is False
    assert result.models[_MODEL].error is ProviderErrorKind.CAPABILITY_MISSING
    assert paths == [
        "/api/v1/images/models",
        "/api/v1/images/models/author/image-exact/endpoints",
    ]


async def _ready_response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/images/models":
        return httpx.Response(200, json=_model_catalog())
    if request.url.path.endswith("/endpoints"):
        return httpx.Response(
            200,
            json=_endpoint_catalog(parameters=_parameters()),
        )
    raise AssertionError("generation response must be provided by the test")


@pytest.mark.asyncio
async def test_openrouter_generation_uses_exact_images_request_and_maps_usage() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return await _ready_response(request)
        return httpx.Response(
            200,
            json={
                "created": 1_751_000_000,
                "data": [
                    {
                        "b64_json": "cHJvdmlkZXItaW1hZ2U=",
                        "media_type": "image/webp",
                    }
                ],
                "usage": {
                    "prompt_tokens": 19,
                    "completion_tokens": 7,
                    "total_tokens": 26,
                    "cost": 999,
                    "opaque": {"private": "ignored"},
                },
            },
        )

    result = await _runtime(httpx.MockTransport(respond)).generate_image(
        "protected prompt",
        tuning=ImageTuning(),
    )

    assert result.output.base64_data == "cHJvdmlkZXItaW1hZ2U="
    assert result.output.media_type == "image/webp"
    assert result.usage is not None
    assert result.usage.input_tokens == 19
    assert result.usage.output_tokens == 7
    assert result.usage.provider_metadata == {}
    assert len(requests) == 3
    request = requests[-1]
    assert request.url == httpx.URL("https://openrouter.ai/api/v1/images")
    assert request.headers["authorization"] == "Bearer secret-image-key"
    payload = json.loads(request.content)
    assert payload == {
        "model": _MODEL,
        "prompt": "protected prompt",
        "n": 1,
        "stream": False,
        "size": "1024x1024",
        "quality": "medium",
        "output_format": "webp",
        "provider": {
            "only": [_TAG],
            "allow_fallbacks": False,
        },
    }
    assert "require_parameters" not in payload["provider"]
    assert "order" not in payload["provider"]
    assert "options" not in payload["provider"]
    assert "resolution" not in payload
    assert "aspect_ratio" not in payload
    assert "provider_slug" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_openrouter_generation_allows_absent_media_type_and_usage() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await _ready_response(request)
        return httpx.Response(
            200,
            json={"created": 0, "data": [{"b64_json": "YWJj"}]},
        )

    result = await _runtime(httpx.MockTransport(respond)).generate_image(
        "protected prompt",
        tuning=ImageTuning(),
    )

    assert result.output.media_type is None
    assert result.usage is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"created": True, "data": [{"b64_json": "YWJj"}]},
        {"created": -1, "data": [{"b64_json": "YWJj"}]},
        {"created": 2**63, "data": [{"b64_json": "YWJj"}]},
        {"created": 1, "data": []},
        {"created": 1, "data": [{"b64_json": "YWJj"}, {"b64_json": "ZGVm"}]},
        {"created": 1, "data": [7]},
        {"created": 1, "data": [{}]},
        {"created": 1, "data": [{"b64_json": ""}]},
        {"created": 1, "data": [{"b64_json": 7}]},
        {"created": 1, "data": [{"b64_json": "YWJj", "media_type": None}]},
        {"created": 1, "data": [{"b64_json": "YWJj", "media_type": ""}]},
        {
            "created": 1,
            "data": [{"b64_json": "YWJj", "media_type": "x" * 101}],
        },
        {"created": 1, "data": [{"b64_json": "YWJj"}], "usage": {}},
        {
            "created": 1,
            "data": [{"b64_json": "YWJj"}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 4,
            },
        },
        {
            "created": 1,
            "data": [{"b64_json": "YWJj"}],
            "usage": {
                "prompt_tokens": True,
                "completion_tokens": 2,
                "total_tokens": 3,
            },
        },
        {
            "created": 1,
            "data": [{"b64_json": "YWJj"}],
            "usage": {
                "prompt_tokens": 2**63,
                "completion_tokens": 0,
                "total_tokens": 2**63,
            },
        },
    ],
)
async def test_openrouter_generation_rejects_malformed_success(body: object) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await _ready_response(request)
        return httpx.Response(200, json=body)

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.role is AgentRole.IMAGE_GENERATOR
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (307, ProviderErrorKind.PROTOCOL_ERROR, False),
        (400, ProviderErrorKind.PROTOCOL_ERROR, False),
        (401, ProviderErrorKind.AUTHENTICATION_FAILED, False),
        (402, ProviderErrorKind.PAYMENT_REQUIRED, False),
        (403, ProviderErrorKind.PROTOCOL_ERROR, False),
        (404, ProviderErrorKind.CAPABILITY_MISSING, False),
        (413, ProviderErrorKind.PROTOCOL_ERROR, False),
        (429, ProviderErrorKind.RATE_LIMITED, True),
        (500, ProviderErrorKind.UNAVAILABLE, True),
        (502, ProviderErrorKind.UNAVAILABLE, True),
        (503, ProviderErrorKind.UNAVAILABLE, True),
        (524, ProviderErrorKind.TIMED_OUT, True),
        (529, ProviderErrorKind.UNAVAILABLE, True),
        (599, ProviderErrorKind.UNAVAILABLE, True),
    ],
)
async def test_openrouter_generation_maps_images_status_without_retry(
    status: int,
    expected: ProviderErrorKind,
    retryable: bool,
) -> None:
    generation_calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal generation_calls
        if request.method == "GET":
            return await _ready_response(request)
        generation_calls += 1
        return httpx.Response(
            status,
            headers={"Location": "https://redirect.invalid/private"},
            json={
                "error": {
                    "code": status,
                    "message": "private-provider-body",
                    "metadata": {"error_type": "must-not-drive-mapping"},
                }
            },
        )

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is expected
    assert raised.value.error.retryable is retryable
    assert generation_calls == 1
    assert "private-provider-body" not in repr(raised.value)
    assert "must-not-drive-mapping" not in repr(raised.value)


@pytest.mark.asyncio
async def test_openrouter_malformed_error_body_still_uses_status() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await _ready_response(request)
        return httpx.Response(401, content=b"raw-private-non-json-body")

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is ProviderErrorKind.AUTHENTICATION_FAILED
    assert "raw-private-non-json-body" not in repr(raised.value)


@pytest.mark.asyncio
async def test_openrouter_generation_timeout_is_detached_and_not_replayed() -> None:
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "GET":
            return await _ready_response(request)
        calls += 1
        raise httpx.ReadTimeout(
            "secret-image-key | private prompt | raw body",
            request=request,
        )

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "secret-image-key" not in repr(raised.value)
    assert calls == 1
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model operation timed out."
    ]


@pytest.mark.asyncio
async def test_openrouter_generation_cancellation_propagates() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await _ready_response(request)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )


@pytest.mark.asyncio
async def test_openrouter_tuning_change_fails_before_any_http() -> None:
    async def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("invocation tuning must match the immutable inspected selection")

    runtime = _runtime(
        httpx.MockTransport(unexpected),
        tuning=ImageTuning(width=512, height=512),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await runtime.generate_image("private prompt", tuning=ImageTuning())

    assert raised.value.error.kind is ProviderErrorKind.CAPABILITY_MISSING


@pytest.mark.asyncio
async def test_openrouter_current_missing_size_calls_zero_generation() -> None:
    paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v1/images/models":
            return httpx.Response(200, json=_model_catalog())
        if request.url.path.endswith("/endpoints"):
            return httpx.Response(
                200,
                json=_endpoint_catalog(parameters=_parameters(include_size=False)),
            )
        pytest.fail("current catalog contract cannot prove exact size")

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is ProviderErrorKind.CAPABILITY_MISSING
    assert paths == [
        "/api/v1/images/models",
        "/api/v1/images/models/author/image-exact/endpoints",
    ]


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


@pytest.mark.asyncio
async def test_openrouter_catalog_rejects_declared_oversized_body_without_reading() -> (
    None
):
    module = importlib.import_module("services.providers.openrouter_images")
    stream = _Chunks(json.dumps(_model_catalog()).encode())

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(module.DISCOVERY_BODY_LIMIT + 1)},
            stream=stream,
        )

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models[_MODEL].error is ProviderErrorKind.PROTOCOL_ERROR
    assert stream.yielded == 0


@pytest.mark.asyncio
async def test_openrouter_catalog_normalizes_deeply_nested_json() -> None:
    deeply_nested_json = b"[" * 100_000 + b"]" * 100_000

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=deeply_nested_json)

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models[_MODEL].error is ProviderErrorKind.PROTOCOL_ERROR


@pytest.mark.asyncio
async def test_openrouter_generation_bounds_observed_body_before_json() -> None:
    module = importlib.import_module("services.providers.openrouter_images")
    stream = _Chunks(
        b"x" * module.IMAGE_BODY_LIMIT,
        b"y",
        b"private-tail-that-must-not-be-read",
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await _ready_response(request)
        return httpx.Response(200, headers={"Content-Length": "1"}, stream=stream)

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert stream.yielded == 2
    assert "private-tail-that-must-not-be-read" not in repr(raised.value)


@pytest.mark.asyncio
async def test_openrouter_oversized_error_body_is_protocol_error() -> None:
    stream = _Chunks(
        b"x" * ERROR_BODY_LIMIT,
        b"y",
        b"private-tail-that-must-not-be-read",
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return await _ready_response(request)
        return httpx.Response(401, stream=stream)

    with pytest.raises(ProviderInvocationError) as raised:
        await _runtime(httpx.MockTransport(respond)).generate_image(
            "private prompt",
            tuning=ImageTuning(),
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert stream.yielded == 2
