import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

import services.model_runtime as model_runtime
from core.logging import log_context
from core.runtime_config import get_runtime_snapshot
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ModelMessage,
    ModelUsage,
    ModelUsageEvent,
    ProviderDiscoveryResult,
    ProviderErrorKind,
    ProviderName,
)
from services.provider_errors import ProviderInvocationError


class Answer(BaseModel):
    value: str


class Discovery:
    def __init__(self, result: ProviderDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    async def inspect(self) -> ProviderDiscoveryResult:
        self.calls += 1
        return self.result


def test_provider_neutral_role_model_factory_is_available() -> None:
    assert callable(getattr(model_runtime, "RuntimeStructuredModelFactory", None))


def test_bounded_model_usage_journal_is_available() -> None:
    assert callable(getattr(model_runtime, "InProcessModelUsageJournal", None))


@pytest.mark.asyncio
async def test_model_usage_journal_records_concurrently_and_keeps_bounded_tail() -> (
    None
):
    journal = model_runtime.InProcessModelUsageJournal(max_events=3)

    async def record(index: int) -> None:
        await journal.record(
            ModelUsageEvent(
                role=AgentRole.MASTER_CHEF,
                provider=ProviderName.OLLAMA,
                model=f"model-{index}",
                usage=ModelUsage(input_tokens=index, output_tokens=1),
            )
        )

    await asyncio.gather(*(record(index) for index in range(10)))

    assert [event.model for event in await journal.snapshot()] == [
        "model-7",
        "model-8",
        "model-9",
    ]


@pytest.mark.asyncio
async def test_factory_selects_exact_openrouter_role_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = config.providers[ProviderName.OLLAMA].model_copy(
        update={
            "endpoint": "https://openrouter.test/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    )
    selection = config.roles[AgentRole.MASTER_CHEF].model_copy(
        update={
            "provider": ProviderName.OPENROUTER,
            "model": "vendor/exact-model",
            "text_tuning": config.roles[AgentRole.MASTER_CHEF].text_tuning.model_copy(
                update={"context_window": None, "reasoning_effort": None}
            ),
        }
    )
    custom = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: provider,
                    },
                    "roles": {**config.roles, AgentRole.MASTER_CHEF: selection},
                }
            )
        }
    )
    requests: list[httpx.Request] = []
    traced: list[tuple[AgentRole, ProviderName, str]] = []

    async def trace_safe(*, role, provider, model, operation):
        traced.append((role, provider, model))
        return await operation()

    monkeypatch.setattr(
        model_runtime,
        "invoke_trace_safe_text_model",
        trace_safe,
        raising=False,
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"value":"ok"}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    journal = model_runtime.InProcessModelUsageJournal(max_events=10)
    factory = model_runtime.RuntimeStructuredModelFactory(
        custom,
        environ={"OPENROUTER_API_KEY": "secret"},
        transports={ProviderName.OPENROUTER: httpx.MockTransport(respond)},
        usage_journal=journal,
        discoveries={
            ProviderName.OPENROUTER: Discovery(
                ProviderDiscoveryResult(
                    provider=ProviderName.OPENROUTER,
                    models={
                        "vendor/exact-model": DiscoveredModel(
                            model="vendor/exact-model",
                            available=True,
                            capabilities=(
                                Capability.STRUCTURED_OUTPUT,
                                Capability.TEXT,
                            ),
                            supported_parameters=(
                                "structured_outputs",
                                "temperature",
                            ),
                            context_window=131072,
                        )
                    },
                )
            )
        },
    )

    result = await factory.build(AgentRole.MASTER_CHEF, Answer).ainvoke(
        [ModelMessage(role="user", content="Cook.")],
        config={
            "metadata": {
                "session_id": "session-1",
                "job_id": "job-1",
                "batch_number": 2,
                "prompt": "private-correlation-canary",
                "api_key": "secret-correlation-canary",
            }
        },
    )

    assert result == Answer(value="ok")
    assert json.loads(requests[0].content)["model"] == "vendor/exact-model"
    assert traced == [
        (AgentRole.MASTER_CHEF, ProviderName.OPENROUTER, "vendor/exact-model")
    ]
    events = await journal.snapshot()
    assert len(events) == 1
    event = events[0]
    assert event.role is AgentRole.MASTER_CHEF
    assert event.provider is ProviderName.OPENROUTER
    assert event.model == "vendor/exact-model"
    assert event.usage.input_tokens == 11
    assert event.usage.output_tokens == 4
    assert event.session_id == "session-1"
    assert event.job_id == "job-1"
    assert event.batch_number == 2
    assert "canary" not in repr(event)

    with log_context(session_id="session-disabled", job_id="job-disabled"):
        await factory.build(AgentRole.MASTER_CHEF, Answer).ainvoke(
            [ModelMessage(role="user", content="Cook without tracing.")],
            config={},
        )
    assert traced == [
        (AgentRole.MASTER_CHEF, ProviderName.OPENROUTER, "vendor/exact-model")
    ]
    disabled_event = (await journal.snapshot())[-1]
    assert disabled_event.session_id == "session-disabled"
    assert disabled_event.job_id == "job-disabled"


@pytest.mark.asyncio
async def test_factory_rejects_openrouter_tuning_missing_from_exact_discovery() -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = config.providers[ProviderName.OLLAMA].model_copy(
        update={
            "endpoint": "https://openrouter.test/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    )
    selection = config.roles[AgentRole.MASTER_CHEF].model_copy(
        update={
            "provider": ProviderName.OPENROUTER,
            "model": "vendor/no-reasoning",
            "text_tuning": config.roles[AgentRole.MASTER_CHEF].text_tuning.model_copy(
                update={"reasoning_effort": "high", "context_window": 32768}
            ),
        }
    )
    custom = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: provider,
                    },
                    "roles": {**config.roles, AgentRole.MASTER_CHEF: selection},
                }
            )
        }
    )
    transport_calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"value":"unsafe"}'}}]},
        )

    factory = model_runtime.RuntimeStructuredModelFactory(
        custom,
        environ={"OPENROUTER_API_KEY": "secret"},
        transports={ProviderName.OPENROUTER: httpx.MockTransport(respond)},
        discoveries={
            ProviderName.OPENROUTER: Discovery(
                ProviderDiscoveryResult(
                    provider=ProviderName.OPENROUTER,
                    models={
                        "vendor/no-reasoning": DiscoveredModel(
                            model="vendor/no-reasoning",
                            available=True,
                            capabilities=(
                                Capability.STRUCTURED_OUTPUT,
                                Capability.TEXT,
                            ),
                            supported_parameters=(
                                "structured_outputs",
                                "temperature",
                            ),
                            context_window=16384,
                        )
                    },
                )
            )
        },
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await factory.build(AgentRole.MASTER_CHEF, Answer).ainvoke(
            [ModelMessage(role="user", content="Cook.")]
        )

    assert raised.value.error.kind is ProviderErrorKind.CAPABILITY_MISSING
    assert transport_calls == 0


@pytest.mark.asyncio
async def test_factory_requires_discovered_ollama_thinking_for_positive_reasoning() -> (
    None
):
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    transport_calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return httpx.Response(
            200,
            json={"message": {"content": '{"value":"unsafe"}'}},
        )

    factory = model_runtime.RuntimeStructuredModelFactory(
        snapshot,
        transports={ProviderName.OLLAMA: httpx.MockTransport(respond)},
        discoveries={
            ProviderName.OLLAMA: Discovery(
                ProviderDiscoveryResult(
                    provider=ProviderName.OLLAMA,
                    models={
                        "gpt-oss:20b": DiscoveredModel(
                            model="gpt-oss:20b",
                            available=True,
                            capabilities=(
                                Capability.STRUCTURED_OUTPUT,
                                Capability.TEXT,
                            ),
                        )
                    },
                )
            )
        },
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await factory.build(AgentRole.MASTER_CHEF, Answer).ainvoke(
            [ModelMessage(role="user", content="Cook.")]
        )

    assert raised.value.error.kind is ProviderErrorKind.CAPABILITY_MISSING
    assert transport_calls == 0


@pytest.mark.asyncio
async def test_factory_keeps_codex_unavailable_instead_of_falling_back() -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    codex_provider = config.providers[ProviderName.OLLAMA].model_copy(
        update={"endpoint": None, "command": ("codex", "app-server")}
    )
    selection = config.roles[AgentRole.MASTER_CHEF].model_copy(
        update={"provider": ProviderName.CODEX, "model": "codex-exact"}
    )
    custom = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.CODEX: codex_provider,
                    },
                    "roles": {**config.roles, AgentRole.MASTER_CHEF: selection},
                }
            )
        }
    )
    model = model_runtime.RuntimeStructuredModelFactory(custom).build(
        AgentRole.MASTER_CHEF,
        Answer,
    )

    with pytest.raises(ProviderInvocationError) as raised:
        await model.ainvoke([ModelMessage(role="user", content="Cook.")])

    assert raised.value.error.kind is ProviderErrorKind.CAPABILITY_MISSING
    assert raised.value.error.provider is ProviderName.CODEX
