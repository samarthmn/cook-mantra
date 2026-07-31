import httpx
import pytest

from core.errors import AppError, ErrorCode
from services.ollama_health import OllamaHealthService

ALL_REQUIRED_MODELS = [
    "qwen3.5:27b",
    "qwen3.5:9b",
    "gemma4:26b",
    "gpt-oss:20b",
    "x/z-image-turbo:fp8",
]


def model_tag(name: str) -> dict[str, object]:
    return {
        "name": name,
        "model": name,
        "modified_at": "2026-07-30T00:00:00Z",
        "size": 1,
        "digest": "sha256:digest",
        "details": {
            "parent_model": "",
            "format": "gguf",
            "family": "qwen",
            "families": ["qwen"],
            "parameter_size": "9B",
            "quantization_level": "Q4_K_M",
        },
    }


@pytest.mark.asyncio
async def test_inspect_uses_tags_without_pulling_and_compares_every_model() -> None:
    requests: list[tuple[str, str]] = []
    installed = ["qwen3.5:9b", "gpt-oss:20b", "custom:latest"]

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"models": [model_tag(name) for name in installed]},
        )

    service = OllamaHealthService(
        "http://ollama.local:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    result = await service.inspect()

    assert requests == [("GET", "/api/tags")]
    assert result == {
        "reachable": True,
        "available_models": installed,
        "missing": [
            "qwen3.5:27b",
            "gemma4:26b",
            "x/z-image-turbo:fp8",
        ],
    }


@pytest.mark.asyncio
async def test_inspect_returns_the_complete_available_inventory() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [model_tag(name) for name in reversed(ALL_REQUIRED_MODELS)]
            },
        )

    service = OllamaHealthService(
        "http://ollama.local:11434/",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    result = await service.inspect()

    assert result == {
        "reachable": True,
        "available_models": list(reversed(ALL_REQUIRED_MODELS)),
        "missing": [],
    }


@pytest.mark.asyncio
async def test_connection_details_are_hidden_behind_the_public_error() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "connection refused at private-host:11434",
            request=request,
        )

    service = OllamaHealthService(
        "http://ollama.local:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(AppError) as raised:
        await service.inspect()

    assert raised.value.code is ErrorCode.OLLAMA_UNAVAILABLE
    assert raised.value.message == "Ollama is unavailable."
    assert raised.value.status_code == 503
    assert raised.value.retryable is True
    assert raised.value.details == {}
    assert "private-host" not in raised.value.message


@pytest.mark.asyncio
async def test_malformed_tags_response_is_reported_as_unavailable() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not ollama</html>")

    service = OllamaHealthService(
        "http://ollama.local:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(AppError) as raised:
        await service.inspect()

    assert raised.value.code is ErrorCode.OLLAMA_UNAVAILABLE
    assert raised.value.status_code == 503
    assert raised.value.retryable is True
