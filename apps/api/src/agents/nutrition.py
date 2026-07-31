"""Ollama adapter for structured recipe nutrition estimates."""

from typing import Protocol

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from core import Agent, Settings
from domain.recipe_options import (
    NUTRITION_DISCLAIMER,
    IngredientRequirement,
    NutritionEstimate,
    RecipeOptionDraft,
    RecipePreferences,
)
from services.llm import get_model
from services.structured_output import StructuredModel, invoke_structured
from services.tracing import RunnableConfig, TracingService


class NutritionAgent(Protocol):
    """Estimate nutrition for one recipe option."""

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: RecipePreferences,
    ) -> NutritionEstimate:
        raise NotImplementedError


class NutritionModelOutput(BaseModel):
    """Nutrition fields accepted from the model before product normalization."""

    model_config = ConfigDict(extra="ignore")

    calories_kcal: int = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbohydrates_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    diet_tags: list[str] = Field(default_factory=list)
    allergen_warnings: list[str] = Field(default_factory=list)


class OllamaNutritionAgent:
    """Estimate recipe nutrition with the configured Nutrition model."""

    def __init__(
        self,
        model: StructuredModel[NutritionModelOutput] | None = None,
        settings: Settings | None = None,
        tracing: TracingService | None = None,
    ) -> None:
        self._model = model
        self._settings = settings
        self._tracing = tracing

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: RecipePreferences,
    ) -> NutritionEstimate:
        """Return a per-serving estimate with the product disclaimer."""
        model = self._model
        if model is None:
            model = get_model(
                Agent.NUTRITION,
                thinking=False,
                settings=self._settings,
            ).with_structured_output(NutritionModelOutput)

        messages = [HumanMessage(content=_build_prompt(option, preferences))]

        async def invoke(config: RunnableConfig) -> NutritionModelOutput:
            return await invoke_structured(model, messages, config=config)

        if self._tracing is None:
            estimate = await invoke_structured(model, messages)
        else:
            estimate = await self._tracing.invoke_text(Agent.NUTRITION, invoke)
        return NutritionEstimate(
            **estimate.model_dump(exclude={"disclaimer"}),
            disclaimer=NUTRITION_DISCLAIMER,
        )


def _build_prompt(option: RecipeOptionDraft, preferences: RecipePreferences) -> str:
    """Build the instruction for an honest per-serving estimate."""
    used_ingredients = ", ".join(option.used_ingredients)
    missing_ingredients = _format_requirements(option.missing_ingredients)
    optional_ingredients = _format_requirements(option.optional_ingredients)
    dietary_preferences = ", ".join(preferences.dietary_preferences) or "(none)"
    allergens = ", ".join(preferences.allergens) or "(none)"

    return f"""You are Cook Mantra's Nutrition Agent. Estimate this recipe per serving.
Recipe name: {option.name}
Recipe summary: {option.summary}
Cuisine: {option.cuisine}
Total minutes: {option.total_minutes}
Servings: {preferences.servings}
Used ingredients: {used_ingredients}
Missing ingredients: {missing_ingredients}
Optional ingredients: {optional_ingredients}
Dietary preferences: {dietary_preferences}
User allergens: {allergens}

Return estimated per-serving calories and macronutrients. Include useful diet tags
and likely allergen warnings, especially for the user's allergens. Make uncertainty
explicit: these are approximate estimates, not precise measurements or medical advice.
Base the estimate on the recipe information and do not assume unlisted ingredients."""


def _format_requirements(requirements: list[IngredientRequirement]) -> str:
    """Render optional and missing recipe ingredients for the model prompt."""
    return ", ".join(str(requirement) for requirement in requirements) or "(none)"
