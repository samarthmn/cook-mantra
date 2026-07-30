"""LangGraph workflow for generating selected complete recipes in parallel."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.specialized_recipe import (
    OllamaSpecializedRecipeAgent,
    SpecializedRecipeAgent,
)
from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.recipe_options import RecipeOption, RecipePreferences
from domain.recipe_service import (
    RecipeGenerationContext,
    commit_recipe_results,
    restore_after_recipe_failure,
    validate_recipe_generation,
)
from domain.recipes import CompleteRecipe, RecipeFailure
from domain.session_service import confirmed_ingredient_names
from domain.sessions import Session, SessionStage
from repositories.session_store import SessionStore
from services.concurrency import ModelCallLimiter

type ProgressReporter = Callable[[int], Awaitable[None]]

logger = logging.getLogger(__name__)

_LOAD_PROGRESS = 10
_SETTLED_PROGRESS = 90
_GENERIC_FAILURE_MESSAGE = "The model returned invalid structured output."


class CompleteRecipesState(TypedDict):
    """State shared by complete-recipe graph nodes."""

    session_id: str
    generation_context: RecipeGenerationContext | Mapping[str, object]
    option_ids: NotRequired[Sequence[str]]
    previous_stage: NotRequired[SessionStage | str]
    session: NotRequired[Session]
    selected_options: NotRequired[list[RecipeOption]]
    confirmed_names: NotRequired[list[str]]
    preferences: NotRequired[RecipePreferences]
    successes: NotRequired[dict[str, CompleteRecipe]]
    failures: NotRequired[dict[str, RecipeFailure]]
    complete_recipes: NotRequired[list[CompleteRecipe]]
    recipe_failures: NotRequired[list[RecipeFailure]]
    stage: NotRequired[SessionStage]
    selected_option_ids: NotRequired[list[str]]
    recipe_option_ids: NotRequired[list[str]]
    failed_option_ids: NotRequired[list[str]]


class CompleteRecipesOutput(TypedDict):
    """Detached selected-attempt results returned after the atomic commit."""

    session_id: str
    complete_recipes: list[CompleteRecipe]
    recipe_failures: list[RecipeFailure]
    stage: SessionStage
    selected_option_ids: list[str]
    recipe_option_ids: list[str]
    failed_option_ids: list[str]


type CompleteRecipesRunner = Callable[
    [str, RecipeGenerationContext, ProgressReporter],
    Awaitable[CompleteRecipesOutput],
]


async def _ignore_progress(_: int) -> None:
    return None


@dataclass(frozen=True, slots=True)
class CompleteRecipeDependencies:
    """Replaceable service boundaries for complete-recipe generation."""

    agent: SpecializedRecipeAgent
    session_store: SessionStore
    model_call_limiter: ModelCallLimiter
    progress: ProgressReporter = _ignore_progress


class _CommitSucceededDuringCancellation(Exception):
    """Carry cancellation without rolling back an exact committed replacement."""

    def __init__(self, cancellation: asyncio.CancelledError) -> None:
        super().__init__("Complete recipe commit completed during cancellation.")
        self.cancellation = cancellation


def build_complete_recipes_graph(
    dependencies: CompleteRecipeDependencies,
) -> CompiledStateGraph:
    """Build the selected complete-recipe workflow around supplied dependencies."""

    async def load_generating_session(
        state: CompleteRecipesState,
    ) -> dict[str, object]:
        session = await dependencies.session_store.require(state["session_id"])
        context = _validate_context_input(session, state["generation_context"])
        rollback = validate_recipe_generation(session, context)
        _validate_transient_input(state, session, context)

        options_by_id = {option.id: option for option in rollback.recipe_options}
        selected_options = [
            options_by_id[option_id].model_copy(deep=True)
            for option_id in context.selected_option_ids
        ]
        await dependencies.progress(_LOAD_PROGRESS)
        return {
            "session": session,
            "generation_context": context,
            "selected_options": selected_options,
            "confirmed_names": confirmed_ingredient_names(session),
            "preferences": session.preferences.model_copy(deep=True),
        }

    async def generate_selected(
        state: CompleteRecipesState,
    ) -> dict[str, object]:
        selected_options = state["selected_options"]
        tasks = [
            asyncio.create_task(
                _generate_one(
                    option,
                    state["confirmed_names"],
                    state["preferences"],
                    dependencies,
                ),
                name=f"complete-recipe:{option.id}",
            )
            for option in selected_options
        ]
        successes: dict[str, CompleteRecipe] = {}
        failures: dict[str, RecipeFailure] = {}
        settled = 0
        try:
            for completion in asyncio.as_completed(tasks):
                option_id, result = await completion
                if isinstance(result, CompleteRecipe):
                    successes[option_id] = result
                else:
                    failures[option_id] = result
                settled += 1
                await dependencies.progress(
                    _LOAD_PROGRESS
                    + settled
                    * (_SETTLED_PROGRESS - _LOAD_PROGRESS)
                    // len(selected_options)
                )
        except asyncio.CancelledError:
            await _cancel_and_drain_tasks(tasks)
            raise
        except BaseException:
            await _cancel_and_drain_tasks(tasks)
            raise

        return {
            "successes": successes,
            "failures": failures,
        }

    async def commit_results(
        state: CompleteRecipesState,
    ) -> dict[str, object]:
        context = _validate_context_input(
            state["session"],
            state["generation_context"],
        )
        committed = commit_recipe_results(
            state["session"],
            state["successes"],
            state["failures"],
            context,
        )
        replacement = committed.model_copy(
            deep=True,
            update={"updated_at": state["session"].updated_at},
        )
        stored = await _replace_settling_cancellation(replacement, dependencies)

        selected_option_ids = list(context.selected_option_ids)
        complete_recipes = [
            stored.complete_recipes[option_id].model_copy(deep=True)
            for option_id in selected_option_ids
            if option_id in stored.complete_recipes
        ]
        recipe_failures = [
            stored.recipe_failures[option_id].model_copy(deep=True)
            for option_id in selected_option_ids
            if option_id in stored.recipe_failures
        ]
        return {
            "complete_recipes": complete_recipes,
            "recipe_failures": recipe_failures,
            "stage": stored.stage,
            "selected_option_ids": selected_option_ids,
            "recipe_option_ids": [recipe.option_id for recipe in complete_recipes],
            "failed_option_ids": [failure.option_id for failure in recipe_failures],
        }

    guarded_load = _rollback_on_failure(load_generating_session, dependencies)
    guarded_generate = _rollback_on_failure(generate_selected, dependencies)
    guarded_commit = _rollback_on_failure(commit_results, dependencies)

    builder = StateGraph(
        CompleteRecipesState,
        output_schema=CompleteRecipesOutput,
    )
    builder.add_node("load_generating_session", guarded_load)
    builder.add_node("generate_selected", guarded_generate)
    builder.add_node("commit_results", guarded_commit)
    builder.add_edge(START, "load_generating_session")
    builder.add_edge("load_generating_session", "generate_selected")
    builder.add_edge("generate_selected", "commit_results")
    builder.add_edge("commit_results", END)
    return builder.compile()


async def _generate_one(
    option: RecipeOption,
    confirmed_names: list[str],
    preferences: RecipePreferences,
    dependencies: CompleteRecipeDependencies,
) -> tuple[str, CompleteRecipe | RecipeFailure]:
    """Generate and independently sanitize one selected option's outcome."""
    try:
        recipe = await dependencies.model_call_limiter.run(
            lambda: dependencies.agent.generate(
                option.model_copy(deep=True),
                list(confirmed_names),
                preferences.model_copy(deep=True),
            )
        )
        detached = CompleteRecipe.model_validate(
            recipe.model_dump(
                mode="python",
                round_trip=True,
                warnings="error",
            )
        )
        return option.id, detached
    except AppError as error:
        return option.id, _failure_from_app_error(option.id, error)
    except Exception:
        return option.id, _generic_failure(option.id)


def _failure_from_app_error(option_id: str, error: AppError) -> RecipeFailure:
    """Retain only the stable public fields from an expected application error."""
    try:
        if (
            not isinstance(error.code, ErrorCode)
            or not isinstance(error.message, str)
            or not error.message.strip()
            or not isinstance(error.retryable, bool)
        ):
            raise ValueError
        return RecipeFailure(
            option_id=option_id,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
        )
    except (TypeError, ValueError):
        return _generic_failure(option_id)


def _generic_failure(option_id: str) -> RecipeFailure:
    return RecipeFailure(
        option_id=option_id,
        code=ErrorCode.MODEL_OUTPUT_INVALID,
        message=_GENERIC_FAILURE_MESSAGE,
        retryable=True,
    )


def _validate_context_input(
    session: Session,
    value: RecipeGenerationContext | Mapping[str, object],
) -> RecipeGenerationContext:
    try:
        return RecipeGenerationContext.model_validate(value)
    except (TypeError, ValueError):
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Recipe generation context is invalid.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        ) from None


def _validate_transient_input(
    state: CompleteRecipesState,
    session: Session,
    context: RecipeGenerationContext,
) -> None:
    option_ids_match = True
    if "option_ids" in state:
        option_ids = state["option_ids"]
        option_ids_match = (
            not isinstance(option_ids, (str, bytes))
            and isinstance(option_ids, Sequence)
            and tuple(option_ids) == context.selected_option_ids
        )

    previous_stage_matches = True
    if "previous_stage" in state:
        try:
            previous_stage = SessionStage(state["previous_stage"])
        except (TypeError, ValueError):
            previous_stage_matches = False
        else:
            previous_stage_matches = previous_stage is context.previous_stage

    if not option_ids_match or not previous_stage_matches:
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Recipe generation input does not match the session.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )


def _rollback_on_failure(
    operation: Callable[
        [CompleteRecipesState],
        Awaitable[dict[str, object]],
    ],
    dependencies: CompleteRecipeDependencies,
) -> Callable[[CompleteRecipesState], Awaitable[dict[str, object]]]:
    async def guarded(
        state: CompleteRecipesState,
    ) -> dict[str, object]:
        try:
            return await operation(state)
        except _CommitSucceededDuringCancellation as committed:
            raise committed.cancellation from None
        except asyncio.CancelledError as cancellation:
            await _drain_rollback(state, dependencies)
            raise cancellation
        except BaseException:
            await _drain_rollback(state, dependencies)
            raise

    return guarded


async def _cancel_and_drain_tasks(
    tasks: list[asyncio.Task[tuple[str, CompleteRecipe | RecipeFailure]]],
) -> None:
    """Cancel and settle every fan-out child despite repeated parent cancellation."""
    for task in tasks:
        task.cancel()

    async def settle() -> list[object]:
        return await asyncio.gather(*tasks, return_exceptions=True)

    settle_task = asyncio.create_task(settle())
    while not settle_task.done():
        try:
            await asyncio.shield(settle_task)
        except asyncio.CancelledError:
            continue
    settle_task.result()


async def _replace_settling_cancellation(
    replacement: Session,
    dependencies: CompleteRecipeDependencies,
) -> Session:
    """Settle the exact optimistic replace before choosing cancellation cleanup."""
    replace_task = asyncio.create_task(dependencies.session_store.replace(replacement))
    cancellation: asyncio.CancelledError | None = None
    while not replace_task.done():
        try:
            await asyncio.shield(replace_task)
        except asyncio.CancelledError as error:
            cancellation = error
            continue

    try:
        stored = replace_task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise

    if cancellation is not None:
        raise _CommitSucceededDuringCancellation(cancellation)
    return stored


async def _drain_rollback(
    state: CompleteRecipesState,
    dependencies: CompleteRecipeDependencies,
) -> None:
    """Drain exact-attempt rollback despite repeated cancellation."""
    rollback_task = asyncio.create_task(_preserving_rollback(state, dependencies))
    while not rollback_task.done():
        try:
            await asyncio.shield(rollback_task)
        except asyncio.CancelledError:
            continue
    rollback_task.result()


async def _preserving_rollback(
    state: CompleteRecipesState,
    dependencies: CompleteRecipeDependencies,
) -> None:
    """Restore only the exact still-current attempt and preserve primary failure."""
    try:
        current = await dependencies.session_store.require(state["session_id"])
        context = _validate_context_input(
            current,
            state["generation_context"],
        )
        restored = restore_after_recipe_failure(current, context)
        await dependencies.session_store.replace(
            restored.model_copy(update={"updated_at": current.updated_at})
        )
    except Exception:
        logger.exception(
            "Complete recipe generation rollback was not applied",
            extra={"session_id": state.get("session_id")},
        )


@dataclass(frozen=True, slots=True)
class DevelopmentCompleteRecipesRuntime:
    """Own real lazy dependencies and the inspectable development graph."""

    settings: Settings
    dependencies: CompleteRecipeDependencies = field(init=False)
    graph: CompiledStateGraph = field(init=False)

    def __post_init__(self) -> None:
        dependencies = build_real_complete_recipe_dependencies(self.settings)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(
            self,
            "graph",
            build_complete_recipes_graph(dependencies),
        )


def build_real_complete_recipe_dependencies(
    settings: Settings | None = None,
) -> CompleteRecipeDependencies:
    """Wire one lazy real agent, configured limiter, and in-memory store."""
    resolved_settings = settings or Settings(_env_file=None)
    return CompleteRecipeDependencies(
        agent=OllamaSpecializedRecipeAgent(settings=resolved_settings),
        session_store=SessionStore(ttl_seconds=resolved_settings.session_ttl_seconds),
        model_call_limiter=ModelCallLimiter(
            resolved_settings.max_concurrent_model_calls
        ),
    )


@lru_cache
def get_development_complete_recipes_runtime() -> DevelopmentCompleteRecipesRuntime:
    """Return the stable runtime exposed to LangGraph development tools."""
    return DevelopmentCompleteRecipesRuntime(Settings(_env_file=None))


def build_development_complete_recipes_graph() -> CompiledStateGraph:
    """Return the lazy real graph without opening network-backed resources."""
    return get_development_complete_recipes_runtime().graph


async def run_complete_recipes(
    session_id: str,
    generation_context: RecipeGenerationContext,
    progress: ProgressReporter,
    *,
    dependencies: CompleteRecipeDependencies,
) -> CompleteRecipesOutput:
    """Run the exact persisted recipe-generation attempt."""
    resolved_dependencies = replace(dependencies, progress=progress)
    graph = build_complete_recipes_graph(resolved_dependencies)
    result = await graph.ainvoke(
        {
            "session_id": session_id,
            "generation_context": generation_context,
            "option_ids": list(generation_context.selected_option_ids),
            "previous_stage": generation_context.previous_stage,
        }
    )
    return cast(CompleteRecipesOutput, dict(result))


def build_complete_recipes_runner(
    dependencies: CompleteRecipeDependencies,
) -> CompleteRecipesRunner:
    """Bind application-owned dependencies to a complete-recipe job runner."""

    async def run(
        session_id: str,
        generation_context: RecipeGenerationContext,
        progress: ProgressReporter,
    ) -> CompleteRecipesOutput:
        return await run_complete_recipes(
            session_id,
            generation_context,
            progress,
            dependencies=dependencies,
        )

    return run


CompleteRecipesDependencies = CompleteRecipeDependencies
