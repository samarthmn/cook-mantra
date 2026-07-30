import pytest
from langchain_ollama import ChatOllama

from core import Agent, Model, Settings
from services import get_model


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm_timeout_seconds=300,
        ollama_base_url="http://192.168.29.16:11434",
        _env_file=None,
    )


def test_backend_switching_is_not_supported(settings: Settings) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'backend'"):
        get_model(  # type: ignore[call-arg]
            Agent.INGREDIENT_EXTRACTION,
            backend="remote",
            settings=settings,
        )


def test_model_targets_the_configured_ollama_host(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, settings=settings)

    assert isinstance(model, ChatOllama)
    assert model.model == "qwen3.5:27b"
    assert model.base_url == "http://192.168.29.16:11434"


def test_model_normalizes_a_typed_ollama_url_with_a_trailing_slash() -> None:
    settings = Settings(
        ollama_base_url="http://192.168.29.16:11434/",
        _env_file=None,
    )

    model = get_model(Agent.MASTER_CHEF, settings=settings)

    assert model.base_url == "http://192.168.29.16:11434"


def test_each_agent_uses_its_configured_model(settings: Settings) -> None:
    extraction = get_model(Agent.INGREDIENT_EXTRACTION, settings=settings)
    chef = get_model(Agent.MASTER_CHEF, settings=settings)

    assert extraction.model == "qwen3.5:9b"
    assert chef.model == "qwen3.5:27b"


def test_explicit_model_overrides_the_agent_default(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, model=Model.GEMMA_LARGE, settings=settings)

    assert model.model == "gemma4:26b"


def test_thinking_can_be_disabled(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, thinking=False, settings=settings)

    assert model.reasoning is False


def test_thinking_left_unset_defers_to_the_model(settings: Settings) -> None:
    model = get_model(Agent.MASTER_CHEF, settings=settings)

    assert model.reasoning is None


def test_image_agent_is_rejected_as_a_chat_model(settings: Settings) -> None:
    with pytest.raises(ValueError, match="generates images"):
        get_model(Agent.IMAGE, settings=settings)


def test_unknown_agent_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="Unknown agent 'sous_chef'"):
        get_model("sous_chef", settings=settings)


def test_unknown_model_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="Unknown model 'gpt-4o'"):
        get_model(Agent.MASTER_CHEF, model="gpt-4o", settings=settings)
