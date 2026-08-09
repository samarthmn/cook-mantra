import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from core.logging import cause_chain
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ModelMessage,
    ProviderErrorKind,
    TextTuning,
)
from services.provider_errors import ProviderInvocationError
from services.providers import ollama


class Answer(BaseModel):
    ingredient: str


def test_native_ollama_text_vision_adapter_is_available() -> None:
    assert callable(getattr(ollama, "OllamaTextVisionAdapter", None))


def test_native_ollama_model_discovery_is_available() -> None:
    assert callable(getattr(ollama, "OllamaModelDiscovery", None))


@pytest.mark.asyncio
async def test_ollama_structured_vision_uses_native_shape_and_maps_usage() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": '{"ingredient":"Tomato"}'},
                "done": True,
                "prompt_eval_count": 17,
                "eval_count": 5,
            },
        )

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="vision-exact:1",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(
            temperature=0.25,
            reasoning_effort="none",
            max_output_tokens=321,
            context_window=8192,
            timeout_seconds=7,
        ),
        transport=httpx.MockTransport(respond),
    )

    result = await adapter.invoke(
        [
            ModelMessage(
                role="user",
                content="Identify visible food.",
                image=b"image-canary",
                media_type="image/png",
            )
        ],
        Answer,
    )

    assert result.output == Answer(ingredient="Tomato")
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (17, 5)
    assert len(requests) == 1
    request = requests[0]
    assert request.url == httpx.URL("http://ollama.test/api/chat")
    assert json.loads(request.content) == {
        "model": "vision-exact:1",
        "messages": [
            {
                "role": "user",
                "content": "Identify visible food.",
                "images": ["aW1hZ2UtY2FuYXJ5"],
            }
        ],
        "format": Answer.model_json_schema(),
        "stream": False,
        "options": {
            "temperature": 0.25,
            "num_predict": 321,
            "num_ctx": 8192,
        },
        "think": False,
    }


@pytest.mark.asyncio
async def test_ollama_cancellation_propagates_without_normalization() -> None:
    async def cancel(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="text-exact:1",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        transport=httpx.MockTransport(cancel),
    )

    with pytest.raises(asyncio.CancelledError):
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)


@pytest.mark.asyncio
async def test_ollama_discovery_inspects_exact_models_once_and_maps_capabilities() -> (
    None
):
    calls: list[tuple[str, str, object | None]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "vision:1"},
                        {"name": "text:1"},
                        {"name": "unconfigured:1"},
                    ]
                },
            )
        assert body is not None
        if body["model"] == "vision:1":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        return httpx.Response(200, json={"capabilities": ["completion"]})

    discovery = ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"vision:1", "text:1"},
        timeout_seconds=4,
        transport=httpx.MockTransport(respond),
    )

    first, second = await asyncio.gather(discovery.inspect(), discovery.inspect())

    assert first is not second
    assert first.available_models == ("text:1", "vision:1")
    assert first.models["vision:1"].capabilities == (
        Capability.STRUCTURED_OUTPUT,
        Capability.TEXT,
        Capability.VISION,
    )
    assert first.models["text:1"].capabilities == (
        Capability.STRUCTURED_OUTPUT,
        Capability.TEXT,
    )
    assert calls == [
        ("GET", "/api/version", None),
        ("GET", "/api/tags", None),
        ("POST", "/api/show", {"model": "text:1"}),
        ("POST", "/api/show", {"model": "vision:1"}),
    ]

    first.models["text:1"] = DiscoveredModel(
        model="text:1",
        available=False,
        error=ProviderErrorKind.PROTOCOL_ERROR,
    )
    later = await discovery.inspect()
    assert later.models["text:1"].available is True
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_ollama_discovery_marks_absent_model_without_pulling() -> None:
    paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        return httpx.Response(200, json={"models": []})

    result = await ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"missing:1"},
        timeout_seconds=4,
        transport=httpx.MockTransport(respond),
    ).inspect()

    assert result.models["missing:1"].available is False
    assert result.models["missing:1"].error is ProviderErrorKind.CAPABILITY_MISSING
    assert paths == ["/api/version", "/api/tags"]


@pytest.mark.asyncio
async def test_ollama_discovery_isolates_each_installed_model_show_failure() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "healthy:1"},
                        {"name": "missing-show:1"},
                        {"name": "malformed-show:1"},
                    ]
                },
            )
        model = json.loads(request.content)["model"]
        if model == "missing-show:1":
            return httpx.Response(404, json={"error": "private missing detail"})
        if model == "malformed-show:1":
            return httpx.Response(200, json={"capabilities": ["completion", 7]})
        return httpx.Response(200, json={"capabilities": ["completion"]})

    result = await ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"healthy:1", "missing-show:1", "malformed-show:1"},
        timeout_seconds=4,
        transport=httpx.MockTransport(respond),
    ).inspect()

    assert result.available_models == (
        "healthy:1",
        "malformed-show:1",
        "missing-show:1",
    )
    assert result.models["healthy:1"].available is True
    assert result.models["missing-show:1"].error is ProviderErrorKind.CAPABILITY_MISSING
    assert result.models["malformed-show:1"].error is ProviderErrorKind.PROTOCOL_ERROR


@pytest.mark.asyncio
async def test_ollama_discovery_accepts_installed_gpt_oss_capability_payload() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gpt-oss:20b"}]})
        return httpx.Response(
            200,
            json={
                "capabilities": ["completion", "tools", "thinking"],
                "details": {
                    "format": "gguf",
                    "family": "gptoss",
                    "parameter_size": "20.9B",
                    "quantization_level": "MXFP4",
                },
                "model_info": {"gpt-oss.context_length": 131072},
            },
        )

    result = await ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"gpt-oss:20b"},
        timeout_seconds=4,
        transport=httpx.MockTransport(respond),
    ).inspect()

    model = result.models["gpt-oss:20b"]
    assert model.available is True
    assert model.capabilities == (Capability.STRUCTURED_OUTPUT, Capability.TEXT)
    assert model.supported_parameters == ("reasoning",)


@pytest.mark.asyncio
async def test_ollama_discovery_rejects_non_string_capability_data() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "text:1"}]})
        return httpx.Response(200, json={"capabilities": ["completion", 3]})

    result = await ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"text:1"},
        timeout_seconds=4,
        transport=httpx.MockTransport(respond),
    ).inspect()

    assert result.models["text:1"].available is False
    assert result.models["text:1"].error is ProviderErrorKind.PROTOCOL_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, ProviderErrorKind.PROTOCOL_ERROR),
        (429, ProviderErrorKind.RATE_LIMITED),
        (500, ProviderErrorKind.UNAVAILABLE),
    ],
)
async def test_ollama_discovery_normalizes_api_surface_failures(
    status: int,
    expected: ProviderErrorKind,
) -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "raw-private-provider-detail"})

    result = await ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"text:1"},
        timeout_seconds=4,
        transport=httpx.MockTransport(respond),
    ).inspect()

    assert result.models["text:1"].error is expected
    assert "raw-private-provider-detail" not in repr(result)


@pytest.mark.asyncio
async def test_ollama_malformed_usage_retains_safe_role_context() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"ingredient":"Tomato"}'},
                "prompt_eval_count": "seventeen",
                "eval_count": 5,
            },
        )

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="text:1",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.error.role is AgentRole.MASTER_CHEF


@pytest.mark.asyncio
async def test_ollama_raw_success_body_is_not_retained_as_exception_cause() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"raw-response-canary")

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="text:1",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "raw-response-canary" not in repr(raised.value)


@pytest.mark.asyncio
async def test_ollama_transport_failure_retains_only_normalized_safe_error() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "prompt-canary | invalid-provider-value-canary | "
            "data:image/png;base64,aW1hZ2UtY2FuYXJ5",
            request=request,
        )

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="vision:1",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(),
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke(
            [
                ModelMessage(
                    role="user",
                    content="prompt-canary",
                    image=b"image-canary",
                    media_type="image/png",
                )
            ],
            Answer,
        )

    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert cause_chain(error) == [
        "ProviderInvocationError: The selected model provider is unavailable."
    ]


@pytest.mark.asyncio
async def test_ollama_timeout_failure_is_fully_detached() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("prompt-timeout-canary", request=request)

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="text:1",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_ollama_invalid_output_failure_is_fully_detached() -> None:
    invalid_value = "invalid-provider-value-detachment-canary"

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps({"ingredient": [invalid_value]}),
                }
            },
        )

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="text:1",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.INVALID_OUTPUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert invalid_value not in str(cause_chain(raised.value))


@pytest.mark.asyncio
async def test_ollama_media_invocation_and_discovery_bypass_hostile_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://proxy.invalid:8080")
    real_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def record_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client = real_client(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(ollama.httpx, "AsyncClient", record_client)

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(
                200, json={"message": {"content": '{"ingredient":"Tomato"}'}}
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        pytest.fail("unexpected provider path")

    transport = httpx.MockTransport(respond)
    await ollama.OllamaTextVisionAdapter(
        endpoint="http://127.0.0.1:11434",
        model="vision:1",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(),
        transport=transport,
    ).invoke(
        [
            ModelMessage(
                role="user",
                content="Identify food.",
                image=b"image",
                media_type="image/png",
            )
        ],
        Answer,
    )
    await ollama.OllamaModelDiscovery(
        endpoint="http://127.0.0.1:11434",
        configured_models={"vision:1"},
        timeout_seconds=4,
        media_bearing=True,
        transport=transport,
    ).inspect()

    assert len(created) == 2
    assert all(client.trust_env is False for client in created)
    assert all(client.follow_redirects is False for client in created)


@pytest.mark.asyncio
async def test_ollama_non_media_clients_keep_environment_proxy_flexibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def record_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client = real_client(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(ollama.httpx, "AsyncClient", record_client)

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(
                200, json={"message": {"content": '{"ingredient":"Tomato"}'}}
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        return httpx.Response(200, json={"models": []})

    transport = httpx.MockTransport(respond)
    await ollama.OllamaTextVisionAdapter(
        endpoint="http://ollama.test",
        model="text:1",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        transport=transport,
    ).invoke([ModelMessage(role="user", content="Cook.")], Answer)
    await ollama.OllamaModelDiscovery(
        endpoint="http://ollama.test",
        configured_models={"text:1"},
        timeout_seconds=4,
        transport=transport,
    ).inspect()

    assert len(created) == 2
    assert all(client.trust_env is True for client in created)
    assert all(client.follow_redirects is False for client in created)


@pytest.mark.asyncio
async def test_ollama_media_invocation_does_not_follow_redirect() -> None:
    calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            307,
            headers={"Location": "http://redirect.invalid/private"},
        )

    adapter = ollama.OllamaTextVisionAdapter(
        endpoint="http://127.0.0.1:11434",
        model="vision:1",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke(
            [
                ModelMessage(
                    role="user",
                    content="Identify food.",
                    image=b"image",
                    media_type="image/png",
                )
            ],
            Answer,
        )

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert calls == 1
