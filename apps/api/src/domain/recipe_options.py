"""Domain models for generated recipe suggestions."""

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.images import DishPreview

NUTRITION_DISCLAIMER = "Estimated values; not medical advice."


def _normalize_preference_values(values: list[str]) -> list[str]:
    """Trim preference values and reject case-insensitive duplicates."""
    normalized_values: list[str] = []
    seen_values: set[str] = set()
    for value in values:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Preference values must not be blank.")
        normalized_key = normalized_value.casefold()
        if normalized_key in seen_values:
            raise ValueError("Preference values must be unique.")
        seen_values.add(normalized_key)
        normalized_values.append(normalized_value)
    return normalized_values


class Difficulty(StrEnum):
    """The estimated effort needed to prepare a recipe."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class RecipePreferences(BaseModel):
    """Optional constraints for a batch of recipe suggestions."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "dietary_preferences": ["vegetarian"],
                    "allergens": ["peanut"],
                    "preferred_cuisines": ["Italian"],
                    "max_total_minutes": 45,
                    "servings": 2,
                    "option_count": 4,
                }
            ]
        }
    )

    dietary_preferences: list[str] = Field(default_factory=list, max_length=20)
    allergens: list[str] = Field(default_factory=list, max_length=20)
    preferred_cuisines: list[str] = Field(default_factory=list, max_length=20)
    max_total_minutes: int | None = Field(default=None, ge=1, le=1_440)
    servings: int = Field(default=2, ge=1, le=12)
    option_count: int = Field(default=4, ge=1, le=6)

    @field_validator(
        "dietary_preferences", "allergens", "preferred_cuisines", mode="after"
    )
    @classmethod
    def normalize_preference_values(cls, values: list[str]) -> list[str]:
        """Keep each preference list meaningful and unambiguous."""
        return _normalize_preference_values(values)


class NutritionEstimate(BaseModel):
    """Estimated nutrition information for a recipe option."""

    calories_kcal: int = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbohydrates_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    diet_tags: list[str] = Field(default_factory=list)
    allergen_warnings: list[str] = Field(default_factory=list)
    disclaimer: str = NUTRITION_DISCLAIMER

    @field_validator("disclaimer")
    @classmethod
    def validate_disclaimer(cls, disclaimer: str) -> str:
        """Keep the nutrition notice explicitly non-medical."""
        if disclaimer != NUTRITION_DISCLAIMER:
            raise ValueError(f"Disclaimer must be {NUTRITION_DISCLAIMER!r}")
        return disclaimer


class IngredientRequirement(BaseModel):
    """An ingredient that is missing from or optional for a recipe."""

    name: str
    reason: str
    substitution: str | None = None


class RecipeOptionDraft(BaseModel):
    """A recipe suggestion before optional enrichment has completed."""

    name: str
    summary: str
    cuisine: str
    total_minutes: int = Field(ge=1, le=1_440)
    difficulty: Difficulty
    used_ingredients: list[str] = Field(min_length=1)
    missing_ingredients: list[IngredientRequirement] = Field(default_factory=list)
    optional_ingredients: list[IngredientRequirement] = Field(default_factory=list)


class RecipeOptionBatch(BaseModel):
    """A bounded non-empty collection of generated recipe drafts."""

    options: list[RecipeOptionDraft] = Field(min_length=1, max_length=6)


class RecipeOption(RecipeOptionDraft):
    """A stored recipe suggestion with optional enrichments."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    nutrition: NutritionEstimate | None = None
    preview: DishPreview | None = None
    warnings: list[str] = Field(default_factory=list)
