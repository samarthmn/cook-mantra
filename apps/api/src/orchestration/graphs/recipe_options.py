"""LangGraph workflow for generating and atomically committing recipe options."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langsmith import tracing_context

from agents.master_chef import MasterChef, OllamaMasterChef
from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.recipe_option_service import (
    OptionGenerationContext,
    commit_option_batch,
    normalize_recipe_name,
    restore_option_generation,
    validate_option_names,
)
from domain.recipe_options import RecipeOption, RecipeOptionDraft, RecipePreferences
from domain.session_service import confirmed_ingredient_names
from domain.sessions import Session, SessionStage
from repositories.session_store import SessionStore
from services.concurrency import ModelCallLimiter
from services.tracing import TracingService, trace_batch_number

type ProgressReporter = Callable[[int], Awaitable[None]]

logger = logging.getLogger(__name__)

_MAX_GENERATION_ATTEMPTS = 3
_DRAFT_PROGRESS_START = 15
_DRAFT_PROGRESS_INCREMENT = 10
_VALIDATED_PROGRESS = 80


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
    session_store: SessionStore
    model_call_limiter: ModelCallLimiter
    progress: ProgressReporter = _ignore_progress


class _CommitSucceededDuringCancellation(Exception):
    """Carry cancellation past cleanup after the exact replacement committed."""

    def __init__(self, cancellation: asyncio.CancelledError) -> None:
        super().__init__("Recipe option commit completed during cancellation.")
        self.cancellation = cancellation


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
        await dependencies.progress(
            _DRAFT_PROGRESS_START + (state["attempt_count"] * _DRAFT_PROGRESS_INCREMENT)
        )
        with trace_batch_number(state["session"].option_batch_number + 1):
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
            logger.info(
                "recipe_option_duplicate_retry",
                extra={
                    "event": "recipe_option_duplicate_retry",
                    "attempt_number": state["attempt_count"],
                    "option_names": [option.name for option in state["drafts"]],
                },
            )
            return {
                "exclusions": expanded_exclusions,
                "retry_generation": True,
            }

        await dependencies.progress(_VALIDATED_PROGRESS)
        return {"retry_generation": False}

    async def commit_batch(
        state: RecipeOptionState,
    ) -> dict[str, object]:
        context = OptionGenerationContext.model_validate(state["generation_context"])
        committed = commit_option_batch(
            state["session"],
            state["drafts"],
            context,
        )
        batch_start = len(state["session"].recipe_options) if context.more else 0
        replacement = committed.model_copy(
            update={"updated_at": state["session"].updated_at}
        )
        stored = await _replace_settling_cancellation(
            replacement,
            dependencies,
        )
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
    guarded_commit = _rollback_on_failure(commit_batch, dependencies)

    builder = StateGraph(RecipeOptionState, output_schema=RecipeOptionOutput)
    builder.add_node("load_generating_session", guarded_load)
    builder.add_node("generate_drafts", guarded_generate)
    builder.add_node("validate_names", guarded_validate)
    builder.add_node("commit_batch", guarded_commit)
    builder.add_edge(START, "load_generating_session")
    builder.add_edge("load_generating_session", "generate_drafts")
    builder.add_edge("generate_drafts", "validate_names")
    builder.add_conditional_edges(
        "validate_names",
        lambda state: "retry" if state.get("retry_generation", False) else "commit",
        {
            "retry": "generate_drafts",
            "commit": "commit_batch",
        },
    )
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
        except _CommitSucceededDuringCancellation as committed:
            raise committed.cancellation from None
        except asyncio.CancelledError as cancellation:
            await _drain_rollback(state, dependencies)
            raise cancellation
        except Exception:
            await _drain_rollback(state, dependencies)
            raise

    return guarded


async def _replace_settling_cancellation(
    replacement: Session,
    dependencies: RecipeOptionDependencies,
) -> Session:
    """Settle the exact replacement before deciding cancellation cleanup."""
    replace_task = asyncio.create_task(dependencies.session_store.replace(replacement))
    cancellation: asyncio.CancelledError | None = None
    while not replace_task.done():
        try:
            await asyncio.shield(replace_task)
        except asyncio.CancelledError as error:
            cancellation = error
            continue
        except BaseException:
            break

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
        except BaseException:
            break
    try:
        rollback_task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation from None


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
    """Own the settings, text dependencies, and development graph."""

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
    tracing = TracingService(settings)
    return RecipeOptionDependencies(
        master_chef=OllamaMasterChef(settings=settings, tracing=tracing),
        session_store=SessionStore(ttl_seconds=settings.session_ttl_seconds),
        model_call_limiter=model_call_limiter,
    )


@lru_cache
def get_development_recipe_options_runtime() -> DevelopmentRecipeOptionsRuntime:
    """Return the stable runtime exposed to LangGraph development tools."""
    return DevelopmentRecipeOptionsRuntime(Settings(_env_file=None))


@asynccontextmanager
async def build_development_recipe_options_graph() -> AsyncIterator[CompiledStateGraph]:
    """Yield the stable configured text-only graph."""
    yield get_development_recipe_options_runtime().graph


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
    with tracing_context(enabled=False):
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
