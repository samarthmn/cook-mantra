import pytest
from pydantic import ValidationError

from core.errors import AppError
from domain.ingredients import (
    Ingredient,
    IngredientDraft,
    IngredientSource,
    apply_ingredient_review,
)
from schemas.ingredients import IngredientReviewRequest


def test_existing_rename_preserves_server_owned_metadata() -> None:
    detected = Ingredient(
        id="ingredient-1",
        name="Tomato",
        source=IngredientSource.DETECTED,
        confidence=0.91,
    )

    reviewed = apply_ingredient_review(
        [detected],
        [IngredientDraft(id="ingredient-1", name="Cherry tomato", confirmed=True)],
    )

    assert reviewed[0].source is IngredientSource.DETECTED
    assert reviewed[0].confidence == 0.91
    assert reviewed[0].name == "Cherry tomato"
    assert reviewed[0].confirmed is True


def test_new_item_is_user_added() -> None:
    reviewed = apply_ingredient_review(
        [],
        [IngredientDraft(name="Spinach", confirmed=True)],
    )

    assert reviewed[0].source is IngredientSource.USER_ADDED
    assert reviewed[0].confidence is None


def test_omitted_existing_item_is_removed() -> None:
    existing = [
        Ingredient(
            id="ingredient-1",
            name="Tomato",
            source=IngredientSource.DETECTED,
            confidence=0.91,
        ),
        Ingredient(
            id="ingredient-2",
            name="Onion",
            source=IngredientSource.PANTRY_SUGGESTION,
        ),
    ]

    reviewed = apply_ingredient_review(
        existing,
        [IngredientDraft(id="ingredient-1", name="Tomato", confirmed=True)],
    )

    assert [item.id for item in reviewed] == ["ingredient-1"]


def test_unknown_existing_id_is_rejected() -> None:
    with pytest.raises(AppError, match="Unknown ingredient"):
        apply_ingredient_review(
            [],
            [IngredientDraft(id="missing", name="Spinach", confirmed=True)],
        )


def test_duplicate_existing_id_is_rejected() -> None:
    existing = [
        Ingredient(
            id="ingredient-1",
            name="Tomato",
            source=IngredientSource.DETECTED,
            confidence=0.91,
        )
    ]

    with pytest.raises(AppError, match="Duplicate ingredient ID"):
        apply_ingredient_review(
            existing,
            [
                IngredientDraft(id="ingredient-1", name="Tomato", confirmed=True),
                IngredientDraft(
                    id="ingredient-1", name="Cherry tomato", confirmed=True
                ),
            ],
        )


def test_duplicate_normalized_names_are_rejected() -> None:
    with pytest.raises(AppError, match="Duplicate ingredient name"):
        apply_ingredient_review(
            [],
            [
                IngredientDraft(name="Spinach", confirmed=True),
                IngredientDraft(name=" spinach ", confirmed=False),
            ],
        )


def test_blank_draft_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Ingredient name must not be blank"):
        IngredientDraft(name="   ", confirmed=True)


def test_review_request_rejects_more_than_100_submitted_ingredients() -> None:
    with pytest.raises(ValidationError):
        IngredientReviewRequest(
            ingredients=[
                {"name": f"ingredient-{index}", "confirmed": True}
                for index in range(101)
            ]
        )


@pytest.mark.parametrize("server_owned_field", ["source", "confidence"])
def test_review_request_rejects_server_owned_fields(
    server_owned_field: str,
) -> None:
    with pytest.raises(ValidationError):
        IngredientReviewRequest(
            ingredients=[
                {
                    "name": "Spinach",
                    "confirmed": True,
                    server_owned_field: "detected"
                    if server_owned_field == "source"
                    else 0.91,
                }
            ]
        )
