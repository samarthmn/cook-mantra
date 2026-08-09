import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

import services.providers as providers
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
from services.providers.openrouter import OpenRouterTextVisionAdapter


class Answer(BaseModel):
    ingredient: str


def test_openrouter_text_vision_adapter_is_available() -> None:
    assert callable(getattr(providers, "OpenRouterTextVisionAdapter", None))


def test_openrouter_model_discovery_is_available() -> None:
    assert callable(getattr(providers, "OpenRouterModelDiscovery", None))


@pytest.mark.asyncio
async def test_openrouter_structured_vision_uses_strict_shape_and_maps_usage() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"ingredient":"Tomato"}',
                        }
                    }
                ],
                "usage": {"prompt_tokens": 23, "completion_tokens": 7},
            },
        )

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret-bearer-canary",
        model="vendor/vision-exact",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(
            temperature=0.2,
            reasoning_effort="low",
            max_output_tokens=456,
            timeout_seconds=8,
        ),
        supported_parameters={
            "temperature",
            "reasoning",
            "max_tokens",
            "structured_outputs",
        },
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
    assert (result.usage.input_tokens, result.usage.output_tokens) == (23, 7)
    request = requests[0]
    assert request.url == httpx.URL("https://openrouter.test/api/v1/chat/completions")
    assert request.headers["authorization"] == "Bearer secret-bearer-canary"
    assert json.loads(request.content) == {
        "model": "vendor/vision-exact",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Identify visible food."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aW1hZ2UtY2FuYXJ5"},
                    },
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "Answer",
                "strict": True,
                "schema": Answer.model_json_schema(),
            },
        },
        "provider": {"allow_fallbacks": False, "require_parameters": True},
        "temperature": 0.2,
        "max_tokens": 456,
        "reasoning": {"effort": "low"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [
        (401, None, ProviderErrorKind.AUTHENTICATION_FAILED),
        (402, None, ProviderErrorKind.PAYMENT_REQUIRED),
        (429, None, ProviderErrorKind.RATE_LIMITED),
        (502, None, ProviderErrorKind.UNAVAILABLE),
        (503, None, ProviderErrorKind.UNAVAILABLE),
        (400, "provider_timeout", ProviderErrorKind.TIMED_OUT),
    ],
)
async def test_openrouter_errors_are_normalized_and_redacted(
    status: int,
    error_type: str | None,
    expected: ProviderErrorKind,
) -> None:
    secret = "secret-bearer-canary"
    raw = "raw-provider-body-canary"

    async def respond(_: httpx.Request) -> httpx.Response:
        metadata = {"raw": raw}
        if error_type is not None:
            metadata["error_type"] = error_type
        return httpx.Response(
            status,
            json={"error": {"message": raw, "metadata": metadata}},
        )

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key=secret,
        model="vendor/text-exact",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is expected
    assert secret not in repr(raised.value)
    assert raw not in repr(raised.value)


@pytest.mark.asyncio
async def test_openrouter_discovery_filters_exact_models_and_caches_once() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vendor/vision",
                        "architecture": {
                            "input_modalities": ["text", "image"],
                            "output_modalities": ["text"],
                        },
                        "supported_parameters": [
                            "temperature",
                            "max_tokens",
                            "reasoning",
                            "structured_outputs",
                        ],
                        "context_length": 131072,
                    },
                    {
                        "id": "vendor/unconfigured",
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                        },
                        "supported_parameters": ["structured_outputs"],
                        "context_length": 4096,
                    },
                ]
            },
        )

    discovery = providers.OpenRouterModelDiscovery(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret-discovery-canary",
        configured_models={"vendor/vision"},
        timeout_seconds=5,
        transport=httpx.MockTransport(respond),
    )

    first, second = await asyncio.gather(discovery.inspect(), discovery.inspect())

    assert first is not second
    assert first.available_models == ("vendor/vision",)
    model = first.models["vendor/vision"]
    assert model.capabilities == (
        Capability.STRUCTURED_OUTPUT,
        Capability.TEXT,
        Capability.VISION,
    )
    assert model.supported_parameters == (
        "max_tokens",
        "reasoning",
        "structured_outputs",
        "temperature",
    )
    assert model.context_window == 131072
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://openrouter.test/api/v1/models")
    assert requests[0].headers["authorization"] == "Bearer secret-discovery-canary"
    first.models["vendor/vision"] = DiscoveredModel(
        model="vendor/vision",
        available=False,
        error=ProviderErrorKind.PROTOCOL_ERROR,
    )
    later = await discovery.inspect()
    assert later.models["vendor/vision"].available is True
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_openrouter_discovery_narrows_missing_structured_output_capability() -> (
    None
):
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vendor/text",
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                        },
                        "supported_parameters": ["temperature"],
                        "context_length": 8192,
                    }
                ]
            },
        )

    result = await providers.OpenRouterModelDiscovery(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        configured_models={"vendor/text"},
        timeout_seconds=5,
        transport=httpx.MockTransport(respond),
    ).inspect()

    assert result.models["vendor/text"].capabilities == (Capability.TEXT,)


@pytest.mark.asyncio
async def test_malformed_openrouter_error_body_still_uses_http_status() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"not-json-private-body")

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.AUTHENTICATION_FAILED
    assert "not-json-private-body" not in repr(raised.value)


@pytest.mark.asyncio
async def test_unknown_openrouter_error_metadata_falls_back_to_http_status() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "private-provider-message",
                    "metadata": {"error_type": "new_unknown_vendor_error"},
                }
            },
        )

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.AUTHENTICATION_FAILED


@pytest.mark.asyncio
async def test_unknown_openrouter_error_metadata_in_success_is_protocol_error() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": {
                    "message": "private-provider-message",
                    "metadata": {"error_type": "new_unknown_vendor_error"},
                }
            },
        )

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR


@pytest.mark.asyncio
async def test_openrouter_cancellation_propagates() -> None:
    async def cancel(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(cancel),
    )

    with pytest.raises(asyncio.CancelledError):
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)


@pytest.mark.asyncio
async def test_openrouter_raw_success_body_is_not_retained_as_exception_cause() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"raw-response-canary")

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.PROTOCOL_ERROR
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "raw-response-canary" not in repr(raised.value)


@pytest.mark.asyncio
async def test_openrouter_transport_failure_retains_only_normalized_safe_error() -> (
    None
):
    canaries = (
        "Bearer secret-bearer-canary",
        "prompt-canary-private-meal",
        "invalid-provider-value-canary",
        "data:image/png;base64,aW1hZ2UtY2FuYXJ5",
    )

    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(" | ".join(canaries), request=request)

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret-bearer-canary",
        model="vendor/vision",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke(
            [
                ModelMessage(
                    role="user",
                    content="prompt-canary-private-meal",
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
    assert all(canary not in str(cause_chain(error)) for canary in canaries)


@pytest.mark.asyncio
async def test_openrouter_timeout_failure_is_fully_detached() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("prompt-timeout-canary", request=request)

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret-timeout-canary",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.TIMED_OUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_openrouter_invalid_output_failure_is_fully_detached() -> None:
    invalid_value = "invalid-provider-value-detachment-canary"

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"ingredient": [invalid_value]})
                        }
                    }
                ]
            },
        )

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.test/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await adapter.invoke([ModelMessage(role="user", content="Cook.")], Answer)

    assert raised.value.error.kind is ProviderErrorKind.INVALID_OUTPUT
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert invalid_value not in str(cause_chain(raised.value))


@pytest.mark.asyncio
async def test_openrouter_media_invocation_and_discovery_bypass_hostile_proxy_env(
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

    monkeypatch.setattr(httpx, "AsyncClient", record_client)

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ingredient":"Tomato"}'}}]},
            )
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(respond)
    await OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.ai/api/v1",
        api_key="secret",
        model="vendor/vision",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
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
    await providers.OpenRouterModelDiscovery(
        endpoint="https://openrouter.ai/api/v1",
        api_key="secret",
        configured_models={"vendor/vision"},
        timeout_seconds=4,
        media_bearing=True,
        transport=transport,
    ).inspect()

    assert len(created) == 2
    assert all(client.trust_env is False for client in created)
    assert all(client.follow_redirects is False for client in created)


@pytest.mark.asyncio
async def test_openrouter_non_media_clients_keep_environment_proxy_flexibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def record_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client = real_client(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", record_client)

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ingredient":"Tomato"}'}}]},
            )
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(respond)
    await OpenRouterTextVisionAdapter(
        endpoint="https://proxy-compatible.invalid/api/v1",
        api_key="secret",
        model="vendor/text",
        role=AgentRole.MASTER_CHEF,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
        transport=transport,
    ).invoke([ModelMessage(role="user", content="Cook.")], Answer)
    await providers.OpenRouterModelDiscovery(
        endpoint="https://proxy-compatible.invalid/api/v1",
        api_key="secret",
        configured_models={"vendor/text"},
        timeout_seconds=4,
        transport=transport,
    ).inspect()

    assert len(created) == 2
    assert all(client.trust_env is True for client in created)
    assert all(client.follow_redirects is False for client in created)


@pytest.mark.asyncio
async def test_openrouter_media_invocation_does_not_follow_redirect() -> None:
    calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            307,
            headers={"Location": "https://redirect.invalid/private"},
        )

    adapter = OpenRouterTextVisionAdapter(
        endpoint="https://openrouter.ai/api/v1",
        api_key="secret",
        model="vendor/vision",
        role=AgentRole.INGREDIENT_EXTRACTOR,
        tuning=TextTuning(),
        supported_parameters={"temperature", "structured_outputs"},
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
