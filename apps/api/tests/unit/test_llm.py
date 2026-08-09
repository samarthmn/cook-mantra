import pytest
from langchain_ollama import ChatOllama

from core import Agent, Model, Settings
from core.config import AGENT_MODELS
from core.errors import AppError, ErrorCode
from core.runtime_config import ProviderConfiguration, get_runtime_snapshot
from domain.model_runtime import AgentRole, ProviderName
from services import get_model
from services import llm as llm_service


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm_timeout_seconds=300,
        _env_file=None,
    )


def test_backend_switching_is_not_supported(settings: Settings) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'backend'"):
        get_model(  # type: ignore[call-arg]
            Agent.INGREDIENT_EXTRACTION,
            backend="remote",
            settings=settings,
        )


def test_unimplemented_selected_provider_is_refused_without_ollama_fallback(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    role = AgentRole.MASTER_CHEF
    openrouter = ProviderConfiguration(
        endpoint="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    )
    changed = config.roles[role].model_copy(
        update={"provider": ProviderName.OPENROUTER}
    )
    selected = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: openrouter,
                    },
                    "roles": {**config.roles, role: changed},
                }
            )
        }
    )
    monkeypatch.setattr(llm_service, "get_runtime_snapshot", lambda: selected)

    with pytest.raises(AppError) as raised:
        get_model(Agent.MASTER_CHEF, settings=settings)

    assert raised.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert raised.value.details == {"role": role.value}


def test_model_targets_the_yaml_configured_ollama_host(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, settings=settings)

    assert isinstance(model, ChatOllama)
    assert model.model == AGENT_MODELS[Agent.MASTER_CHEF].value
    assert model.base_url == "http://127.0.0.1:11434"


def test_each_agent_uses_its_configured_model(settings: Settings) -> None:
    extraction = get_model(Agent.INGREDIENT_EXTRACTION, settings=settings)
    chef = get_model(Agent.MASTER_CHEF, settings=settings)

    assert extraction.model == AGENT_MODELS[Agent.INGREDIENT_EXTRACTION].value
    assert chef.model == AGENT_MODELS[Agent.MASTER_CHEF].value


def test_yaml_tuning_is_translated_by_the_ollama_adapter() -> None:
    model = get_model(Agent.MASTER_CHEF, settings=Settings(_env_file=None))

    assert model.model == "gpt-oss:20b"
    assert model.base_url == "http://127.0.0.1:11434"
    assert model.reasoning == "low"
    assert model.num_ctx == 16_384


def test_explicit_model_overrides_the_agent_default(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, model=Model.GEMMA_LARGE, settings=settings)

    assert model.model == Model.GEMMA_LARGE.value


def test_thinking_can_be_disabled(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, thinking=False, settings=settings)

    assert model.reasoning is False


def test_thinking_left_unset_uses_yaml_tuning(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, settings=settings)

    assert model.reasoning == "low"


def test_reasoning_effort_levels_are_forwarded(settings: Settings) -> None:
    model = get_model(Agent.SPECIALIZED_RECIPE, thinking="low", settings=settings)

    assert model.reasoning == "low"


def test_agents_never_fall_back_to_the_ollama_default_context_window(
    settings: Settings,
) -> None:
    """Ollama's 4k default truncates structured output mid-JSON."""
    for agent in (
        Agent.INGREDIENT_EXTRACTION,
        Agent.MASTER_CHEF,
        Agent.SPECIALIZED_RECIPE,
    ):
        model = get_model(agent, settings=settings)

        assert model.num_ctx is not None and model.num_ctx > 4_096


def test_explicit_context_window_overrides_the_configured_default(
    settings: Settings,
) -> None:
    model = get_model(Agent.MASTER_CHEF, num_ctx=32_768, settings=settings)

    assert model.num_ctx == 32_768


def test_generation_budgets_can_be_configured(settings: Settings) -> None:
    model = get_model(
        Agent.SPECIALIZED_RECIPE,
        num_predict=4_096,
        num_ctx=16_384,
        settings=settings,
    )

    assert model.num_predict == 4_096
    assert model.num_ctx == 16_384


def test_keep_alive_is_forwarded(settings: Settings) -> None:
    model = get_model(
        Agent.INGREDIENT_EXTRACTION,
        keep_alive=0,
        settings=settings,
    )

    assert model.keep_alive == 0


def test_unknown_agent_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="Unknown agent 'sous_chef'"):
        get_model("sous_chef", settings=settings)


def test_unknown_model_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="Unknown model 'gpt-4o'"):
        get_model(Agent.MASTER_CHEF, model="gpt-4o", settings=settings)
