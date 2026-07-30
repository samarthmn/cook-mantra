"""Public API schemas for ingredients."""

from pydantic import BaseModel, ConfigDict, Field

from domain.ingredients import Ingredient, IngredientDraft


class IngredientResponse(Ingredient):
    """Public representation of an ingredient under review."""

    model_config = ConfigDict(from_attributes=True)


class IngredientReviewRequest(BaseModel):
    """An ingredient-review request containing only client-editable fields."""

    model_config = ConfigDict(extra="forbid")

    ingredients: list[IngredientDraft] = Field(max_length=100)
