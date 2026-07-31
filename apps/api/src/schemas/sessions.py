"""Public API schemas for cooking sessions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from domain.recipe_options import RecipePreferences
from domain.sessions import SessionStage
from schemas.ingredients import IngredientResponse
from schemas.recipe_options import RECIPE_OPTION_EXAMPLE, RecipeOptionResponse
from schemas.recipes import (
    COMPLETE_RECIPE_EXAMPLE,
    RECIPE_FAILURE_EXAMPLE,
    CompleteRecipeResponse,
    RecipeFailureResponse,
)

INGREDIENT_REVIEW_RESPONSE_EXAMPLES = {
    "reviewedIngredients": {
        "summary": "Reviewed ingredients from every supported source",
        "value": {
            "id": "session-123",
            "stage": "reviewing_ingredients",
            "image_artifact_id": "artifact-123",
            "ingredients": [
                {
                    "id": "ingredient-detected",
                    "name": "Cherry tomato",
                    "source": "detected",
                    "confidence": 0.91,
                    "confirmed": True,
                },
                {
                    "id": "ingredient-pantry",
                    "name": "Salt",
                    "source": "pantry_suggestion",
                    "confidence": None,
                    "confirmed": False,
                },
                {
                    "id": "ingredient-user",
                    "name": "Spinach",
                    "source": "user_added",
                    "confidence": None,
                    "confirmed": True,
                },
            ],
            "warnings": [],
            "created_at": "2026-07-30T12:00:00Z",
            "updated_at": "2026-07-30T12:05:00Z",
        },
    }
}

INGREDIENT_CONFIRMATION_RESPONSE_EXAMPLES = {
    "confirmedIngredients": {
        "summary": "Confirmed ingredients from every supported source",
        "value": {
            **INGREDIENT_REVIEW_RESPONSE_EXAMPLES["reviewedIngredients"]["value"],
            "stage": "ingredients_confirmed",
            "updated_at": "2026-07-30T12:06:00Z",
        },
    }
}


class QueuedJobResponse(BaseModel):
    """Identifiers returned after a background job is queued."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"session_id": "session-123", "job_id": "job-123"}]
        }
    )

    session_id: str
    job_id: str


class SessionResponse(BaseModel):
    """Initial public representation of a cooking session."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "session-123",
                    "stage": "recipes_ready",
                    "image_artifact_id": "artifact-upload-123",
                    "ingredients": [
                        {
                            "id": "ingredient-123",
                            "name": "Tomato",
                            "source": "detected",
                            "confidence": 0.94,
                            "confirmed": True,
                        }
                    ],
                    "preferences": {
                        "dietary_preferences": ["vegetarian"],
                        "allergens": [],
                        "preferred_cuisines": ["Italian"],
                        "max_total_minutes": 45,
                        "servings": 2,
                        "option_count": 4,
                    },
                    "recipe_options": [RECIPE_OPTION_EXAMPLE],
                    "complete_recipes": {
                        "option-123": COMPLETE_RECIPE_EXAMPLE,
                    },
                    "recipe_failures": {
                        "option-456": RECIPE_FAILURE_EXAMPLE,
                    },
                    "excluded_recipe_names": ["Tomato Basil Pasta"],
                    "option_batch_number": 1,
                    "warnings": [],
                    "created_at": "2026-07-30T12:00:00Z",
                    "updated_at": "2026-07-30T12:10:00Z",
                }
            ]
        },
    )

    id: str
    stage: SessionStage
    image_artifact_id: str | None
    ingredients: list[IngredientResponse]
    preferences: RecipePreferences
    recipe_options: list[RecipeOptionResponse]
    complete_recipes: dict[str, CompleteRecipeResponse]
    recipe_failures: dict[str, RecipeFailureResponse]
    excluded_recipe_names: set[str]
    option_batch_number: int
    warnings: list[str]
    created_at: datetime
    updated_at: datetime
