"""Public API schemas for recipe suggestions."""

from pydantic import ConfigDict

from domain.images import DishPreview
from domain.recipe_options import RecipeOption

RECIPE_OPTION_EXAMPLE = {
    "id": "option-123",
    "name": "Tomato Basil Pasta",
    "summary": "A quick pasta using tomatoes and fresh basil.",
    "cuisine": "Italian",
    "total_minutes": 30,
    "difficulty": "easy",
    "used_ingredients": ["Tomato", "Fresh basil"],
    "missing_ingredients": [
        {
            "name": "Pasta",
            "reason": "Forms the base of the dish.",
            "substitution": "Use any short pasta.",
        }
    ],
    "optional_ingredients": [
        {
            "name": "Parmesan",
            "reason": "Adds a savoury finish.",
            "substitution": None,
        }
    ],
    "nutrition": {
        "calories_kcal": 520,
        "protein_g": 18.0,
        "carbohydrates_g": 78.0,
        "fat_g": 15.0,
        "diet_tags": ["vegetarian"],
        "allergen_warnings": ["wheat"],
        "disclaimer": "Estimated values; not medical advice.",
    },
    "preview": {
        "artifact_id": "artifact-preview-123",
        "label": "AI-generated illustration",
    },
    "warnings": [],
}


class RecipeOptionResponse(RecipeOption):
    """Public representation of a generated recipe suggestion."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [RECIPE_OPTION_EXAMPLE]},
    )

    preview: DishPreview | None = None
