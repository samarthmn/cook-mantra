"""LangGraph workflow for generating and atomically committing recipe options."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.master_chef import MasterChef, OllamaMasterChef
from agents.nutrition import NutritionAgent, OllamaNutritionAgent
from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.recipe_option_service import (
    OptionGenerationContext,
    commit_option_batch,
    normalize_recipe_name,
    restore_option_generation,
    validate_option_names,
)
from domain.recipe_options import (
    NutritionEstimate,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
)
from domain.session_service import confirmed_ingredient_names
from domain.sessions import Session, SessionStage
from repositories.session_store import SessionStore
from services.concurrency import ModelCallLimiter

type ProgressReporter = Callable[[int], Awaitable[None]]

logger = logging.getLogger(__name__)

_MAX_GENERATION_ATTEMPTS = 3
_NUTRITION_WARNING = "Nutrition estimate unavailable."


class RecipeOptionState(TypedDict):
    """State shared by the recipe-option graph nodes."""

    session_id: str
    preferences: RecipePreferences | Mapping[str, object]
    more: bool
    generation_context: OptionGenerationContext | Mapping[str, object]
    previous_stage: NotRequired[SessionStage]
    session: NotRequired[Session]
    exclusions: NotRequired[set[str]]
    drafts: NotRequired[list[RecipeOptionDraft]]
    nutrition: NotRequired[list[NutritionEstimate | None]]
    nutrition_warnings: NotRequired[list[list[str]]]
    attempt_count: NotRequired[int]
    retry_generation: NotRequired[bool]
    recipe_options: NotRequired[list[RecipeOption]]
    stage: NotRequired[SessionStage]


class RecipeOptionOutput(TypedDict):
    """Serializable state returned after an option batch is committed."""

    session_id: str
    recipe_options: list[RecipeOption]
    stage: SessionStage
    option_ids: list[str]
    batch_number: int


type RecipeOptionsRunner = Callable[
    [
        str,
        RecipePreferences | Mapping[str, object],
        bool,
        OptionGenerationContext,
        ProgressReporter,
    ],
    Awaitable[RecipeOptionOutput],
]


async def _ignore_progress(_: int) -> None:
    return None


@dataclass(frozen=True, slots=True)
class RecipeOptionDependencies:
    """Replaceable service boundaries used by the recipe-option workflow."""

    master_chef: MasterChef
    nutrition_agent: NutritionAgent
    session_store: SessionStore
    model_call_limiter: ModelCallLimiter
    progress: ProgressReporter = _ignore_progress


def build_recipe_options_graph(
    dependencies: RecipeOptionDependencies,
) -> CompiledStateGraph:
    """Build an option graph around the supplied application dependencies."""

    async def load_generating_session(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        context = OptionGenerationContext.model_validate(state["generation_context"])
        preferences = RecipePreferences.model_validate(state["preferences"])
        session = await dependencies.session_store.require(state["session_id"])

        # This pure operation validates the exact attempt identity and the complete
        # rollback snapshot without changing the persisted generating session.
        restore_option_generation(session, context)
        if state["more"] is not context.more or session.preferences != preferences:
            raise AppError(
                code=ErrorCode.INVALID_SESSION_TRANSITION,
                message="Recipe option generation input does not match the session.",
                status_code=409,
                retryable=False,
                session_id=session.id,
            )

        await dependencies.progress(10)
        return {
            "session": session,
            "preferences": preferences,
            "generation_context": context,
            "previous_stage": context.previous_stage,
            "exclusions": set(session.excluded_recipe_names),
            "attempt_count": 0,
            "retry_generation": False,
        }

    async def generate_drafts(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        drafts = await dependencies.model_call_limiter.run(
            lambda: dependencies.master_chef.generate(
                confirmed_ingredient_names(state["session"]),
                RecipePreferences.model_validate(state["preferences"]),
                set(state["exclusions"]),
            )
        )
        return {
            "drafts": drafts,
            "attempt_count": state["attempt_count"] + 1,
            "retry_generation": False,
        }

    async def validate_names(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        try:
            validate_option_names(state["drafts"], state["exclusions"])
        except AppError as error:
            if (
                error.code is not ErrorCode.RECIPE_DUPLICATE
                or state["attempt_count"] >= _MAX_GENERATION_ATTEMPTS
            ):
                raise
            expanded_exclusions = set(state["exclusions"])
            expanded_exclusions.update(
                normalize_recipe_name(option.name) for option in state["drafts"]
            )
            return {
                "exclusions": expanded_exclusions,
                "retry_generation": True,
            }

        await dependencies.progress(45)
        return {"retry_generation": False}

    async def enrich_nutrition(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        preferences = RecipePreferences.model_validate(state["preferences"])

        def nutrition_call(
            option: RecipeOptionDraft,
        ) -> Callable[[], Awaitable[NutritionEstimate]]:
            async def call() -> NutritionEstimate:
                return await dependencies.nutrition_agent.estimate(
                    option,
                    preferences,
                )

            return call

        calls = [nutrition_call(option) for option in state["drafts"]]
        results = await asyncio.gather(
            *(dependencies.model_call_limiter.run(call) for call in calls),
            return_exceptions=True,
        )
        nutrition: list[NutritionEstimate | None] = []
        warnings: list[list[str]] = []
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                nutrition.append(None)
                warnings.append([_NUTRITION_WARNING])
            else:
                nutrition.append(result)
                warnings.append([])

        await dependencies.progress(80)
        return {
            "nutrition": nutrition,
            "nutrition_warnings": warnings,
        }

    async def commit_batch(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        context = OptionGenerationContext.model_validate(state["generation_context"])
        committed = commit_option_batch(
            state["session"],
            state["drafts"],
            state["nutrition"],
            context,
        )
        batch_start = len(state["session"].recipe_options) if context.more else 0
        committed_options = [
            option.model_copy(deep=True) for option in committed.recipe_options
        ]
        for offset, warnings in enumerate(state["nutrition_warnings"]):
            if warnings:
                index = batch_start + offset
                committed_options[index] = committed_options[index].model_copy(
                    update={"warnings": list(warnings)}
                )

        replacement = committed.model_copy(
            update={
                "recipe_options": committed_options,
                "updated_at": state["session"].updated_at,
            }
        )
        stored = await dependencies.session_store.replace(replacement)
        saved_options = stored.recipe_options[batch_start:]
        return {
            "recipe_options": stored.recipe_options,
            "stage": stored.stage,
            "option_ids": [option.id for option in saved_options],
            "batch_number": stored.option_batch_number,
        }

    guarded_load = _rollback_on_failure(load_generating_session, dependencies)
    guarded_generate = _rollback_on_failure(generate_drafts, dependencies)
    guarded_validate = _rollback_on_failure(validate_names, dependencies)
    guarded_enrich = _rollback_on_failure(enrich_nutrition, dependencies)
    guarded_commit = _rollback_on_failure(commit_batch, dependencies)

    builder = StateGraph(RecipeOptionState, output_schema=RecipeOptionOutput)
    builder.add_node("load_generating_session", guarded_load)
    builder.add_node("generate_drafts", guarded_generate)
    builder.add_node("validate_names", guarded_validate)
    builder.add_node("enrich_nutrition", guarded_enrich)
    builder.add_node("commit_batch", guarded_commit)
    builder.add_edge(START, "load_generating_session")
    builder.add_edge("load_generating_session", "generate_drafts")
    builder.add_edge("generate_drafts", "validate_names")
    builder.add_conditional_edges(
        "validate_names",
        lambda state: "retry" if state.get("retry_generation", False) else "enrich",
        {
            "retry": "generate_drafts",
            "enrich": "enrich_nutrition",
        },
    )
    builder.add_edge("enrich_nutrition", "commit_batch")
    builder.add_edge("commit_batch", END)
    return builder.compile()


def _rollback_on_failure(
    operation: Callable[
        [RecipeOptionState],
        Awaitable[dict[str, object]],
    ],
    dependencies: RecipeOptionDependencies,
) -> Callable[[RecipeOptionState], Awaitable[dict[str, object]]]:
    async def guarded(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        try:
            return await operation(state)
        except asyncio.CancelledError as cancellation:
            await _drain_rollback(state, dependencies)
            raise cancellation
        except Exception:
            await _drain_rollback(state, dependencies)
            raise

    return guarded


async def _drain_rollback(
    state: RecipeOptionState,
    dependencies: RecipeOptionDependencies,
) -> None:
    """Finish rollback despite repeated cancellation of the invoking task."""
    rollback_task = asyncio.create_task(_preserving_rollback(state, dependencies))
    cancellation: asyncio.CancelledError | None = None
    while not rollback_task.done():
        try:
            await asyncio.shield(rollback_task)
        except asyncio.CancelledError as error:
            cancellation = error
            continue
    rollback_task.result()
    if cancellation is not None:
        raise cancellation


async def _preserving_rollback(
    state: RecipeOptionState,
    dependencies: RecipeOptionDependencies,
) -> None:
    """Restore only the exact still-current attempt and preserve its failure."""
    try:
        context = OptionGenerationContext.model_validate(state["generation_context"])
        current = await dependencies.session_store.require(state["session_id"])
        restored = restore_option_generation(current, context)
        await dependencies.session_store.replace(
            restored.model_copy(update={"updated_at": current.updated_at})
        )
    except Exception:
        logger.exception(
            "Recipe option generation rollback was not applied",
            extra={"session_id": state.get("session_id")},
        )


@dataclass(frozen=True, slots=True)
class DevelopmentRecipeOptionsRuntime:
    """Own the settings, real dependencies, and inspectable development graph."""

    settings: Settings
    dependencies: RecipeOptionDependencies = field(init=False)
    graph: CompiledStateGraph = field(init=False)

    def __post_init__(self) -> None:
        dependencies = build_real_recipe_option_dependencies(self.settings)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(
            self,
            "graph",
            build_recipe_options_graph(dependencies),
        )


def build_real_recipe_option_dependencies(
    settings: Settings,
) -> RecipeOptionDependencies:
    """Wire real lazy agents, a store, and one shared model-call limiter."""
    model_call_limiter = ModelCallLimiter(settings.max_concurrent_model_calls)
    return RecipeOptionDependencies(
        master_chef=OllamaMasterChef(settings=settings),
        nutrition_agent=OllamaNutritionAgent(settings=settings),
        session_store=SessionStore(ttl_seconds=settings.session_ttl_seconds),
        model_call_limiter=model_call_limiter,
    )


@lru_cache
def get_development_recipe_options_runtime() -> DevelopmentRecipeOptionsRuntime:
    """Return the stable runtime exposed to LangGraph development tools."""
    return DevelopmentRecipeOptionsRuntime(Settings(_env_file=None))


def build_development_recipe_options_graph() -> CompiledStateGraph:
    """Return the stable real-dependency graph without making a model call."""
    return get_development_recipe_options_runtime().graph


async def run_recipe_options(
    session_id: str,
    preferences: RecipePreferences | Mapping[str, object],
    more: bool,
    generation_context: OptionGenerationContext,
    progress: ProgressReporter,
    *,
    dependencies: RecipeOptionDependencies,
) -> RecipeOptionOutput:
    """Run the exact persisted generation attempt with application dependencies."""
    resolved_dependencies = replace(dependencies, progress=progress)
    graph = build_recipe_options_graph(resolved_dependencies)
    result = await graph.ainvoke(
        {
            "session_id": session_id,
            "preferences": preferences,
            "more": more,
            "generation_context": generation_context,
        }
    )
    return cast(RecipeOptionOutput, dict(result))


def build_recipe_options_runner(
    dependencies: RecipeOptionDependencies,
) -> RecipeOptionsRunner:
    """Bind application-owned dependencies to the option job runner."""

    async def run(
        session_id: str,
        preferences: RecipePreferences | Mapping[str, object],
        more: bool,
        generation_context: OptionGenerationContext,
        progress: ProgressReporter,
    ) -> RecipeOptionOutput:
        return await run_recipe_options(
            session_id,
            preferences,
            more,
            generation_context,
            progress,
            dependencies=dependencies,
        )

    return run
