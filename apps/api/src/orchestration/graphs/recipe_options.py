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
from core.config import Model, Settings
from core.errors import AppError, ErrorCode
from domain.images import DishPreview
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
from services.artifacts import ArtifactStore
from services.concurrency import ModelCallLimiter
from services.dish_previews import DishPreviewService
from services.image_generation import OllamaImageGenerator

type ProgressReporter = Callable[[int], Awaitable[None]]

logger = logging.getLogger(__name__)

_MAX_GENERATION_ATTEMPTS = 3
_ENRICHMENT_PROGRESS_START = 45
_ENRICHMENT_PROGRESS_END = 80
_NUTRITION_WARNING = "Nutrition estimate unavailable."
_PREVIEW_WARNING = "Dish preview unavailable."


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
    previews: NotRequired[list[DishPreview | None]]
    preview_warnings: NotRequired[list[list[str]]]
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
    dish_previews: DishPreviewService
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
        progress_by_option = [0] * len(state["drafts"])
        last_reported_progress = _ENRICHMENT_PROGRESS_START
        progress_lock = asyncio.Lock()

        def nutrition_call(
            option: RecipeOptionDraft,
        ) -> Callable[[], Awaitable[NutritionEstimate]]:
            async def call() -> NutritionEstimate:
                return await dependencies.nutrition_agent.estimate(
                    option,
                    preferences,
                )

            return call

        def preview_call(
            index: int,
            option: RecipeOptionDraft,
        ) -> Callable[[], Awaitable[DishPreview]]:
            async def report_preview_progress(value: int) -> None:
                nonlocal last_reported_progress
                bounded_value = max(0, min(value, 100))
                async with progress_lock:
                    progress_by_option[index] = max(
                        progress_by_option[index],
                        bounded_value,
                    )
                    mapped_progress = _ENRICHMENT_PROGRESS_START + (
                        sum(progress_by_option)
                        * (_ENRICHMENT_PROGRESS_END - _ENRICHMENT_PROGRESS_START - 1)
                        // (len(progress_by_option) * 100)
                    )
                    if mapped_progress > last_reported_progress:
                        await dependencies.progress(mapped_progress)
                        last_reported_progress = mapped_progress

            async def call() -> DishPreview:
                return await dependencies.dish_previews.generate(
                    state["session_id"],
                    option,
                    report_preview_progress,
                )

            return call

        calls: list[Callable[[], Awaitable[NutritionEstimate | DishPreview]]] = []
        for index, option in enumerate(state["drafts"]):
            calls.extend(
                (
                    nutrition_call(option),
                    preview_call(index, option),
                )
            )
        results = await asyncio.gather(
            *(dependencies.model_call_limiter.run(call) for call in calls),
            return_exceptions=True,
        )
        nutrition: list[NutritionEstimate | None] = []
        nutrition_warnings: list[list[str]] = []
        previews: list[DishPreview | None] = []
        preview_warnings: list[list[str]] = []
        for index in range(len(state["drafts"])):
            nutrition_result = results[index * 2]
            preview_result = results[index * 2 + 1]
            if isinstance(nutrition_result, asyncio.CancelledError):
                raise nutrition_result
            if isinstance(preview_result, asyncio.CancelledError):
                raise preview_result
            if isinstance(nutrition_result, Exception):
                nutrition.append(None)
                nutrition_warnings.append([_NUTRITION_WARNING])
            else:
                nutrition.append(cast(NutritionEstimate, nutrition_result))
                nutrition_warnings.append([])
            if isinstance(preview_result, Exception):
                previews.append(None)
                preview_warnings.append([_PREVIEW_WARNING])
            else:
                previews.append(cast(DishPreview, preview_result))
                preview_warnings.append([])

        await dependencies.progress(_ENRICHMENT_PROGRESS_END)
        return {
            "nutrition": nutrition,
            "nutrition_warnings": nutrition_warnings,
            "previews": previews,
            "preview_warnings": preview_warnings,
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
        for offset, preview in enumerate(state["previews"]):
            index = batch_start + offset
            warnings = [
                *state["nutrition_warnings"][offset],
                *state["preview_warnings"][offset],
            ]
            committed_options[index] = committed_options[index].model_copy(
                update={
                    "preview": preview.model_copy(deep=True)
                    if preview is not None
                    else None,
                    "warnings": warnings,
                }
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
    image_generator: OllamaImageGenerator = field(init=False)
    artifact_store: ArtifactStore = field(init=False)
    dependencies: RecipeOptionDependencies = field(init=False)
    graph: CompiledStateGraph = field(init=False)

    def __post_init__(self) -> None:
        image_generator = OllamaImageGenerator(
            base_url=str(self.settings.ollama_base_url),
            model=Model.Z_IMAGE,
            timeout_seconds=self.settings.image_timeout_seconds,
        )
        artifact_store = ArtifactStore(
            self.settings.artifact_root,
            ttl_seconds=self.settings.session_ttl_seconds,
        )
        dependencies = build_real_recipe_option_dependencies(
            self.settings,
            image_generator=image_generator,
            artifact_store=artifact_store,
        )
        object.__setattr__(self, "image_generator", image_generator)
        object.__setattr__(self, "artifact_store", artifact_store)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(
            self,
            "graph",
            build_recipe_options_graph(dependencies),
        )

    async def startup(self) -> None:
        """Prepare the owned artifact namespace before graph invocation."""
        await self.artifact_store.startup()

    async def shutdown(self) -> None:
        """Close every owned image resource before propagating cancellation."""
        shutdown_task = asyncio.create_task(self._shutdown_owned_resources())
        cancellation: asyncio.CancelledError | None = None
        while not shutdown_task.done():
            try:
                await asyncio.shield(shutdown_task)
            except asyncio.CancelledError as error:
                cancellation = error
                continue
        shutdown_task.result()
        if cancellation is not None:
            raise cancellation

    async def _shutdown_owned_resources(self) -> None:
        try:
            await self.image_generator.aclose()
        finally:
            await self.artifact_store.shutdown()


def build_real_recipe_option_dependencies(
    settings: Settings,
    *,
    image_generator: OllamaImageGenerator | None = None,
    artifact_store: ArtifactStore | None = None,
) -> RecipeOptionDependencies:
    """Wire real lazy agents, a store, and one shared model-call limiter."""
    model_call_limiter = ModelCallLimiter(settings.max_concurrent_model_calls)
    resolved_image_generator = image_generator or OllamaImageGenerator(
        base_url=str(settings.ollama_base_url),
        model=Model.Z_IMAGE,
        timeout_seconds=settings.image_timeout_seconds,
    )
    resolved_artifact_store = artifact_store or ArtifactStore(
        settings.artifact_root,
        ttl_seconds=settings.session_ttl_seconds,
    )
    return RecipeOptionDependencies(
        master_chef=OllamaMasterChef(settings=settings),
        nutrition_agent=OllamaNutritionAgent(settings=settings),
        dish_previews=DishPreviewService(
            resolved_image_generator,
            resolved_artifact_store,
            settings,
        ),
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
