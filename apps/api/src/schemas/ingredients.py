"""Public API schemas for ingredients."""

from pydantic import BaseModel, ConfigDict

from domain.ingredients import IngredientSource


class IngredientResponse(BaseModel):
    """Public representation of an ingredient under review."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    source: IngredientSource
    confidence: float | None
    confirmed: bool
