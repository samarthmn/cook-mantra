"""Public API schemas for ingredients."""

from pydantic import ConfigDict

from domain.ingredients import Ingredient


class IngredientResponse(Ingredient):
    """Public representation of an ingredient under review."""

    model_config = ConfigDict(from_attributes=True)
