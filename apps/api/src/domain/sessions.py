"""Domain models for a user's cooking session."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from domain.ingredients import Ingredient
from domain.recipe_options import RecipeOption, RecipePreferences


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
    preferences: RecipePreferences = Field(default_factory=RecipePreferences)
    recipe_options: list[RecipeOption] = Field(default_factory=list)
    excluded_recipe_names: set[str] = Field(default_factory=set)
    option_batch_number: int = Field(default=0, ge=0)
    option_generation_id: str | None = Field(default=None, repr=False)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
