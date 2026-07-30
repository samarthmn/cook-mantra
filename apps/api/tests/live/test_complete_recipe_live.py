import os

import pytest

from agents.specialized_recipe import OllamaSpecializedRecipeAgent
from core.config import Settings
from domain.recipe_options import (
    Difficulty,
    IngredientRequirement,
    RecipeOption,
    RecipePreferences,
)
from domain.recipes import IngredientAvailability

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("COOK_MANTRA_RUN_LIVE") != "1",
        reason="Set COOK_MANTRA_RUN_LIVE=1 to run the real specialized recipe agent.",
    ),
]


@pytest.mark.asyncio
async def test_real_specialized_agent_returns_a_structurally_cookable_recipe() -> None:
    option = RecipeOption(
        id="live-tomato-curry",
        name="Tomato Curry",
        summary="A warming tomato curry.",
        cuisine="Indian",
        total_minutes=35,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato", "Onion"],
        missing_ingredients=[
            IngredientRequirement(
                name="Garam masala",
                reason="Adds the curry spice profile.",
                substitution="Curry powder",
            )
        ],
    )

    recipe = await OllamaSpecializedRecipeAgent(
        settings=Settings(_env_file=None)
    ).generate(
        option,
        ["Tomato", "Onion"],
        RecipePreferences(servings=2, option_count=1),
    )

    assert recipe.servings == 2
    assert all(ingredient.quantity for ingredient in recipe.ingredients)
    assert [step.number for step in recipe.steps] == list(
        range(1, len(recipe.steps) + 1)
    )
    assert any(
        ingredient.name == "Garam masala"
        and ingredient.availability is IngredientAvailability.MISSING
        for ingredient in recipe.ingredients
    )
