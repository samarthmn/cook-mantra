from domain.ingredients import (
    DetectedIngredient,
    ExtractionResult,
    IngredientSource,
    assemble_review_ingredients,
)


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
