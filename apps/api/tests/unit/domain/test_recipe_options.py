from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from domain.recipe_options import (
    Difficulty,
    NutritionEstimate,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
)
from domain.sessions import Session, SessionStage
from schemas.sessions import SessionResponse


def test_preferences_have_product_defaults() -> None:
    preferences = RecipePreferences()

    assert preferences.servings == 2
    assert preferences.option_count == 4
    assert preferences.dietary_preferences == []
    assert preferences.allergens == []


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
