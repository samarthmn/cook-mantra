"""Ollama adapter for structured recipe-option generation."""

from typing import Protocol

from langchain_core.messages import HumanMessage

from core import Agent, Settings
from core.errors import AppError, ErrorCode
from domain.recipe_options import (
    RecipeOptionBatch,
    RecipeOptionDraft,
    RecipePreferences,
)
from services.llm import get_model
from services.structured_output import StructuredModel, invoke_structured


class MasterChef(Protocol):
    """Generate recipe-option drafts from confirmed ingredients."""

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        raise NotImplementedError


class OllamaMasterChef:
    """Generate recipe options with the configured Master Chef model."""

    def __init__(
        self,
        model: StructuredModel[RecipeOptionBatch] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._model = model
        self._settings = settings

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        """Return the requested number of honest, distinct recipe options."""
        model = self._model
        if model is None:
            model = get_model(
                Agent.MASTER_CHEF,
                settings=self._settings,
            ).with_structured_output(RecipeOptionBatch)

        batch = await invoke_structured(
            model,
            [
                HumanMessage(
                    content=_build_prompt(ingredients, preferences, excluded_names)
                )
            ],
        )
        if len(batch.options) != preferences.option_count:
            raise AppError(
                code=ErrorCode.MODEL_OUTPUT_INVALID,
                message="The model returned the wrong number of recipe options.",
                status_code=502,
                retryable=True,
            )
        return batch.options


def _build_prompt(
    ingredients: list[str],
    preferences: RecipePreferences,
    excluded_names: set[str],
) -> str:
    """Build the constrained recipe-generation instruction."""
    confirmed_ingredients = ", ".join(ingredients) or "(none)"
    exclusions = ", ".join(sorted(excluded_names)) or "(none)"
    dietary_preferences = ", ".join(preferences.dietary_preferences) or "(none)"
    allergens = ", ".join(preferences.allergens) or "(none)"
    cuisines = ", ".join(preferences.preferred_cuisines) or "(none)"
    maximum_time = preferences.max_total_minutes or "(not specified)"

    return f"""You are Cook Mantra's Master Chef. Generate recipe option drafts.
Only supplied confirmed items are available.
Do not treat any other ingredient as available.
Confirmed ingredients: {confirmed_ingredients}
Dietary preferences: {dietary_preferences}
Allergens to avoid: {allergens}
Preferred cuisines: {cuisines}
Maximum total minutes: {maximum_time}
Servings: {preferences.servings}
Excluded normalized recipe names: {exclusions}

Respect the preferences wherever possible. If preferences conflict with the supplied
confirmed ingredients, surface the conflict honestly. Every option must include a
country or cuisine. Avoid every excluded normalized recipe name. List missing and
optional ingredients honestly, and give a substitution for a missing ingredient when
one is feasible. Return exactly {preferences.option_count} options."""
