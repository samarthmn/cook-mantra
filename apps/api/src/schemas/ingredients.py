"""Public API schemas for ingredients."""

from pydantic import BaseModel, ConfigDict, Field

from domain.ingredients import Ingredient, IngredientDraft

INGREDIENT_RESPONSE_EXAMPLE = {
    "id": "ingredient-123",
    "name": "Tomato",
    "source": "detected",
    "confidence": 0.94,
    "confirmed": True,
}


class IngredientResponse(Ingredient):
    """Public representation of an ingredient under review."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [INGREDIENT_RESPONSE_EXAMPLE]},
    )


class IngredientReviewRequest(BaseModel):
    """An ingredient-review request containing only client-editable fields."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "ingredients": [
                        {
                            "id": "ingredient-123",
                            "name": "Tomato",
                            "confirmed": True,
                        },
                        {
                            "id": None,
                            "name": "Fresh basil",
                            "confirmed": True,
                        },
                    ]
                }
            ]
        },
    )

    ingredients: list[IngredientDraft] = Field(max_length=100)
