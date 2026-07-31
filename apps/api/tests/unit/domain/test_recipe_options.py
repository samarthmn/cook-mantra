from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from domain.recipe_options import (
    Difficulty,
    IngredientRequirement,
    NutritionEstimate,
    RecipeOption,
    RecipeOptionBatch,
    RecipeOptionDraft,
    RecipePreferences,
    canonicalize_used_ingredients,
    reconcile_used_ingredients,
    validate_confirmed_used_ingredients,
    validate_unique_option_ingredient_names,
)
from domain.sessions import Session, SessionStage
from schemas.sessions import SessionResponse


def test_preferences_have_product_defaults() -> None:
    preferences = RecipePreferences()

    assert preferences.servings == 2
    assert preferences.option_count == 4
    assert preferences.dietary_preferences == []
    assert preferences.allergens == []


def test_preferences_reject_unknown_request_fields() -> None:
    with pytest.raises(ValidationError):
        RecipePreferences.model_validate(
            {"servings": 2, "optionCount": 6, "typo_field": True}
        )


def test_option_count_cannot_exceed_six() -> None:
    with pytest.raises(ValidationError):
        RecipePreferences(option_count=7)


@pytest.mark.parametrize("servings", [0, 13])
def test_preferences_reject_servings_outside_one_through_twelve(servings: int) -> None:
    with pytest.raises(ValidationError):
        RecipePreferences(servings=servings)


@pytest.mark.parametrize("maximum_minutes", [0, 1_441])
def test_preferences_reject_invalid_maximum_total_time(maximum_minutes: int) -> None:
    with pytest.raises(ValidationError):
        RecipePreferences(max_total_minutes=maximum_minutes)


@pytest.mark.parametrize(
    "field_name",
    ["dietary_preferences", "allergens", "preferred_cuisines"],
)
def test_preferences_trim_and_reject_case_insensitive_duplicates(
    field_name: str,
) -> None:
    with pytest.raises(ValidationError):
        RecipePreferences(**{field_name: ["  Vegetarian  ", "vegetarian"]})


def test_preferences_trim_preference_values() -> None:
    preferences = RecipePreferences(preferred_cuisines=["  Indian  "])

    assert preferences.preferred_cuisines == ["Indian"]


def test_preferences_limit_each_preference_list_to_twenty_values() -> None:
    with pytest.raises(ValidationError):
        RecipePreferences(allergens=[f"allergen-{index}" for index in range(21)])


def test_recipe_draft_requires_a_used_ingredient() -> None:
    with pytest.raises(ValidationError):
        RecipeOptionDraft(
            name="Tomato soup",
            summary="A quick soup.",
            cuisine="Italian",
            total_minutes=20,
            difficulty=Difficulty.EASY,
            used_ingredients=[],
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("name", "   "),
        ("cuisine", ""),
        ("used_ingredients", ["Tomato", "  "]),
    ],
)
def test_generated_recipe_batch_rejects_blank_text(
    field_name: str,
    field_value: str | list[str],
) -> None:
    values: dict[str, object] = {
        "name": "Tomato soup",
        "summary": "A quick soup.",
        "cuisine": "Italian",
        "total_minutes": 20,
        "difficulty": Difficulty.EASY,
        "used_ingredients": ["Tomato"],
    }
    values[field_name] = field_value

    with pytest.raises(ValidationError):
        RecipeOptionBatch(options=[RecipeOptionDraft(**values)])


def test_generated_recipe_batch_keeps_an_option_with_a_blank_summary() -> None:
    # A terse model drops the summary on a short ingredient list. The dish is
    # still real, so losing the whole batch over prose would leave the user with
    # no ideas at all.
    batch = RecipeOptionBatch(
        options=[
            RecipeOptionDraft(
                name="Tomato soup",
                summary="   ",
                cuisine="Italian",
                total_minutes=20,
                difficulty=Difficulty.EASY,
                used_ingredients=["Tomato"],
            )
        ]
    )

    assert batch.options[0].summary == ""


@pytest.mark.parametrize(
    ("used_ingredients", "missing_ingredients", "optional_ingredients"),
    [
        (["Tomato", " tomato "], [], []),
        (
            ["Tomato"],
            [{"name": " tomato ", "reason": "Needed for body."}],
            [],
        ),
        (
            ["Tomato"],
            [{"name": "Paneer", "reason": "Needed for body."}],
            [{"name": " paneer ", "reason": "Useful garnish."}],
        ),
    ],
)
def test_generated_recipe_batch_parses_repeated_ingredient_names(
    used_ingredients: list[str],
    missing_ingredients: list[dict[str, str]],
    optional_ingredients: list[dict[str, str]],
) -> None:
    """Duplicates must survive parsing so reconcile can collapse them.

    Rejecting at the structured-output boundary turns honest model echo
    (Salt in both used and missing) into a hard model_output_invalid.
    """
    batch = RecipeOptionBatch.model_validate(
        {
            "options": [
                {
                    "name": "Tomato soup",
                    "summary": "A quick soup.",
                    "cuisine": "Italian",
                    "total_minutes": 20,
                    "difficulty": Difficulty.EASY,
                    "used_ingredients": used_ingredients,
                    "missing_ingredients": missing_ingredients,
                    "optional_ingredients": optional_ingredients,
                }
            ]
        }
    )

    assert len(batch.options) == 1
    with pytest.raises(ValueError, match="Ingredient names must be unique"):
        validate_unique_option_ingredient_names(batch.options)


def test_reconcile_collapses_duplicate_ingredient_names_across_lists() -> None:
    option = RecipeOptionDraft(
        name="Bean stew",
        summary="A stew.",
        cuisine="Mexican",
        total_minutes=35,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato", " tomato ", "Salt"],
        missing_ingredients=[
            IngredientRequirement(name=" salt ", reason="Seasoning."),
            IngredientRequirement(name="Oil", reason="For frying."),
        ],
        optional_ingredients=[
            IngredientRequirement(name="Oil", reason="Finish."),
            IngredientRequirement(name="Cilantro", reason="Garnish."),
        ],
    )

    reconciled = reconcile_used_ingredients([option], ["Tomato", "Salt"])

    assert reconciled[0].used_ingredients == ["Tomato", "Salt"]
    assert [item.name for item in reconciled[0].missing_ingredients] == ["Oil"]
    assert [item.name for item in reconciled[0].optional_ingredients] == ["Cilantro"]
    validate_unique_option_ingredient_names(reconciled)


def test_nutrition_disclaimer_is_fixed_estimate_notice() -> None:
    with pytest.raises(ValidationError):
        NutritionEstimate(
            calories_kcal=240,
            protein_g=10,
            carbohydrates_g=32,
            fat_g=8,
            disclaimer="Nutrition facts.",
        )


def test_session_response_serializes_recipe_suggestion_state() -> None:
    session = Session(
        id="session-1",
        stage=SessionStage.OPTIONS_READY,
        preferences=RecipePreferences(option_count=1),
        recipe_options=[
            RecipeOption(
                name="Tomato soup",
                summary="A quick soup.",
                cuisine="Italian",
                total_minutes=20,
                difficulty=Difficulty.EASY,
                used_ingredients=["Tomato"],
            )
        ],
        excluded_recipe_names={"tomato soup"},
        option_batch_number=1,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        updated_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    response = SessionResponse.model_validate(session)

    assert response.preferences.option_count == 1
    assert response.recipe_options[0].name == "Tomato soup"
    assert response.excluded_recipe_names == {"tomato soup"}
    assert response.option_batch_number == 1


def test_confirmed_used_ingredient_validation_is_case_and_spacing_tolerant() -> None:
    option = RecipeOptionDraft(
        name="Tomato Curry",
        summary="A curry.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["  tomato ", "OIL OR GHEE"],
    )

    validate_confirmed_used_ingredients([option], ["Tomato", "Oil or ghee"])


def test_unconfirmed_used_ingredient_is_still_rejected() -> None:
    option = RecipeOptionDraft(
        name="Tomato Curry",
        summary="A curry.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Paneer"],
    )

    with pytest.raises(ValueError, match="confirmed"):
        validate_confirmed_used_ingredients([option], ["Tomato"])


def test_canonicalization_rewrites_to_the_confirmed_spelling() -> None:
    option = RecipeOptionDraft(
        name="Tomato Curry",
        summary="A curry.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["tomato", "oil or ghee", "white beans (cannellini)"],
    )

    canonical = canonicalize_used_ingredients(
        [option],
        ["Tomato", "Oil or ghee", "white beans (cannellini)"],
    )

    assert canonical[0].used_ingredients == [
        "Tomato",
        "Oil or ghee",
        "white beans (cannellini)",
    ]
    # The original draft is not mutated.
    assert option.used_ingredients == [
        "tomato",
        "oil or ghee",
        "white beans (cannellini)",
    ]


def test_canonicalization_rejects_genuinely_unconfirmed_ingredients() -> None:
    option = RecipeOptionDraft(
        name="Tomato Curry",
        summary="A curry.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Butter"],
    )

    with pytest.raises(ValueError, match="confirmed"):
        canonicalize_used_ingredients([option], ["Tomato"])
