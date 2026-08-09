"""Public API schemas for complete recipes."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.images import DishPreview
from domain.recipes import CompleteRecipe, RecipeFailure

COMPLETE_RECIPE_EXAMPLE = {
    "option_id": "option-123",
    "name": "Tomato Basil Pasta",
    "cuisine": "Italian",
    "servings": 2,
    "total_minutes": 30,
    "ingredients": [
        {
            "name": "Tomato",
            "quantity": "3 medium",
            "availability": "available",
            "substitution": None,
        },
        {
            "name": "Pasta",
            "quantity": "200 g",
            "availability": "missing",
            "substitution": "Use any short pasta.",
        },
    ],
    "steps": [
        {
            "number": 1,
            "instruction": (
                "Boil the pasta in well-salted water, stirring once so it does not "
                "stick. Taste a piece near the end; it should retain a gentle bite."
            ),
            "duration_minutes": 10,
            "done_when": "pasta tender with a gentle bite at the center",
            "heat_level": "high",
        },
        {
            "number": 2,
            "instruction": (
                "Saute the tomatoes until they slump and smell sweet, then emulsify "
                "the sauce with the pasta and a splash of reserved cooking water."
            ),
            "duration_minutes": 15,
            "done_when": "sauce glossy and clinging evenly to the pasta",
            "heat_level": "medium",
        },
    ],
    "tips": ["Reserve a little pasta water for the sauce."],
    "substitutions": ["Use coriander if basil is unavailable."],
    "nutrition": {
        "calories_kcal": 420,
        "protein_g": 16.5,
        "carbohydrates_g": 55.0,
        "fat_g": 14.0,
        "diet_tags": ["vegetarian"],
        "allergen_warnings": ["wheat/gluten"],
        "disclaimer": "Estimated values; not medical advice.",
    },
    "nutrition_notice": "Estimated values; not medical advice.",
    "allergen_notice": "Check ingredient labels for allergens.",
    "preview": None,
    "assumptions": ["Salt and cooking oil are available."],
    "warnings": ["Pasta must be purchased before cooking."],
}

RECIPE_FAILURE_EXAMPLE = {
    "option_id": "option-456",
    "code": "model_output_invalid",
    "message": "The recipe could not be generated.",
    "retryable": False,
}


def _normalize_option_id(option_id: str) -> str:
    normalized_id = option_id.strip()
    if not normalized_id:
        raise ValueError("Recipe option IDs must not be blank.")
    return normalized_id


class RecipeSelectionRequest(BaseModel):
    """A bounded selection of unique server-owned recipe options."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"option_ids": ["option-123", "option-456"]}]},
    )

    option_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("option_ids")
    @classmethod
    def normalize_unique_option_ids(cls, option_ids: list[str]) -> list[str]:
        """Normalize option IDs and reject duplicate selections."""
        normalized_ids = [_normalize_option_id(option_id) for option_id in option_ids]
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("Recipe option IDs must be unique.")
        return normalized_ids


class CompleteRecipeResponse(CompleteRecipe):
    """Public representation of a complete recipe."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [COMPLETE_RECIPE_EXAMPLE]},
    )

    preview: DishPreview | None


class RecipeFailureResponse(RecipeFailure):
    """Public representation of one selected option failure."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [RECIPE_FAILURE_EXAMPLE]},
    )
