import asyncio
import importlib
import json

import httpx
import pytest

from core.logging import cause_chain
from domain.model_runtime import (
    AgentRole,
    Capability,
    ImageOutputFormat,
    ImageQuality,
    ImageTuning,
    ProviderErrorKind,
)
from services.provider_errors import ProviderInvocationError


def test_ollama_image_runtime_is_available() -> None:
    module = importlib.import_module("services.providers.ollama_images")

    assert callable(getattr(module, "OllamaImageRuntime", None))


def _runtime(
    handler: httpx.MockTransport,
    *,
    capabilities: set[Capability] | frozenset[Capability] = frozenset(
        {Capability.IMAGE_OUTPUT}
    ),
    tuning: ImageTuning | None = None,
) -> object:
    module = importlib.import_module("services.providers.ollama_images")
    return module.OllamaImageRuntime(
        endpoint="http://ollama.test",
        model="vendor/image:exact",
        configured_capabilities=capabilities,
        tuning=tuning or ImageTuning(),
        transport=handler,
    )


@pytest.mark.asyncio
async def test_ollama_image_discovery_uses_exact_sequence_and_caches_tuning_gate() -> (
    None
):
    calls: list[tuple[str, str, object | None]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.5"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "vendor/image:exact"},
                        {"name": "unconfigured:latest"},
                    ]
                },
            )
        return httpx.Response(200, json={"capabilities": ["image"]})

    runtime = _runtime(httpx.MockTransport(respond))

    first, second = await asyncio.gather(runtime.inspect(), runtime.inspect())

    assert first is not second
    discovered = first.models["vendor/image:exact"]
    assert discovered.available is False
    assert discovered.capabilities == (Capability.IMAGE_OUTPUT,)
    assert discovered.supported_parameters == ("height", "width")
    assert discovered.error is ProviderErrorKind.CAPABILITY_MISSING
    assert first.available_models == ()
    assert calls == [
        ("GET", "/api/version", None),
        ("GET", "/api/tags", None),
        ("POST", "/api/show", {"model": "vendor/image:exact"}),
    ]

    first.models["vendor/image:exact"] = first.models["vendor/image:exact"].model_copy(
        update={"error": ProviderErrorKind.PROTOCOL_ERROR}
    )
    later = await runtime.inspect()
    assert (
        later.models["vendor/image:exact"].error is ProviderErrorKind.CAPABILITY_MISSING
    )
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_ollama_current_contract_fails_before_generate() -> None:
    paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.5"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "vendor/image:exact"}]},
            )
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["image"]})
        pytest.fail("the audited Ollama contract cannot satisfy quality/format")

    runtime = _runtime(httpx.MockTransport(respond))

    with pytest.raises(ProviderInvocationError) as raised:
        await runtime.generate_image("private prompt", tuning=ImageTuning())

    assert raised.value.error.kind is ProviderErrorKind.CAPABILITY_MISSING
    assert raised.value.error.role is AgentRole.IMAGE_GENERATOR
    assert paths == ["/api/version", "/api/tags", "/api/show"]


@pytest.mark.asyncio
async def test_ollama_missing_configured_image_capability_uses_zero_http() -> None:
    async def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid configuration must fail before provider discovery")

    result = await _runtime(
        httpx.MockTransport(unexpected),
        capabilities=frozenset(),
    ).inspect()

    discovered = result.models["vendor/image:exact"]
    assert discovered.available is False
    assert discovered.error is ProviderErrorKind.CAPABILITY_MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.14.0", ProviderErrorKind.CAPABILITY_MISSING),
        ("0.20.0", ProviderErrorKind.CAPABILITY_MISSING),
        ("0.32.4", ProviderErrorKind.CAPABILITY_MISSING),
        ("0.32.6", ProviderErrorKind.CAPABILITY_MISSING),
        ("1.0.0", ProviderErrorKind.CAPABILITY_MISSING),
        ("0.32.5-rc.1", ProviderErrorKind.PROTOCOL_ERROR),
        ("v0.32.5", ProviderErrorKind.PROTOCOL_ERROR),
        ("0.32", ProviderErrorKind.PROTOCOL_ERROR),
        (" 0.32.5", ProviderErrorKind.PROTOCOL_ERROR),
        ("01.32.5", ProviderErrorKind.PROTOCOL_ERROR),
    ],
)
async def test_ollama_rejects_unreviewed_or_unstable_versions(
    version: str,
    expected: ProviderErrorKind,
) -> None:
    paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"version": version})

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models["vendor/image:exact"].error is expected
    assert paths == ["/api/version"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tags", "show", "expected", "paths"),
    [
        ({"models": []}, None, ProviderErrorKind.CAPABILITY_MISSING, 2),
        (
            {"models": [{"name": "vendor/image:other"}]},
            None,
            ProviderErrorKind.CAPABILITY_MISSING,
            2,
        ),
        (
            {"models": [{"name": "vendor/image:exact"}]},
            {"capabilities": ["completion"]},
            ProviderErrorKind.CAPABILITY_MISSING,
            3,
        ),
        (
            {"models": [{"name": "vendor/image:exact"}]},
            {"capabilities": ["image", 7]},
            ProviderErrorKind.PROTOCOL_ERROR,
            3,
        ),
        ({"models": [7]}, None, ProviderErrorKind.PROTOCOL_ERROR, 2),
        ({"models": "bad"}, None, ProviderErrorKind.PROTOCOL_ERROR, 2),
        (
            {
                "models": [
                    {"name": "vendor/image:exact"},
                    {"name": "vendor/image:exact"},
                ]
            },
            None,
            ProviderErrorKind.PROTOCOL_ERROR,
            2,
        ),
    ],
)
async def test_ollama_requires_exact_tag_and_literal_image_capability(
    tags: object,
    show: object | None,
    expected: ProviderErrorKind,
    paths: int,
) -> None:
    seen: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.5"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags)
        return httpx.Response(200, json=show)

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models["vendor/image:exact"].error is expected
    assert len(seen) == paths
    assert not any(path in {"/api/pull", "/api/copy", "/api/create"} for path in seen)


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


def _synthetic_contract() -> object:
    module = importlib.import_module("services.providers.ollama_images")
    return module._NativeImageContract(
        request_parameters=frozenset({"width", "height"}),
        qualities=frozenset({ImageQuality.MEDIUM}),
        output_formats=frozenset({ImageOutputFormat.WEBP}),
    )


@pytest.mark.asyncio
async def test_ollama_synthetic_contract_uses_only_native_buffered_request() -> None:
    module = importlib.import_module("services.providers.ollama_images")
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "vendor/image:exact",
                "done": True,
                "image": "cHJvdmlkZXItaW1hZ2U=",
                "prompt_eval_count": 11,
                "eval_count": 3,
            },
        )

    result = await module._invoke_native_image(
        endpoint="http://ollama.test",
        model="vendor/image:exact",
        prompt="protected prompt",
        tuning=ImageTuning(width=640, height=768, timeout_seconds=9),
        contract=_synthetic_contract(),
        transport=httpx.MockTransport(respond),
    )

    assert result.output.base64_data == "cHJvdmlkZXItaW1hZ2U="
    assert result.output.media_type is None
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (11, 3)
    assert len(requests) == 1
    request = requests[0]
    assert request.url == httpx.URL("http://ollama.test/api/generate")
    assert json.loads(request.content) == {
        "model": "vendor/image:exact",
        "prompt": "protected prompt",
        "width": 640,
        "height": 768,
        "stream": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"model": "vendor/image:other", "done": True, "image": "YWJj"},
        {"model": "vendor/image:exact", "done": False, "image": "YWJj"},
        {"model": "vendor/image:exact", "image": "YWJj"},
        {"model": "vendor/image:exact", "done": True},
        {"model": "vendor/image:exact", "done": True, "image": ""},
        {"model": "vendor/image:exact", "done": True, "image": 7},
        {
            "model": "vendor/image:exact",
            "done": True,
            "image": "YWJj",
            "prompt_eval_count": 1,
        },
        {
            "model": "vendor/image:exact",
            "done": True,
            "image": "YWJj",
            "prompt_eval_count": True,
            "eval_count": 1,
        },
    ],
)
async def test_ollama_generation_rejects_malformed_success(body: object) -> None:
    module = importlib.import_module("services.providers.ollama_images")

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(ProviderInvocationError) as raised:
        await module._invoke_native_image(
            endpoint="http://ollama.test",
            model="vendor/image:exact",
            prompt="protected prompt",
            tuning=ImageTuning(),
            contract=_synthetic_contract(),
            transport=httpx.MockTransport(respond),
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (307, ProviderErrorKind.PROTOCOL_ERROR, False),
        (400, ProviderErrorKind.PROTOCOL_ERROR, False),
        (404, ProviderErrorKind.CAPABILITY_MISSING, False),
        (429, ProviderErrorKind.RATE_LIMITED, True),
        (500, ProviderErrorKind.UNAVAILABLE, True),
        (503, ProviderErrorKind.UNAVAILABLE, True),
    ],
)
async def test_ollama_generation_maps_status_without_retry(
    status: int,
    expected: ProviderErrorKind,
    retryable: bool,
) -> None:
    module = importlib.import_module("services.providers.ollama_images")
    calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            headers={"Location": "http://redirect.invalid/private"},
            json={"error": "private-provider-body"},
        )

    with pytest.raises(ProviderInvocationError) as raised:
        await module._invoke_native_image(
            endpoint="http://ollama.test",
            model="vendor/image:exact",
            prompt="private prompt",
            tuning=ImageTuning(),
            contract=_synthetic_contract(),
            transport=httpx.MockTransport(respond),
        )

    assert raised.value.error.kind is expected
    assert raised.value.error.retryable is retryable
    assert calls == 1
    assert "private-provider-body" not in repr(raised.value)


@pytest.mark.asyncio
async def test_ollama_generation_timeout_is_detached_and_not_replayed() -> None:
    module = importlib.import_module("services.providers.ollama_images")
    calls = 0

    async def fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(
            "private prompt | raw body | image canary",
            request=request,
        )

    with pytest.raises(ProviderInvocationError) as raised:
        await module._invoke_native_image(
            endpoint="http://ollama.test",
            model="vendor/image:exact",
            prompt="private prompt",
            tuning=ImageTuning(),
            contract=_synthetic_contract(),
            transport=httpx.MockTransport(fail),
        )

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert calls == 1
    assert cause_chain(raised.value) == [
        "ProviderInvocationError: The model operation timed out."
    ]


@pytest.mark.asyncio
async def test_ollama_generation_cancellation_propagates() -> None:
    module = importlib.import_module("services.providers.ollama_images")

    async def cancel(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await module._invoke_native_image(
            endpoint="http://ollama.test",
            model="vendor/image:exact",
            prompt="private prompt",
            tuning=ImageTuning(),
            contract=_synthetic_contract(),
            transport=httpx.MockTransport(cancel),
        )


@pytest.mark.asyncio
async def test_ollama_discovery_rejects_declared_oversized_body_without_reading() -> (
    None
):
    module = importlib.import_module("services.providers.ollama_images")
    stream = _Chunks(b'{"version":"0.32.5"}')

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(module.DISCOVERY_BODY_LIMIT + 1)},
            stream=stream,
        )

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models["vendor/image:exact"].error is ProviderErrorKind.PROTOCOL_ERROR
    assert stream.yielded == 0


@pytest.mark.asyncio
async def test_ollama_discovery_bounds_observed_decompressed_body() -> None:
    module = importlib.import_module("services.providers.ollama_images")
    stream = _Chunks(
        b"x" * module.DISCOVERY_BODY_LIMIT,
        b"y",
        b"private-tail-that-must-not-be-read",
    )

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "1"},
            stream=stream,
        )

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models["vendor/image:exact"].error is ProviderErrorKind.PROTOCOL_ERROR
    assert stream.yielded == 2


@pytest.mark.asyncio
async def test_ollama_discovery_normalizes_deeply_nested_json() -> None:
    deeply_nested_json = b"[" * 100_000 + b"]" * 100_000

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=deeply_nested_json)

    result = await _runtime(httpx.MockTransport(respond)).inspect()

    assert result.models["vendor/image:exact"].error is ProviderErrorKind.PROTOCOL_ERROR


@pytest.mark.asyncio
async def test_ollama_generation_bounds_observed_body_before_json() -> None:
    module = importlib.import_module("services.providers.ollama_images")
    stream = _Chunks(
        b"x" * module.IMAGE_BODY_LIMIT,
        b"y",
        b"private-tail-that-must-not-be-read",
    )

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with pytest.raises(ProviderInvocationError) as raised:
        await module._invoke_native_image(
            endpoint="http://ollama.test",
            model="vendor/image:exact",
            prompt="private prompt",
            tuning=ImageTuning(),
            contract=_synthetic_contract(),
            transport=httpx.MockTransport(respond),
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert stream.yielded == 2
    assert "private-tail-that-must-not-be-read" not in repr(raised.value)
