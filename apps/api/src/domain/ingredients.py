"""Domain models and rules for ingredient extraction review."""

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.errors import AppError, ErrorCode


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


class IngredientDraft(BaseModel):
    """The user-editable portion of an ingredient under review."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    confirmed: bool

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Normalize the submitted ingredient name."""
        return _normalize_ingredient_name(name)


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


def apply_ingredient_review(
    existing: list[Ingredient], drafts: list[IngredientDraft]
) -> list[Ingredient]:
    """Reconcile user edits while retaining metadata that belongs to the server."""
    existing_by_id = {ingredient.id: ingredient for ingredient in existing}
    submitted_ids: set[str] = set()
    submitted_names: set[str] = set()
    reviewed: list[Ingredient] = []

    for draft in drafts:
        normalized_name = draft.name.casefold()
        if normalized_name in submitted_names:
            _raise_invalid_review("Duplicate ingredient name in review.")
        submitted_names.add(normalized_name)

        if draft.id is None:
            reviewed.append(
                Ingredient(
                    name=draft.name,
                    source=IngredientSource.USER_ADDED,
                    confirmed=draft.confirmed,
                )
            )
            continue

        if draft.id in submitted_ids:
            _raise_invalid_review("Duplicate ingredient ID in review.")
        submitted_ids.add(draft.id)

        stored_ingredient = existing_by_id.get(draft.id)
        if stored_ingredient is None:
            _raise_invalid_review(f"Unknown ingredient ID: {draft.id}")
        reviewed.append(
            stored_ingredient.model_copy(
                update={"name": draft.name, "confirmed": draft.confirmed}
            )
        )

    return reviewed


def _raise_invalid_review(message: str) -> None:
    raise AppError(
        code=ErrorCode.INVALID_REQUEST,
        message=message,
        status_code=422,
        retryable=False,
    )
