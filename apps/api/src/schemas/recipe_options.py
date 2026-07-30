"""Public API schemas for recipe suggestions."""

from pydantic import ConfigDict

from domain.recipe_options import RecipeOption


class RecipeOptionResponse(RecipeOption):
    """Public representation of a generated recipe suggestion."""

    model_config = ConfigDict(from_attributes=True)
