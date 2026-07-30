"""LangGraph workflow for atomically extracting review ingredients."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.ingredient_extraction import IngredientExtractor, OllamaIngredientExtractor
from core.config import Settings
from domain.ingredients import (
    DetectedIngredient,
    ExtractionResult,
    Ingredient,
    assemble_review_ingredients,
)
from domain.sessions import Session, SessionStage
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.concurrency import ModelCallLimiter

type ProgressReporter = Callable[[int], Awaitable[None]]
type IngredientExtractionRunner = Callable[
    [str, str, ProgressReporter],
    Awaitable[dict[str, object]],
]


class IngredientExtractionState(TypedDict):
    """State shared by ingredient-extraction graph nodes."""

    session_id: str
    artifact_id: str
    detected: NotRequired[list[DetectedIngredient]]
    ingredients: NotRequired[list[Ingredient]]
    warnings: NotRequired[list[str]]
    stage: NotRequired[SessionStage]
    session: NotRequired[Session]
    image: NotRequired[bytes]
    media_type: NotRequired[str]


class IngredientExtractionOutput(TypedDict):
    """Serializable state returned after extraction completes."""

    session_id: str
    artifact_id: str
    detected: list[DetectedIngredient]
    ingredients: list[Ingredient]
    warnings: list[str]
    stage: SessionStage


async def _ignore_progress(_: int) -> None:
    return None


@dataclass(frozen=True, slots=True)
class ExtractionDependencies:
    """Replaceable service boundaries used by the extraction workflow."""

    extractor: IngredientExtractor
    session_store: SessionStore
    artifact_store: ArtifactStore
    model_call_limiter: ModelCallLimiter
    progress: ProgressReporter = _ignore_progress


def build_ingredient_extraction_graph(
    dependencies: ExtractionDependencies,
) -> CompiledStateGraph:
    """Build an extraction graph around the supplied service dependencies."""

    async def load_artifact(
        state: IngredientExtractionState,
    ) -> dict[str, object]:
        session = await dependencies.session_store.require(state["session_id"])
        image, media_type = await dependencies.artifact_store.read(
            state["artifact_id"],
            owner_session_id=session.id,
        )
        await dependencies.progress(15)
        return {
            "session": session,
            "image": image,
            "media_type": media_type,
        }

    async def extract(
        state: IngredientExtractionState,
    ) -> dict[str, object]:
        result = await dependencies.model_call_limiter.run(
            lambda: dependencies.extractor.extract(
                state["image"],
                state["media_type"],
            )
        )
        await dependencies.progress(65)
        return {
            "detected": result.detected,
            "warnings": result.warnings,
        }

    async def assemble_review(
        state: IngredientExtractionState,
    ) -> dict[str, object]:
        ingredients = assemble_review_ingredients(
            ExtractionResult(
                detected=state["detected"],
                warnings=state["warnings"],
            )
        )
        await dependencies.progress(85)
        return {"ingredients": ingredients}

    async def save_session(
        state: IngredientExtractionState,
    ) -> dict[str, object]:
        replacement = state["session"].model_copy(
            update={
                "stage": SessionStage.REVIEWING_INGREDIENTS,
                "image_artifact_id": state["artifact_id"],
                "ingredients": state["ingredients"],
                "warnings": state["warnings"],
            }
        )
        stored = await dependencies.session_store.replace(
            replacement,
            before_commit=lambda: dependencies.progress(100),
        )
        return {
            "ingredients": stored.ingredients,
            "warnings": stored.warnings,
            "stage": stored.stage,
        }

    builder = StateGraph(
        IngredientExtractionState,
        output_schema=IngredientExtractionOutput,
    )
    builder.add_node("load_artifact", load_artifact)
    builder.add_node("extract", extract)
    builder.add_node("assemble_review", assemble_review)
    builder.add_node("save_session", save_session)
    builder.add_edge(START, "load_artifact")
    builder.add_edge("load_artifact", "extract")
    builder.add_edge("extract", "assemble_review")
    builder.add_edge("assemble_review", "save_session")
    builder.add_edge("save_session", END)
    return builder.compile()


@dataclass(frozen=True, slots=True)
class DevelopmentIngredientExtractionRuntime:
    """Own the dependencies, graph, and artifact lifecycle used by development."""

    dependencies: ExtractionDependencies
    graph: CompiledStateGraph = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "graph",
            build_ingredient_extraction_graph(self.dependencies),
        )

    async def startup(self) -> None:
        """Start the owned artifact store before seeding or invoking the graph."""
        await self.dependencies.artifact_store.startup()

    async def shutdown(self) -> None:
        """Release the owned artifact store after development use."""
        await self.dependencies.artifact_store.shutdown()


def build_real_extraction_dependencies() -> ExtractionDependencies:
    """Wire the real extractor and temporary stores for development use."""
    settings = Settings(_env_file=None)
    return ExtractionDependencies(
        extractor=OllamaIngredientExtractor(settings=settings),
        session_store=SessionStore(ttl_seconds=settings.session_ttl_seconds),
        artifact_store=ArtifactStore(
            settings.artifact_root,
            ttl_seconds=settings.session_ttl_seconds,
        ),
        model_call_limiter=ModelCallLimiter(settings.max_concurrent_model_calls),
    )


@lru_cache
def get_development_ingredient_extraction_runtime() -> (
    DevelopmentIngredientExtractionRuntime
):
    """Return the stable, inspectable runtime used by the development factory."""
    return DevelopmentIngredientExtractionRuntime(build_real_extraction_dependencies())


def build_development_ingredient_extraction_graph() -> CompiledStateGraph:
    """Build the real dependency graph exposed to LangGraph development tools."""
    return get_development_ingredient_extraction_runtime().graph


async def run_ingredient_extraction(
    session_id: str,
    artifact_id: str,
    progress: ProgressReporter,
    *,
    dependencies: ExtractionDependencies,
) -> dict[str, object]:
    """Run extraction with explicitly supplied application dependencies."""
    resolved_dependencies = replace(
        dependencies,
        progress=progress,
    )
    graph = build_ingredient_extraction_graph(resolved_dependencies)
    result = await graph.ainvoke(
        {
            "session_id": session_id,
            "artifact_id": artifact_id,
        }
    )
    return dict(result)


def build_ingredient_extraction_runner(
    dependencies: ExtractionDependencies,
) -> IngredientExtractionRunner:
    """Bind application-owned dependencies to a three-argument job runner."""

    async def run(
        session_id: str,
        artifact_id: str,
        progress: ProgressReporter,
    ) -> dict[str, object]:
        return await run_ingredient_extraction(
            session_id,
            artifact_id,
            progress,
            dependencies=dependencies,
        )

    return run
