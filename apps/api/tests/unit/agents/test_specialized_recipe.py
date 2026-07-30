import asyncio
import json
from copy import deepcopy

import pytest

from agents import specialized_recipe as specialized_recipe_module
from agents.specialized_recipe import OllamaSpecializedRecipeAgent
from core.config import PROJECT_ROOT, Agent, Settings
from core.errors import AppError, ErrorCode
from domain.recipe_options import RecipeOption, RecipePreferences
from domain.recipes import CompleteRecipe


def recipe_option() -> RecipeOption:
    return RecipeOption(
        id='option-"quoted"\nline',
        name='Tomato "Masala", bright',
        summary="A tangy curry.\nServe immediately.",
        cuisine="Indian, Gujarati",
        total_minutes=35,
        difficulty="medium",
        used_ingredients=["Tomato, ripe", 'Onion "red"'],
        missing_ingredients=[
            {
                "name": "Mustard seeds",
                "reason": "Needed for tempering",
                "substitution": "Cumin seeds",
            }
        ],
        optional_ingredients=[
            {
                "name": "Coriander",
                "reason": "Fresh garnish",
            }
        ],
        nutrition={
            "calories_kcal": 320,
            "protein_g": 9,
            "carbohydrates_g": 42,
            "fat_g": 14,
            "diet_tags": ["vegetarian"],
            "allergen_warnings": ["Check packaged spice blends"],
        },
        preview={
            "artifact_id": 'preview-"tomato"',
            "label": "ignored model label",
        },
        warnings=['Option warning, with "quotes"'],
    )


def preferences() -> RecipePreferences:
    return RecipePreferences(
        dietary_preferences=['Vegetarian, "strict"'],
        allergens=["Peanut\ntraces"],
        preferred_cuisines=["Gujarati"],
        max_total_minutes=40,
        servings=3,
        option_count=2,
    )


def recipe_output(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "option_id": "model-option",
        "name": "Model recipe name",
        "cuisine": "Model cuisine",
        "servings": 7,
        "total_minutes": 35,
        "ingredients": [
            {
                "name": "Tomato, ripe",
                "quantity": "3 medium",
                "availability": "available",
            },
            {
                "name": "Mustard seeds",
                "quantity": "1 teaspoon",
                "availability": "missing",
                "substitution": "Cumin seeds",
            },
            {
                "name": "Coriander",
                "quantity": "1 tablespoon",
                "availability": "optional",
            },
        ],
        "steps": [
            {
                "number": 1,
                "instruction": "Prepare the ingredients.",
                "duration_minutes": 5,
            },
            {
                "number": 2,
                "instruction": "Cook until tender.",
                "duration_minutes": 30,
            },
        ],
        "tips": ["Taste before serving."],
        "substitutions": ["Use cumin seeds for mustard seeds."],
        "nutrition_notice": "Estimated values; not medical advice.",
        "allergen_notice": "Check ingredient labels for allergens.",
        "assumptions": ["A stovetop is available."],
        "warnings": ["Use care around hot oil."],
    }
    values.update(updates)
    return values


class ValidatingStructuredModel:
    def __init__(
        self,
        outcomes: list[dict[str, object] | BaseException],
    ) -> None:
        self.outcomes = outcomes
        self.attempts = 0
        self.messages: list[object] = []

    async def ainvoke(self, messages: object) -> CompleteRecipe:
        self.messages.append(messages)
        outcome = self.outcomes[self.attempts]
        self.attempts += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return CompleteRecipe.model_validate(outcome)


class StructuredModelFactory:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.schema: type[CompleteRecipe] | None = None
        self.model: ValidatingStructuredModel | None = None

    def with_structured_output(
        self,
        schema: type[CompleteRecipe],
    ) -> ValidatingStructuredModel:
        self.schema = schema
        self.model = ValidatingStructuredModel([self.output])
        return self.model


def prompt_json(prompt: str, label: str) -> object:
    prefix = f"{label}: "
    line = next(line for line in prompt.splitlines() if line.startswith(prefix))
    return json.loads(line.removeprefix(prefix))


@pytest.mark.asyncio
async def test_agent_defers_model_construction_and_forwards_exact_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "specialized-recipe-settings-test",
        ollama_base_url="http://configured-ollama.test:11434",
        llm_timeout_seconds=17,
    )
    factory = StructuredModelFactory(recipe_output())
    calls: list[tuple[Agent, Settings | None]] = []

    def capture_model(
        agent: Agent,
        *,
        settings: Settings | None,
    ) -> StructuredModelFactory:
        calls.append((agent, settings))
        return factory

    monkeypatch.setattr(specialized_recipe_module, "get_model", capture_model)
    recipe_agent = OllamaSpecializedRecipeAgent(settings=settings)

    assert calls == []

    result = await recipe_agent.generate(
        recipe_option(),
        ["Tomato, ripe"],
        preferences(),
    )

    assert result.name == recipe_option().name
    assert calls == [(Agent.SPECIALIZED_RECIPE, settings)]
    assert factory.schema is CompleteRecipe


@pytest.mark.asyncio
async def test_prompt_contains_every_input_fact_and_complete_recipe_constraint() -> (
    None
):
    option = recipe_option()
    user_preferences = preferences()
    confirmed = [" Tomato, ripe ", 'Onion "red"']
    output = recipe_output()
    model = ValidatingStructuredModel([output, output])
    recipe_agent = OllamaSpecializedRecipeAgent(model=model)

    await recipe_agent.generate(option, confirmed, user_preferences)
    await recipe_agent.generate(option, confirmed, user_preferences)

    first_prompt = model.messages[0][0].content
    second_prompt = model.messages[1][0].content
    assert isinstance(first_prompt, str)
    assert first_prompt == second_prompt
    assert prompt_json(first_prompt, "Selected option JSON") == option.model_dump(
        mode="json"
    )
    assert prompt_json(first_prompt, "Confirmed ingredients JSON") == [
        "Tomato, ripe",
        'Onion "red"',
    ]
    assert prompt_json(first_prompt, "Preferences JSON") == user_preferences.model_dump(
        mode="json"
    )

    prompt_lower = " ".join(first_prompt.lower().split())
    assert "cuisine-appropriate technique" in prompt_lower
    assert "exact quantities" in prompt_lower
    assert "step numbers exactly 1 through n" in prompt_lower
    assert "duration_minutes for every step" in prompt_lower
    assert "requested servings" in prompt_lower
    assert "total cooking time" in prompt_lower
    assert "tips" in prompt_lower
    assert "substitutions" in prompt_lower
    assert "assumptions" in prompt_lower
    assert "warnings" in prompt_lower
    assert "nutrition_notice" in prompt_lower
    assert "allergen_notice" in prompt_lower
    assert "only exact confirmed ingredient names" in prompt_lower
    assert 'marked "available"' in prompt_lower
    assert 'marked "missing" or "optional"' in prompt_lower
    assert "do not infer pantry ingredients" in prompt_lower


@pytest.mark.asyncio
async def test_prompt_json_escapes_unicode_line_separators() -> None:
    option = recipe_option().model_copy(
        update={"summary": "First line\u2028second line\u2029third line"}
    )
    model = ValidatingStructuredModel([recipe_output()])

    await OllamaSpecializedRecipeAgent(model=model).generate(
        option,
        ["Tomato, ripe"],
        preferences(),
    )

    prompt = model.messages[0][0].content
    assert "\u2028" not in prompt
    assert "\u2029" not in prompt
    assert "\\u2028" in prompt
    assert "\\u2029" in prompt
    assert prompt_json(prompt, "Selected option JSON") == option.model_dump(mode="json")


@pytest.mark.asyncio
async def test_server_owned_identity_and_servings_overwrite_model_values() -> None:
    option = recipe_option()
    user_preferences = preferences()
    model = ValidatingStructuredModel(
        [
            recipe_output(
                option_id="wrong-option",
                name="Wrong name",
                cuisine="Wrong cuisine",
                servings=11,
            )
        ]
    )

    result = await OllamaSpecializedRecipeAgent(model=model).generate(
        option,
        ["Tomato, ripe"],
        user_preferences,
    )

    assert result.option_id == option.id
    assert result.name == option.name
    assert result.cuisine == option.cuisine
    assert result.servings == user_preferences.servings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_output",
    [
        recipe_output(option_id="   "),
        recipe_output(
            steps=[
                {
                    "number": 2,
                    "instruction": "Start at the wrong number.",
                    "duration_minutes": 5,
                }
            ]
        ),
    ],
    ids=["blank-identity", "invalid-step-numbering"],
)
async def test_invalid_model_output_uses_only_shared_two_attempt_boundary(
    invalid_output: dict[str, object],
) -> None:
    model = ValidatingStructuredModel([invalid_output, invalid_output])
    recipe_agent = OllamaSpecializedRecipeAgent(model=model)

    with pytest.raises(AppError) as raised:
        await recipe_agent.generate(
            recipe_option(),
            ["Tomato, ripe"],
            preferences(),
        )

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.status_code == 502
    assert raised.value.retryable is True
    assert model.attempts == 2


@pytest.mark.asyncio
async def test_shared_retry_can_recover_once_without_an_adapter_retry_layer() -> None:
    invalid = recipe_output(
        steps=[
            {
                "number": 3,
                "instruction": "Invalid first response.",
                "duration_minutes": 5,
            }
        ]
    )
    model = ValidatingStructuredModel([invalid, recipe_output()])

    result = await OllamaSpecializedRecipeAgent(model=model).generate(
        recipe_option(),
        ["Tomato, ripe"],
        preferences(),
    )

    assert result.option_id == recipe_option().id
    assert model.attempts == 2


@pytest.mark.asyncio
async def test_unconfirmed_available_item_is_rejected_once_with_safe_error() -> None:
    private_model_text = "Fresh Tomato PRIVATE_MODEL_REASONING"
    model = ValidatingStructuredModel(
        [
            recipe_output(
                ingredients=[
                    {
                        "name": private_model_text,
                        "quantity": "3 medium",
                        "availability": "available",
                    }
                ]
            )
        ]
    )

    with pytest.raises(AppError) as raised:
        await OllamaSpecializedRecipeAgent(model=model).generate(
            recipe_option(),
            ["Tomato"],
            preferences(),
        )

    error = raised.value
    assert error.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert error.message == "The model returned invalid ingredient availability."
    assert error.status_code == 502
    assert error.retryable is True
    assert error.details == {}
    assert private_model_text not in str(error)
    assert model.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ingredients",
    [
        [
            {
                "name": "Tomato, ripe",
                "quantity": "3 medium",
                "availability": "available",
            },
            {
                "name": "Coriander",
                "quantity": "1 tablespoon",
                "availability": "optional",
            },
        ],
        [
            {
                "name": "Tomato, ripe",
                "quantity": "3 medium",
                "availability": "available",
            },
            {
                "name": "Mustard seeds",
                "quantity": "1 teaspoon",
                "availability": "missing",
            },
        ],
    ],
    ids=["omitted-known-missing", "omitted-known-optional"],
)
async def test_known_missing_and_optional_items_cannot_disappear(
    ingredients: list[dict[str, object]],
) -> None:
    model = ValidatingStructuredModel([recipe_output(ingredients=ingredients)])

    with pytest.raises(AppError) as raised:
        await OllamaSpecializedRecipeAgent(model=model).generate(
            recipe_option(),
            ["Tomato, ripe"],
            preferences(),
        )

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.status_code == 502
    assert raised.value.retryable is True
    assert model.attempts == 1


@pytest.mark.asyncio
async def test_confirmed_recipe_ingredient_cannot_be_marked_missing() -> None:
    model = ValidatingStructuredModel(
        [
            recipe_output(
                ingredients=[
                    {
                        "name": "Tomato, ripe",
                        "quantity": "3 medium",
                        "availability": "missing",
                    },
                    {
                        "name": "Mustard seeds",
                        "quantity": "1 teaspoon",
                        "availability": "missing",
                    },
                    {
                        "name": "Coriander",
                        "quantity": "1 tablespoon",
                        "availability": "optional",
                    },
                ]
            )
        ]
    )

    with pytest.raises(AppError) as raised:
        await OllamaSpecializedRecipeAgent(model=model).generate(
            recipe_option(),
            ["  TOMATO,   RIPE  "],
            preferences(),
        )

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.status_code == 502
    assert raised.value.retryable is True
    assert model.attempts == 1


@pytest.mark.asyncio
async def test_duplicate_confirmed_names_are_normalized_without_mutating_inputs() -> (
    None
):
    option = recipe_option()
    user_preferences = preferences()
    confirmed = [
        "  Tomato   Sauce  ",
        "tomato sauce",
        "ONION",
        " onion ",
        "   ",
    ]
    original_confirmed = confirmed.copy()
    original_option = deepcopy(option.model_dump(mode="json"))
    original_preferences = deepcopy(user_preferences.model_dump(mode="json"))
    model = ValidatingStructuredModel(
        [
            recipe_output(
                ingredients=[
                    {
                        "name": "tomato sauce",
                        "quantity": "2 cups",
                        "availability": "available",
                    },
                    {
                        "name": "onion",
                        "quantity": "1 medium",
                        "availability": "available",
                    },
                    {
                        "name": "Mustard seeds",
                        "quantity": "1 teaspoon",
                        "availability": "missing",
                    },
                    {
                        "name": "Coriander",
                        "quantity": "1 tablespoon",
                        "availability": "optional",
                    },
                ]
            )
        ]
    )

    await OllamaSpecializedRecipeAgent(model=model).generate(
        option,
        confirmed,
        user_preferences,
    )

    prompt = model.messages[0][0].content
    assert prompt_json(prompt, "Confirmed ingredients JSON") == [
        "Tomato Sauce",
        "ONION",
    ]
    assert confirmed == original_confirmed
    assert option.model_dump(mode="json") == original_option
    assert user_preferences.model_dump(mode="json") == original_preferences


@pytest.mark.asyncio
async def test_empty_confirmed_list_allows_only_missing_or_optional_items() -> None:
    model = ValidatingStructuredModel(
        [
            recipe_output(
                ingredients=[
                    {
                        "name": "Cooking oil",
                        "quantity": "1 tablespoon",
                        "availability": "missing",
                    },
                    {
                        "name": "Coriander",
                        "quantity": "1 tablespoon",
                        "availability": "optional",
                    },
                    {
                        "name": "Mustard seeds",
                        "quantity": "1 teaspoon",
                        "availability": "missing",
                    },
                ]
            )
        ]
    )

    result = await OllamaSpecializedRecipeAgent(model=model).generate(
        recipe_option(),
        [],
        preferences(),
    )

    prompt = model.messages[0][0].content
    assert prompt_json(prompt, "Confirmed ingredients JSON") == []
    assert [ingredient.availability.value for ingredient in result.ingredients] == [
        "missing",
        "optional",
        "missing",
    ]


@pytest.mark.asyncio
async def test_injected_model_never_constructs_an_ollama_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_model_construction(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Injected tests must not construct a network model.")

    monkeypatch.setattr(
        specialized_recipe_module,
        "get_model",
        fail_model_construction,
    )
    model = ValidatingStructuredModel([recipe_output()])

    result = await OllamaSpecializedRecipeAgent(model=model).generate(
        recipe_option(),
        ["Tomato, ripe"],
        preferences(),
    )

    assert result.option_id == recipe_option().id


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry() -> None:
    model = ValidatingStructuredModel(
        [
            asyncio.CancelledError(),
            recipe_output(),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        await OllamaSpecializedRecipeAgent(model=model).generate(
            recipe_option(),
            ["Tomato, ripe"],
            preferences(),
        )

    assert model.attempts == 1
