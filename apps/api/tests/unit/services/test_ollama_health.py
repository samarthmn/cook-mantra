import httpx
import pytest

from core.config import Model
from core.errors import AppError, ErrorCode
from services.ollama_health import OllamaHealthService

ALL_REQUIRED_MODELS = [model.value for model in Model]
TEXT_MODELS = [model.value for model in Model if model is not Model.Z_IMAGE]


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
    installed = [Model.QWEN_SMALL.value, Model.GPT_OSS.value, "custom:latest"]

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
        "missing": list(
            dict.fromkeys(
                model.value
                for model in (
                    Model.QWEN_LARGE,
                    Model.GEMMA_LARGE,
                    Model.Z_IMAGE,
                )
                if model.value not in installed
            )
        ),
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
async def test_inspect_checks_each_model_on_its_designated_host() -> None:
    requests: list[str] = []
    text_model_on_wrong_host = TEXT_MODELS[0]
    text_inventory = [
        *TEXT_MODELS[1:],
        "shared:latest",
        "text-extra:latest",
    ]
    image_inventory = [
        "shared:latest",
        text_model_on_wrong_host,
        Model.Z_IMAGE.value,
        "image-extra:latest",
    ]

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        inventory = (
            text_inventory
            if request.url.host == "text-ollama.test"
            else image_inventory
        )
        return httpx.Response(
            200,
            json={"models": [model_tag(name) for name in inventory]},
        )

    service = OllamaHealthService(
        "http://text-ollama.test:11434",
        image_base_url="http://image-ollama.test:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    result = await service.inspect()

    assert requests == [
        "http://text-ollama.test:11434/api/tags",
        "http://image-ollama.test:11434/api/tags",
    ]
    assert result == {
        "reachable": True,
        "available_models": [
            *text_inventory,
            text_model_on_wrong_host,
            Model.Z_IMAGE.value,
            "image-extra:latest",
        ],
        "missing": [text_model_on_wrong_host],
    }


@pytest.mark.asyncio
async def test_inspect_queries_equal_hosts_only_once() -> None:
    requests: list[str] = []
    installed = list(dict.fromkeys([*TEXT_MODELS, Model.Z_IMAGE.value]))

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={"models": [model_tag(name) for name in installed]},
        )

    service = OllamaHealthService(
        "http://ollama.local:11434",
        image_base_url="http://ollama.local:11434/",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    result = await service.inspect()

    assert requests == ["http://ollama.local:11434/api/tags"]
    assert result == {
        "reachable": True,
        "available_models": installed,
        "missing": [],
    }


@pytest.mark.asyncio
async def test_unreachable_image_host_is_reported_as_unavailable() -> None:
    requests: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "image-ollama.test":
            raise httpx.ConnectError("image host refused connection", request=request)
        return httpx.Response(
            200,
            json={"models": [model_tag(name) for name in TEXT_MODELS]},
        )

    service = OllamaHealthService(
        "http://text-ollama.test:11434",
        image_base_url="http://image-ollama.test:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(AppError) as raised:
        await service.inspect()

    assert requests == [
        "http://text-ollama.test:11434/api/tags",
        "http://image-ollama.test:11434/api/tags",
    ]
    assert raised.value.code is ErrorCode.OLLAMA_UNAVAILABLE
    assert raised.value.message == "Ollama is unavailable."
    assert raised.value.status_code == 503
    assert raised.value.retryable is True


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
