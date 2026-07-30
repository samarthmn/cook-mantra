import pytest
from pydantic import ValidationError

from domain.ingredients import (
    DetectedIngredient,
    ExtractionResult,
    Ingredient,
    IngredientSource,
    assemble_review_ingredients,
)
from schemas.ingredients import IngredientResponse


def test_detected_duplicates_are_normalized() -> None:
    result = ExtractionResult(
        detected=[
            DetectedIngredient(name=" Tomato ", confidence=0.91),
            DetectedIngredient(name="tomato", confidence=0.80),
        ]
    )

    ingredients = assemble_review_ingredients(result)
    tomatoes = [item for item in ingredients if item.name == "Tomato"]

    assert len(tomatoes) == 1
    assert tomatoes[0].source is IngredientSource.DETECTED
    assert tomatoes[0].confidence == 0.91


def test_pantry_suggestions_are_separate_and_unconfirmed() -> None:
    ingredients = assemble_review_ingredients(ExtractionResult(detected=[]))
    salt = next(item for item in ingredients if item.name == "Salt")

    assert salt.source is IngredientSource.PANTRY_SUGGESTION
    assert salt.confirmed is False
    assert salt.confidence is None


@pytest.mark.parametrize(
    ("source", "confidence"),
    [
        (IngredientSource.DETECTED, None),
        (IngredientSource.PANTRY_SUGGESTION, 0.7),
        (IngredientSource.USER_ADDED, 0.7),
    ],
)
def test_ingredient_source_controls_whether_confidence_is_allowed(
    source: IngredientSource, confidence: float | None
) -> None:
    with pytest.raises(ValidationError):
        Ingredient(name="Tomato", source=source, confidence=confidence)

    with pytest.raises(ValidationError):
        IngredientResponse(name="Tomato", source=source, confidence=confidence)


def test_detected_ingredients_reject_whitespace_only_names() -> None:
    with pytest.raises(ValidationError):
        DetectedIngredient(name="   ", confidence=0.9)
