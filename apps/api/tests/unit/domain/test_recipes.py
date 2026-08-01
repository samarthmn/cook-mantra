from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.errors import ErrorCode
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeFailure,
    RecipeIngredient,
    RecipeStep,
)
from domain.sessions import Session, SessionStage
from schemas.recipes import RecipeSelectionRequest
from schemas.sessions import SessionResponse


def complete_recipe(**updates: object) -> CompleteRecipe:
    values: dict[str, object] = {
        "option_id": "option-1",
        "name": "Tomato Curry",
        "cuisine": "Indian",
        "servings": 2,
        "total_minutes": 30,
        "ingredients": [
            RecipeIngredient(
                name="Tomato",
                quantity="3 medium",
                availability=IngredientAvailability.AVAILABLE,
            )
        ],
        "steps": [
            RecipeStep(number=1, instruction="Chop the tomatoes."),
            RecipeStep(number=2, instruction="Cook until soft."),
        ],
    }
    values.update(updates)
    return CompleteRecipe.model_validate(values)


def session_with_recipe() -> Session:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    recipe = complete_recipe()
    failure = RecipeFailure(
        option_id="option-2",
        code=ErrorCode.MODEL_OUTPUT_INVALID,
        message="The generated recipe was invalid.",
        retryable=True,
    )
    return Session(
        id="session-1",
        stage=SessionStage.RECIPES_READY,
        complete_recipes={recipe.option_id: recipe},
        recipe_failures={failure.option_id: failure},
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_complete_recipe_requires_numbered_steps() -> None:
    recipe = complete_recipe()

    assert [step.number for step in recipe.steps] == [1, 2]


@pytest.mark.parametrize(
    "numbers",
    [
        [2],
        [1, 3],
        [1, 1],
    ],
)
def test_complete_recipe_rejects_nonsequential_step_numbers(
    numbers: list[int],
) -> None:
    with pytest.raises(ValidationError, match="consecutive"):
        complete_recipe(
            steps=[
                RecipeStep(number=number, instruction=f"Instruction {index}")
                for index, number in enumerate(numbers, start=1)
            ]
        )


@pytest.mark.parametrize("option_ids", [[], [f"option-{index}" for index in range(7)]])
def test_selection_accepts_only_one_through_six_options(
    option_ids: list[str],
) -> None:
    with pytest.raises(ValidationError):
        RecipeSelectionRequest(option_ids=option_ids)


def test_selection_rejects_duplicate_normalized_option_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        RecipeSelectionRequest(option_ids=[" option-1 ", "option-1"])


def test_selection_normalizes_nonblank_option_ids() -> None:
    request = RecipeSelectionRequest(option_ids=["  option-1  ", "option-2"])

    assert request.option_ids == ["option-1", "option-2"]

    with pytest.raises(ValidationError, match="blank"):
        RecipeSelectionRequest(option_ids=["   "])


@pytest.mark.parametrize("servings", [0, 13])
def test_complete_recipe_rejects_servings_outside_product_bounds(
    servings: int,
) -> None:
    with pytest.raises(ValidationError):
        complete_recipe(servings=servings)


@pytest.mark.parametrize("total_minutes", [0, 1_441])
def test_complete_recipe_rejects_total_time_outside_product_bounds(
    total_minutes: int,
) -> None:
    with pytest.raises(ValidationError):
        complete_recipe(total_minutes=total_minutes)


def test_complete_recipe_requires_at_least_one_ingredient() -> None:
    with pytest.raises(ValidationError):
        complete_recipe(ingredients=[])


def test_recipe_step_rejects_zero_duration() -> None:
    with pytest.raises(ValidationError):
        RecipeStep(number=1, instruction="Cook.", duration_minutes=0)


def test_recipe_step_defaults_optional_guidance_to_none() -> None:
    step = RecipeStep(number=1, instruction="Prepare the ingredients.")

    assert step.done_when is None
    assert step.heat_level is None


@pytest.mark.parametrize("heat_level", ["low", "medium", "medium-high", "high"])
def test_recipe_step_accepts_sensory_cue_and_valid_heat_levels(
    heat_level: str,
) -> None:
    step = RecipeStep(
        number=1,
        instruction="Sear the paneer.",
        done_when="  edges deeply golden and crisp  ",
        heat_level=heat_level,
    )

    assert step.done_when == "edges deeply golden and crisp"
    assert step.heat_level == heat_level


@pytest.mark.parametrize("done_when", ["", "   ", "\t\n"])
def test_recipe_step_rejects_blank_done_when(done_when: str) -> None:
    with pytest.raises(ValidationError, match="blank"):
        RecipeStep(number=1, instruction="Cook.", done_when=done_when)


def test_recipe_step_rejects_done_when_over_max_length() -> None:
    with pytest.raises(ValidationError):
        RecipeStep(number=1, instruction="Cook.", done_when="a" * 201)


def test_recipe_step_applies_done_when_limit_after_normalizing() -> None:
    step = RecipeStep(
        number=1,
        instruction="Cook.",
        done_when=f"  {'a' * 200}  ",
    )

    assert step.done_when == "a" * 200


def test_recipe_step_rejects_invalid_heat_level() -> None:
    with pytest.raises(ValidationError):
        RecipeStep(number=1, instruction="Cook.", heat_level="medium-low")


@pytest.mark.parametrize("field_name", ["number", "duration_minutes"])
@pytest.mark.parametrize("invalid_value", [True, 1.0, "1"])
def test_recipe_step_rejects_coercible_noninteger_scalars(
    field_name: str,
    invalid_value: object,
) -> None:
    values: dict[str, object] = {
        "number": 1,
        "instruction": "Cook.",
        "duration_minutes": 2,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValidationError):
        RecipeStep.model_validate(values)


@pytest.mark.parametrize("field_name", ["servings", "total_minutes"])
@pytest.mark.parametrize("invalid_value", [True, 2.0, "2"])
def test_complete_recipe_rejects_coercible_noninteger_scalars(
    field_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(ValidationError):
        complete_recipe(**{field_name: invalid_value})


@pytest.mark.parametrize("invalid_value", [0, 1, "false", "true"])
def test_recipe_failure_retryable_rejects_coercible_nonboolean_scalars(
    invalid_value: object,
) -> None:
    with pytest.raises(ValidationError):
        RecipeFailure(
            option_id="option-1",
            code=ErrorCode.MODEL_OUTPUT_INVALID,
            message="The generated recipe was invalid.",
            retryable=invalid_value,
        )


def test_strict_scalars_keep_json_schema_primitive_types() -> None:
    step_properties = RecipeStep.model_json_schema()["properties"]
    recipe_properties = CompleteRecipe.model_json_schema()["properties"]
    failure_properties = RecipeFailure.model_json_schema()["properties"]

    assert step_properties["number"]["type"] == "integer"
    assert step_properties["duration_minutes"]["anyOf"] == [
        {"minimum": 1, "type": "integer"},
        {"type": "null"},
    ]
    assert recipe_properties["servings"]["type"] == "integer"
    assert recipe_properties["total_minutes"]["type"] == "integer"
    assert failure_properties["retryable"]["type"] == "boolean"


def test_missing_and_optional_ingredients_keep_substitution_semantics() -> None:
    recipe = complete_recipe(
        ingredients=[
            {
                "name": "Tomato",
                "quantity": "3 medium",
                "availability": "available",
            },
            {
                "name": "Coconut milk",
                "quantity": "200 ml",
                "availability": "missing",
                "substitution": "  Cashew cream  ",
            },
            {
                "name": "Coriander",
                "quantity": "1 tablespoon",
                "availability": "optional",
            },
        ]
    )

    dumped = recipe.model_dump(mode="json")

    assert dumped["ingredients"][1] == {
        "name": "Coconut milk",
        "quantity": "200 ml",
        "availability": "missing",
        "substitution": "Cashew cream",
    }
    assert dumped["ingredients"][2]["availability"] == "optional"
    assert dumped["ingredients"][2]["substitution"] is None


def test_substitution_is_nonblank_when_supplied() -> None:
    with pytest.raises(ValidationError, match="blank"):
        RecipeIngredient(
            name="Coconut milk",
            quantity="200 ml",
            availability=IngredientAvailability.MISSING,
            substitution="   ",
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("option_id", "   "),
        ("name", "   "),
        ("cuisine", "   "),
        ("nutrition_notice", "   "),
        ("allergen_notice", "   "),
    ],
)
def test_complete_recipe_rejects_blank_string_fields(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="blank"):
        complete_recipe(**{field_name: value})


def test_recipe_strings_and_string_collections_are_normalized() -> None:
    recipe = complete_recipe(
        option_id="  option-1 ",
        name="  Tomato Curry ",
        cuisine="  Indian ",
        tips=["  Taste before salting.  "],
        substitutions=["  Use cashew cream.  "],
        assumptions=["  A stovetop is available.  "],
        warnings=["  Handle chillies carefully.  "],
    )

    assert recipe.option_id == "option-1"
    assert recipe.name == "Tomato Curry"
    assert recipe.cuisine == "Indian"
    assert recipe.tips == ("Taste before salting.",)
    assert recipe.substitutions == ("Use cashew cream.",)
    assert recipe.assumptions == ("A stovetop is available.",)
    assert recipe.warnings == ("Handle chillies carefully.",)

    with pytest.raises(ValidationError, match="blank"):
        complete_recipe(tips=["   "])


def test_stored_recipe_values_are_immutable() -> None:
    recipe = complete_recipe()

    with pytest.raises(ValidationError):
        recipe.name = "Changed"
    with pytest.raises(ValidationError):
        recipe.ingredients[0].quantity = "Changed"

    assert isinstance(recipe.ingredients, tuple)
    assert isinstance(recipe.steps, tuple)
    assert isinstance(recipe.tips, tuple)


def test_recipe_failure_is_immutable_and_serializes_error_code() -> None:
    failure = RecipeFailure(
        option_id="  option-1 ",
        code=ErrorCode.OPERATION_TIMED_OUT,
        message="  Recipe generation timed out.  ",
        retryable=True,
    )

    assert failure.model_dump(mode="json") == {
        "option_id": "option-1",
        "code": "operation_timed_out",
        "message": "Recipe generation timed out.",
        "retryable": True,
    }
    with pytest.raises(ValidationError):
        failure.retryable = False


def test_session_recipe_state_has_backward_compatible_independent_defaults() -> None:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    first = Session(
        id="session-1",
        created_at=timestamp,
        updated_at=timestamp,
    )
    second = Session(
        id="session-2",
        created_at=timestamp,
        updated_at=timestamp,
    )

    assert first.complete_recipes == {}
    assert first.recipe_failures == {}
    first.complete_recipes["option-1"] = complete_recipe()

    assert second.complete_recipes == {}
    assert first.complete_recipes is not second.complete_recipes
    assert first.recipe_failures is not second.recipe_failures


def test_session_model_copy_does_not_alias_recipe_dictionaries() -> None:
    session = session_with_recipe()

    copied = session.model_copy()
    copied.complete_recipes.clear()
    copied.recipe_failures.clear()

    assert list(session.complete_recipes) == ["option-1"]
    assert list(session.recipe_failures) == ["option-2"]


def test_session_response_exposes_detached_serializable_recipe_results() -> None:
    session = session_with_recipe()

    response = SessionResponse.model_validate(session)
    payload = response.model_dump(mode="json")

    assert payload["complete_recipes"]["option-1"]["steps"][0]["number"] == 1
    assert payload["recipe_failures"]["option-2"]["code"] == "model_output_invalid"

    response.complete_recipes.clear()
    response.recipe_failures.clear()
    assert list(session.complete_recipes) == ["option-1"]
    assert list(session.recipe_failures) == ["option-2"]
