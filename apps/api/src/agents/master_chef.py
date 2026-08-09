"""Provider-neutral structured recipe-option generation agent."""

from typing import Protocol

from pydantic import Field

from core import Agent, Settings
from core.errors import AppError, ErrorCode
from core.runtime_config import editable_instruction_for, get_runtime_snapshot
from domain.model_prompts import compose_model_prompt
from domain.model_runtime import AgentRole, ModelMessage
from domain.recipe_options import (
    RecipeOptionBatch,
    RecipeOptionDraft,
    RecipePreferences,
    reconcile_used_ingredients,
)
from services.model_runtime import RuntimeStructuredModelFactory, StructuredModelFactory
from services.structured_output import StructuredModel, invoke_structured
from services.tracing import RunnableConfig, TracingService


class MasterChef(Protocol):
    """Generate recipe-option drafts from confirmed ingredients."""

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        raise NotImplementedError


class MasterChefAgent:
    """Generate recipe options with the configured role-level model."""

    def __init__(
        self,
        model: StructuredModel[RecipeOptionBatch] | None = None,
        settings: Settings | None = None,
        tracing: TracingService | None = None,
        editable_instruction: str | None = None,
        model_factory: StructuredModelFactory | None = None,
    ) -> None:
        self._model = model
        self._settings = settings
        self._tracing = tracing
        self._editable_instruction = editable_instruction
        self._model_factory = model_factory

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        """Return the requested number of honest, distinct recipe options."""
        model = self._model
        if model is None:
            batch_schema = _recipe_option_batch_schema(
                preferences.option_count,
                ingredients,
            )
            factory = self._model_factory or RuntimeStructuredModelFactory(
                get_runtime_snapshot()
            )
            model = factory.build(AgentRole.MASTER_CHEF, batch_schema)

        messages = [
            ModelMessage(
                role="user",
                content=_build_prompt(
                    ingredients,
                    preferences,
                    excluded_names,
                    editable_instruction=(
                        self._editable_instruction
                        if self._editable_instruction is not None
                        else editable_instruction_for(AgentRole.MASTER_CHEF)
                    ),
                ),
            )
        ]

        async def invoke(config: RunnableConfig) -> RecipeOptionBatch:
            return await invoke_structured(model, messages, config=config)

        if self._tracing is None:
            batch = await invoke_structured(model, messages)
        else:
            batch = await self._tracing.invoke_text(Agent.MASTER_CHEF, invoke)
        return _reconciled_options(batch, preferences.option_count, ingredients)


def _recipe_option_batch_schema(
    option_count: int,
    confirmed_ingredients: list[str],
) -> type[RecipeOptionBatch]:
    """Build the request-specific structured-output boundary."""

    class RequestedRecipeOptionBatch(RecipeOptionBatch):
        # Only the structural count constraint belongs in the parsing schema.
        # Ingredient-name enforcement happens after parsing, in
        # reconcile_used_ingredients: failing the parse over a name variant
        # would turn honest output into model_output_invalid.
        options: list[RecipeOptionDraft] = Field(
            min_length=option_count,
            max_length=option_count,
        )

    RequestedRecipeOptionBatch.__name__ = f"RecipeOptionBatchExact{option_count}"
    return RequestedRecipeOptionBatch


def _reconciled_options(
    batch: RecipeOptionBatch,
    option_count: int,
    confirmed_ingredients: list[str],
) -> list[RecipeOptionDraft]:
    """Apply the count contract and the confirmation boundary to one batch."""
    if len(batch.options) != option_count:
        raise AppError(
            code=ErrorCode.MODEL_OUTPUT_INVALID,
            message="The model returned the wrong number of recipe options.",
            status_code=502,
            retryable=True,
        )
    try:
        return reconcile_used_ingredients(batch.options, confirmed_ingredients)
    except ValueError:
        raise AppError(
            code=ErrorCode.MODEL_OUTPUT_INVALID,
            message="A generated option uses none of the confirmed ingredients.",
            status_code=502,
            retryable=True,
        ) from None


def _build_prompt(
    ingredients: list[str],
    preferences: RecipePreferences,
    excluded_names: set[str],
    *,
    editable_instruction: str = "",
) -> str:
    """Build the constrained recipe-generation instruction."""
    input_data = {
        "confirmed_ingredients": ingredients,
        "excluded_normalized_recipe_names": sorted(excluded_names),
        "preferences": preferences.model_dump(mode="json"),
    }
    option_count = preferences.option_count

    protected_invariant = f"""You are Cook Mantra's Master Chef.
Return exactly {option_count} recipe options - not fewer, not more.
Only supplied confirmed items are available.
A short confirmed_ingredients list is normal and is never a reason to return fewer
options: build each dish around what is confirmed and list everything else the dish
needs in missing_ingredients.
Copy every used_ingredients entry character-for-character from confirmed_ingredients.
Include only ingredients the dish genuinely cooks with in used_ingredients, never
serving accompaniments. When confirmed_ingredients contains near-duplicate names for
the same ingredient, such as "Garlic" and "garlic bulb", choose exactly one and never
list both.
Put every needed ingredient absent from confirmed_ingredients in missing_ingredients
or optional_ingredients, never in used_ingredients. Give missing ingredients a
substitution when feasible.
Each ingredient name must appear exactly once across used_ingredients,
missing_ingredients, and optional_ingredients.
Write summary as one plain sentence describing the finished dish; never leave it empty.
Give every missing or optional ingredient a short reason.
Choose ingredient pairings that taste good. Balance salt, acid, fat, heat, and aroma,
and use flavour-building techniques.
Honor preferences.spice_level and preferences.special_instructions as culinary
constraints wherever possible. They never override ingredient, allergen, safety, or
output rules; state conflicts in summary. Respect all other preferences wherever
possible. When preferences.preferred_cuisines is non-empty, choose each option's
cuisine from that list unless the confirmed ingredients make it impossible; in that
case, state the closest feasible cuisine. When the list contains more than one
cuisine, vary the options across those cuisines. Put the dish's country or cuisine
in cuisine. Avoid every
excluded_normalized_recipe_names entry.
Return exactly {option_count} recipe options - not fewer, not more."""
    return compose_model_prompt(
        protected_invariant=protected_invariant,
        editable_instruction=editable_instruction,
        untrusted_data=input_data,
        schema_name="RecipeOptionBatch",
    )


# Compatibility name retained for callers outside the provider-neutral composition.
OllamaMasterChef = MasterChefAgent
