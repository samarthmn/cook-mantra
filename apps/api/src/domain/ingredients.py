"""Domain models and rules for ingredient extraction review."""

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class IngredientSource(StrEnum):
    """How an ingredient entered the cooking session."""

    DETECTED = "detected"
    PANTRY_SUGGESTION = "pantry_suggestion"
    USER_ADDED = "user_added"


class DetectedIngredient(BaseModel):
    """An ingredient identified from an uploaded image."""

    name: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)


class Ingredient(BaseModel):
    """An ingredient available for user review and confirmation."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    source: IngredientSource
    confidence: float | None = None
    confirmed: bool = False


class ExtractionResult(BaseModel):
    """The visible ingredients and warnings returned by extraction."""

    detected: list[DetectedIngredient]
    warnings: list[str] = Field(default_factory=list)


PANTRY_SUGGESTIONS = (
    "Salt",
    "Pepper powder",
    "Oil or ghee",
    "Chilli powder",
    "Onion",
    "Garlic",
    "Ginger",
)


def assemble_review_ingredients(result: ExtractionResult) -> list[Ingredient]:
    """Combine unique detections with separate, unconfirmed pantry suggestions."""
    detected_by_name: dict[str, DetectedIngredient] = {}
    for detected in result.detected:
        normalized_name = detected.name.strip()
        key = normalized_name.casefold()
        existing = detected_by_name.get(key)
        if existing is None or detected.confidence > existing.confidence:
            detected_by_name[key] = detected.model_copy(
                update={"name": normalized_name}
            )

    ingredients = [
        Ingredient(
            name=detected.name,
            source=IngredientSource.DETECTED,
            confidence=detected.confidence,
        )
        for detected in detected_by_name.values()
    ]
    ingredients.extend(
        Ingredient(name=name, source=IngredientSource.PANTRY_SUGGESTION)
        for name in PANTRY_SUGGESTIONS
        if name.casefold() not in detected_by_name
    )
    return ingredients
