from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from domain.ingredients import ExtractionResult, IngredientSource
from domain.sessions import Session, SessionStage
from orchestration.graphs.ingredient_extraction import (
    ExtractionDependencies,
    build_ingredient_extraction_graph,
    run_ingredient_extraction,
)
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore


@dataclass
class ExtractionContext:
    session_store: SessionStore
    artifact_store: ArtifactStore
    session: Session
    artifact_id: str


class FakeExtractor:
    def __init__(
        self,
        result: ExtractionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        if self._error is not None:
            raise self._error
        assert image == b"ingredient-image"
        assert media_type == "image/png"
        assert self._result is not None
        return self._result


@pytest.fixture
async def extraction_context(project_tmp_path) -> AsyncIterator[ExtractionContext]:
    session_store = SessionStore(ttl_seconds=21_600)
    session = await session_store.create()
    artifact_store = ArtifactStore(
        project_tmp_path / "ingredient-extraction-artifacts",
        ttl_seconds=21_600,
    )
    await artifact_store.startup()
    artifact = await artifact_store.write(
        b"ingredient-image",
        "image/png",
        ".png",
        owner_session_id=session.id,
    )
    try:
        yield ExtractionContext(
            session_store=session_store,
            artifact_store=artifact_store,
            session=session,
            artifact_id=artifact.id,
        )
    finally:
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_graph_combines_detection_and_pantry_and_persists_final_review(
    extraction_context: ExtractionContext,
) -> None:
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    extractor = FakeExtractor(
        ExtractionResult(
            detected=[{"name": "Potato", "confidence": 0.88}],
            warnings=["Check ingredients hidden beneath the potato."],
        )
    )
    graph = build_ingredient_extraction_graph(
        ExtractionDependencies(
            extractor,
            extraction_context.session_store,
            extraction_context.artifact_store,
            progress=record_progress,
        )
    )

    result = await graph.ainvoke(
        {
            "session_id": extraction_context.session.id,
            "artifact_id": extraction_context.artifact_id,
        }
    )

    stored = await extraction_context.session_store.require(
        extraction_context.session.id
    )
    assert result["stage"] is SessionStage.REVIEWING_INGREDIENTS
    assert {item.source for item in result["ingredients"]} == {
        IngredientSource.DETECTED,
        IngredientSource.PANTRY_SUGGESTION,
    }
    assert progress_updates == [15, 65, 85, 100]
    assert stored.stage is SessionStage.REVIEWING_INGREDIENTS
    assert stored.image_artifact_id == extraction_context.artifact_id
    assert stored.ingredients == result["ingredients"]
    assert stored.warnings == ["Check ingredients hidden beneath the potato."]


@pytest.mark.asyncio
async def test_run_ingredient_extraction_returns_persisted_review_state(
    extraction_context: ExtractionContext,
) -> None:
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    dependencies = ExtractionDependencies(
        FakeExtractor(
            ExtractionResult(
                detected=[],
                warnings=[
                    "No ingredients were confidently detected. Add them manually."
                ],
            )
        ),
        extraction_context.session_store,
        extraction_context.artifact_store,
    )

    result = await run_ingredient_extraction(
        extraction_context.session.id,
        extraction_context.artifact_id,
        record_progress,
        dependencies=dependencies,
    )

    stored = await extraction_context.session_store.require(
        extraction_context.session.id
    )
    assert result["stage"] is SessionStage.REVIEWING_INGREDIENTS
    assert result["warnings"] == [
        "No ingredients were confidently detected. Add them manually."
    ]
    assert stored.warnings == result["warnings"]
    assert progress_updates == [15, 65, 85, 100]


@pytest.mark.asyncio
async def test_extractor_failure_preserves_the_unmodified_session(
    extraction_context: ExtractionContext,
) -> None:
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    graph = build_ingredient_extraction_graph(
        ExtractionDependencies(
            FakeExtractor(error=RuntimeError("vision model failed")),
            extraction_context.session_store,
            extraction_context.artifact_store,
            progress=record_progress,
        )
    )

    with pytest.raises(RuntimeError, match="vision model failed"):
        await graph.ainvoke(
            {
                "session_id": extraction_context.session.id,
                "artifact_id": extraction_context.artifact_id,
            }
        )

    stored = await extraction_context.session_store.require(
        extraction_context.session.id
    )
    assert stored == extraction_context.session
    assert stored.stage is SessionStage.EXTRACTING
    assert stored.ingredients == []
    assert stored.warnings == []
    assert progress_updates == [15]
