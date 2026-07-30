"""Public API schemas for cooking sessions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from domain.recipe_options import RecipePreferences
from domain.sessions import SessionStage
from schemas.ingredients import IngredientResponse
from schemas.recipe_options import RecipeOptionResponse

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


class SessionCreatedResponse(BaseModel):
    """Identifiers returned after ingredient extraction is queued."""

    session_id: str
    job_id: str


class SessionResponse(BaseModel):
    """Initial public representation of a cooking session."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    stage: SessionStage
    image_artifact_id: str | None
    ingredients: list[IngredientResponse]
    preferences: RecipePreferences
    recipe_options: list[RecipeOptionResponse]
    excluded_recipe_names: set[str]
    option_batch_number: int
    warnings: list[str]
    created_at: datetime
    updated_at: datetime
