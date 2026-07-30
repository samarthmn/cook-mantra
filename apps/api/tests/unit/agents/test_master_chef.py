import pytest

from agents import master_chef as master_chef_module
from agents.master_chef import OllamaMasterChef
from core.config import PROJECT_ROOT, Agent, Settings
from core.errors import AppError, ErrorCode
from domain.recipe_options import RecipeOptionBatch, RecipePreferences


class CapturingStructuredModel:
    def __init__(self, output: RecipeOptionBatch) -> None:
        self.output = output
        self.messages: object | None = None

    async def ainvoke(self, messages: object) -> RecipeOptionBatch:
        self.messages = messages
        return self.output


class StructuredModelFactory:
    def __init__(self, output: RecipeOptionBatch) -> None:
        self.output = output

    def with_structured_output(
        self,
        schema: type[RecipeOptionBatch],
    ) -> CapturingStructuredModel:
        assert schema is RecipeOptionBatch
        return CapturingStructuredModel(self.output)


def recipe_batch(*names: str) -> RecipeOptionBatch:
    return RecipeOptionBatch(
        options=[
            {
                "name": name,
                "summary": "A quick tomato dish.",
                "cuisine": "Indian",
                "total_minutes": 20,
                "difficulty": "easy",
                "used_ingredients": ["Tomato"],
            }
            for name in names
        ]
    )


@pytest.mark.asyncio
async def test_master_chef_prompts_with_confirmed_inputs_and_constraints() -> None:
    model = CapturingStructuredModel(recipe_batch("Tomato masala"))
    chef = OllamaMasterChef(model=model)

    result = await chef.generate(
        ingredients=["Tomato", "Onion"],
        preferences=RecipePreferences(
            preferred_cuisines=["Indian"],
            dietary_preferences=["Vegetarian"],
            allergens=["Peanut"],
            max_total_minutes=30,
            servings=3,
            option_count=1,
        ),
        excluded_names={"tomato curry"},
    )

    assert result[0].name == "Tomato masala"
    assert model.messages is not None
    prompt = model.messages[0].content
    assert "only supplied confirmed items are available" in prompt.lower()
    assert "Tomato" in prompt
    assert "Onion" in prompt
    assert "Indian" in prompt
    assert "Vegetarian" in prompt
    assert "Peanut" in prompt
    assert "tomato curry" in prompt
    assert "missing" in prompt.lower()
    assert "optional" in prompt.lower()
    assert "substitution" in prompt.lower()
    assert "country or cuisine" in prompt.lower()
    assert "exactly 1" in prompt


@pytest.mark.asyncio
async def test_master_chef_rejects_a_batch_with_the_wrong_option_count() -> None:
    model = CapturingStructuredModel(recipe_batch("Tomato masala"))
    chef = OllamaMasterChef(model=model)

    with pytest.raises(AppError) as raised:
        await chef.generate(
            ingredients=["Tomato"],
            preferences=RecipePreferences(option_count=2),
            excluded_names=set(),
        )

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.status_code == 502
    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_master_chef_forwards_its_exact_settings_to_model_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "master-chef-settings-test",
        ollama_base_url="http://configured-ollama.test:11434",
        llm_timeout_seconds=17,
    )
    captured_settings: Settings | None = None

    def capture_model(
        agent: Agent,
        *,
        settings: Settings,
    ) -> StructuredModelFactory:
        nonlocal captured_settings
        assert agent is Agent.MASTER_CHEF
        captured_settings = settings
        return StructuredModelFactory(recipe_batch("Tomato masala"))

    monkeypatch.setattr(master_chef_module, "get_model", capture_model)
    chef = OllamaMasterChef(settings=settings)

    result = await chef.generate(
        ingredients=["Tomato"],
        preferences=RecipePreferences(option_count=1),
        excluded_names=set(),
    )

    assert result[0].name == "Tomato masala"
    assert captured_settings is settings
