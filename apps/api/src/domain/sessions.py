"""Domain models for a user's cooking session."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from domain.ingredients import Ingredient


class SessionStage(StrEnum):
    """Stages in the cooking-session workflow."""

    EXTRACTING = "extracting"
    REVIEWING_INGREDIENTS = "reviewing_ingredients"
    INGREDIENTS_CONFIRMED = "ingredients_confirmed"
    GENERATING_OPTIONS = "generating_options"
    OPTIONS_READY = "options_ready"
    GENERATING_RECIPES = "generating_recipes"
    RECIPES_READY = "recipes_ready"


class Session(BaseModel):
    """The current state of a cooking session."""

    id: str
    stage: SessionStage = SessionStage.EXTRACTING
    image_artifact_id: str | None = None
    ingredients: list[Ingredient] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
