"""Domain models and rules for ingredient extraction review."""

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalize_ingredient_name(name: str) -> str:
    """Remove surrounding whitespace while requiring a visible name."""
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Ingredient name must not be blank.")
    return normalized_name


class IngredientSource(StrEnum):
    """How an ingredient entered the cooking session."""

    DETECTED = "detected"
    PANTRY_SUGGESTION = "pantry_suggestion"
    USER_ADDED = "user_added"


class DetectedIngredient(BaseModel):
    """An ingredient identified from an uploaded image."""

    name: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Normalize the detected ingredient name."""
        return _normalize_ingredient_name(name)


class Ingredient(BaseModel):
    """An ingredient available for user review and confirmation."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    source: IngredientSource
    confidence: float | None = None
    confirmed: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Normalize the ingredient name."""
        return _normalize_ingredient_name(name)

    @model_validator(mode="after")
    def validate_confidence_for_source(self) -> "Ingredient":
        """Allow confidence only for ingredients detected from an image."""
        if self.source is IngredientSource.DETECTED and self.confidence is None:
            raise ValueError("Detected ingredients require confidence.")
        if self.source is not IngredientSource.DETECTED and self.confidence is not None:
            raise ValueError("Only detected ingredients may include confidence.")
        return self


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
        key = detected.name.casefold()
        existing = detected_by_name.get(key)
        if existing is None or detected.confidence > existing.confidence:
            detected_by_name[key] = detected

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
