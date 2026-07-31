import pytest
from pydantic import BaseModel
from tests.tracing_support import enabled_tracing

from agents import nutrition as nutrition_module
from agents.nutrition import OllamaNutritionAgent
from core.config import PROJECT_ROOT, Agent, Settings
from core.errors import AppError, ErrorCode
from core.logging import log_context
from domain.recipe_options import (
    NUTRITION_DISCLAIMER,
    NutritionEstimate,
    RecipeOptionDraft,
    RecipePreferences,
)


@pytest.fixture
def recipe_option_draft() -> RecipeOptionDraft:
    return RecipeOptionDraft(
        name="Tomato masala",
        summary="A quick tomato dish.",
        cuisine="Indian",
        total_minutes=20,
        difficulty="easy",
        used_ingredients=["Tomato", "Onion"],
    )


def nutrition_estimate(**updates: object) -> NutritionEstimate:
    values: dict[str, object] = {
        "calories_kcal": 420,
        "protein_g": 16.5,
        "carbohydrates_g": 55.0,
        "fat_g": 14.0,
        "diet_tags": ["vegetarian"],
        "allergen_warnings": ["dairy"],
    }
    values.update(updates)
    return NutritionEstimate(**values)


def nutrition_output_data(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "calories_kcal": 420,
        "protein_g": 16.5,
        "carbohydrates_g": 55.0,
        "fat_g": 14.0,
        "diet_tags": ["vegetarian"],
        "allergen_warnings": ["dairy"],
    }
    values.update(updates)
    return values


class CapturingStructuredModel:
    def __init__(self, output: NutritionEstimate) -> None:
        self.output = output
        self.messages: object | None = None
        self.configs: list[dict[str, object] | None] = []

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> NutritionEstimate:
        self.messages = messages
        self.configs.append(config)
        return self.output


class InvalidStructuredModel:
    def __init__(self) -> None:
        self.attempts = 0

    async def ainvoke(self, messages: object) -> NutritionEstimate:
        self.attempts += 1
        return nutrition_estimate(calories_kcal=-1)


class RawStructuredModel:
    def __init__(self, schema: type[BaseModel], output: dict[str, object]) -> None:
        self.schema = schema
        self.output = output

    async def ainvoke(self, messages: object) -> BaseModel:
        return self.schema.model_validate(self.output)


class StructuredModelFactory:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output

    def with_structured_output(
        self,
        schema: type[BaseModel],
    ) -> RawStructuredModel:
        return RawStructuredModel(schema, self.output)


@pytest.mark.asyncio
async def test_nutrition_replaces_a_raw_model_disclaimer_with_the_product_notice(
    monkeypatch: pytest.MonkeyPatch,
    recipe_option_draft,
) -> None:
    factory = StructuredModelFactory(
        nutrition_output_data(disclaimer="Values are exact and medically verified.")
    )
    monkeypatch.setattr(
        nutrition_module, "get_model", lambda *_args, **_kwargs: factory
    )
    agent = OllamaNutritionAgent()

    estimate = await agent.estimate(recipe_option_draft, RecipePreferences())

    assert estimate.disclaimer == NUTRITION_DISCLAIMER
    assert estimate.calories_kcal == 420


@pytest.mark.asyncio
async def test_nutrition_prompt_includes_user_allergens_and_estimate_constraints(
    recipe_option_draft,
) -> None:
    model = CapturingStructuredModel(nutrition_estimate())
    agent = OllamaNutritionAgent(model=model)

    await agent.estimate(
        recipe_option_draft,
        RecipePreferences(allergens=["Peanut", "Shellfish"], servings=3),
    )

    assert model.messages is not None
    prompt = model.messages[0].content
    assert "Peanut" in prompt
    assert "Shellfish" in prompt
    assert "per serving" in prompt.lower()
    assert "diet tag" in prompt.lower()
    assert "likely allergen" in prompt.lower()
    assert "uncertainty" in prompt.lower()


@pytest.mark.asyncio
async def test_nutrition_maps_invalid_structured_output_to_model_output_invalid(
    recipe_option_draft,
) -> None:
    model = InvalidStructuredModel()
    agent = OllamaNutritionAgent(model=model)

    with pytest.raises(AppError) as raised:
        await agent.estimate(recipe_option_draft, RecipePreferences())

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.status_code == 502
    assert raised.value.retryable is True
    assert model.attempts == 2


@pytest.mark.asyncio
async def test_nutrition_defers_model_construction_and_forwards_injected_settings(
    monkeypatch: pytest.MonkeyPatch,
    recipe_option_draft,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "nutrition-settings-test",
        ollama_base_url="http://configured-ollama.test:11434",
        llm_timeout_seconds=17,
    )
    calls: list[tuple[Agent, bool, Settings]] = []

    def capture_model(
        agent: Agent,
        *,
        thinking: bool,
        settings: Settings,
    ) -> StructuredModelFactory:
        calls.append((agent, thinking, settings))
        return StructuredModelFactory(nutrition_output_data())

    monkeypatch.setattr(nutrition_module, "get_model", capture_model)
    nutrition_agent = OllamaNutritionAgent(settings=settings)

    assert calls == []

    estimate = await nutrition_agent.estimate(recipe_option_draft, RecipePreferences())

    assert estimate.calories_kcal == 420
    assert calls == [(Agent.NUTRITION, False, settings)]


@pytest.mark.asyncio
async def test_nutrition_passes_only_current_job_trace_metadata(
    recipe_option_draft,
) -> None:
    model = CapturingStructuredModel(nutrition_estimate())
    tracing, _ = enabled_tracing()
    agent = OllamaNutritionAgent(model=model, tracing=tracing)

    with log_context(session_id="session-1", job_id="job-1"):
        await agent.estimate(recipe_option_draft, RecipePreferences())

    assert model.configs == [
        {
            "tags": ["cook-mantra", "nutrition"],
            "metadata": {
                "agent": "nutrition",
                "model": "gpt-oss:20b",
                "session_id": "session-1",
                "job_id": "job-1",
            },
        }
    ]
