import httpx
import pytest

from core.config import Model
from core.errors import AppError, ErrorCode
from services.ollama_health import OllamaHealthService

ALL_REQUIRED_MODELS = [model.value for model in Model]


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
async def test_inspect_uses_one_text_tags_request_and_compares_four_models() -> None:
    requests: list[str] = []
    installed = [Model.QWEN_SMALL.value, Model.GPT_OSS.value, "custom:latest"]

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
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

    assert requests == ["http://ollama.local:11434/api/tags"]
    assert result == {
        "reachable": True,
        "available_models": installed,
        "missing": [Model.QWEN_LARGE.value, Model.GEMMA_LARGE.value],
    }


@pytest.mark.asyncio
async def test_inspect_returns_the_complete_text_inventory() -> None:
    inventory = [*reversed(ALL_REQUIRED_MODELS), "extra:latest"]

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"models": [model_tag(name) for name in inventory]},
        )

    service = OllamaHealthService(
        "http://ollama.local:11434/",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    assert await service.inspect() == {
        "reachable": True,
        "available_models": inventory,
        "missing": [],
    }


def test_constructor_has_no_retired_beast_health_endpoint() -> None:
    with pytest.raises(TypeError, match="beast_base_url"):
        OllamaHealthService(
            "http://ollama.local:11434",
            timeout_seconds=1,
            beast_base_url="http://retired.test:4900",  # type: ignore[call-arg]
        )


@pytest.mark.asyncio
async def test_connection_details_are_hidden_behind_the_ollama_error() -> None:
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
async def test_malformed_tags_response_is_reported_as_ollama_unavailable() -> None:
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
