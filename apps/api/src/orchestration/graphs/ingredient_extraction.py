"""LangGraph workflow for atomically extracting review ingredients."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
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

type ProgressReporter = Callable[[int], Awaitable[None]]


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
    progress: ProgressReporter = _ignore_progress


def build_ingredient_extraction_graph(
    dependencies: ExtractionDependencies,
) -> CompiledStateGraph:
    """Build an extraction graph around the supplied service dependencies."""

    async def load_artifact(
        state: IngredientExtractionState,
    ) -> dict[str, object]:
        session = await dependencies.session_store.require(state["session_id"])
        artifact = await dependencies.artifact_store.require(state["artifact_id"])
        image = await asyncio.to_thread(artifact.path.read_bytes)
        await dependencies.progress(15)
        return {
            "session": session,
            "image": image,
            "media_type": artifact.media_type,
        }

    async def extract(
        state: IngredientExtractionState,
    ) -> dict[str, object]:
        result = await dependencies.extractor.extract(
            state["image"],
            state["media_type"],
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
        stored = await dependencies.session_store.replace(replacement)
        await dependencies.progress(100)
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


def build_real_extraction_dependencies() -> ExtractionDependencies:
    """Wire the real extractor and temporary stores for development use."""
    settings = Settings(_env_file=None)
    return ExtractionDependencies(
        extractor=OllamaIngredientExtractor(),
        session_store=SessionStore(ttl_seconds=settings.session_ttl_seconds),
        artifact_store=ArtifactStore(
            settings.artifact_root,
            ttl_seconds=settings.session_ttl_seconds,
        ),
    )


def build_development_ingredient_extraction_graph() -> CompiledStateGraph:
    """Build the real dependency graph exposed to LangGraph development tools."""
    return build_ingredient_extraction_graph(build_real_extraction_dependencies())


async def run_ingredient_extraction(
    session_id: str,
    artifact_id: str,
    progress: ProgressReporter,
    *,
    dependencies: ExtractionDependencies | None = None,
) -> dict[str, object]:
    """Run extraction with production wiring or explicitly injected dependencies."""
    resolved_dependencies = replace(
        dependencies or build_real_extraction_dependencies(),
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
