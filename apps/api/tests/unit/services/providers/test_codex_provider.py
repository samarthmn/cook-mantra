from collections.abc import Sequence

import pytest
from pydantic import BaseModel

from core.runtime_config import get_runtime_snapshot
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ModelMessage,
    ModelResult,
    ProviderDiscoveryResult,
    ProviderName,
)
from services.model_runtime import RuntimeStructuredModelFactory


class _Answer(BaseModel):
    value: str


class _Discovery:
    async def inspect(self) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.CODEX,
            available_models=("codex-exact",),
            models={
                "codex-exact": DiscoveredModel(
                    model="codex-exact",
                    available=True,
                    capabilities=(Capability.STRUCTURED_OUTPUT, Capability.TEXT),
                )
            },
        )


class _CodexInvoker:
    def __init__(self) -> None:
        self.calls: list[
            tuple[Sequence[ModelMessage], type[BaseModel], str, float]
        ] = []

    async def invoke(
        self,
        messages: Sequence[ModelMessage],
        schema: type[_Answer],
        *,
        model: str,
        timeout_seconds: float,
    ) -> ModelResult[_Answer]:
        self.calls.append((messages, schema, model, timeout_seconds))
        return ModelResult(output=_Answer(value="codex"))

    async def invoke_admitted(
        self,
        messages: Sequence[ModelMessage],
        schema: type[_Answer],
        *,
        model: str,
        timeout_seconds: float,
        admit,
    ) -> ModelResult[_Answer]:
        admit(await _Discovery().inspect())
        return await self.invoke(
            messages,
            schema,
            model=model,
            timeout_seconds=timeout_seconds,
        )


class _MustNotInspectSeparately:
    async def inspect(self) -> ProviderDiscoveryResult:
        raise AssertionError("Codex composed invocation must not inspect separately.")


class _ComposedCodexOwner(_CodexInvoker):
    def __init__(self) -> None:
        super().__init__()
        self.composed_calls = 0

    async def invoke_admitted(
        self,
        messages: Sequence[ModelMessage],
        schema: type[_Answer],
        *,
        model: str,
        timeout_seconds: float,
        admit,
    ) -> ModelResult[_Answer]:
        self.composed_calls += 1
        discovery = ProviderDiscoveryResult(
            provider=ProviderName.CODEX,
            models={
                "codex-exact": DiscoveredModel(
                    model="codex-exact",
                    available=True,
                    capabilities=(Capability.STRUCTURED_OUTPUT, Capability.TEXT),
                )
            },
        )
        admit(discovery)
        return await self.invoke(
            messages,
            schema,
            model=model,
            timeout_seconds=timeout_seconds,
        )


def _codex_snapshot():
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = config.providers[ProviderName.OLLAMA].model_copy(
        update={"endpoint": None, "command": ("codex", "app-server")}
    )
    selection = config.roles[AgentRole.MASTER_CHEF].model_copy(
        update={"provider": ProviderName.CODEX, "model": "codex-exact"}
    )
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {**config.providers, ProviderName.CODEX: provider},
                    "roles": {**config.roles, AgentRole.MASTER_CHEF: selection},
                }
            )
        }
    )


@pytest.mark.asyncio
async def test_factory_selects_exact_codex_model_without_fallback() -> None:
    snapshot = _codex_snapshot()
    assert snapshot.config is not None
    selection = snapshot.config.roles[AgentRole.MASTER_CHEF]
    assert selection.text_tuning is not None
    invoker = _CodexInvoker()
    factory = RuntimeStructuredModelFactory(
        snapshot,
        discoveries={ProviderName.CODEX: _Discovery()},
        codex_client=invoker,
    )

    result = await factory.build(AgentRole.MASTER_CHEF, _Answer).ainvoke(
        [ModelMessage(role="user", content="protected prompt")]
    )

    assert result == _Answer(value="codex")
    assert len(invoker.calls) == 1
    messages, schema, model, timeout_seconds = invoker.calls[0]
    assert messages == [ModelMessage(role="user", content="protected prompt")]
    assert schema is _Answer
    assert model == "codex-exact"
    assert timeout_seconds == 300.0


@pytest.mark.asyncio
async def test_codex_catalog_uses_one_composed_owner_operation() -> None:
    snapshot = _codex_snapshot()
    owner = _ComposedCodexOwner()
    factory = RuntimeStructuredModelFactory(
        snapshot,
        discoveries={ProviderName.CODEX: _MustNotInspectSeparately()},
        codex_client=owner,
    )

    result = await factory.build(AgentRole.MASTER_CHEF, _Answer).ainvoke(
        [ModelMessage(role="user", content="protected prompt")]
    )

    assert result == _Answer(value="codex")
    assert owner.composed_calls == 1
    assert len(owner.calls) == 1
