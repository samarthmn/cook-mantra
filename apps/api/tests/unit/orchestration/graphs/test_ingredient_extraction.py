import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from core.errors import AppError, ErrorCode
from domain.ingredients import ExtractionResult, IngredientSource
from domain.sessions import Session, SessionStage
from orchestration.graphs import ingredient_extraction as extraction_graph
from orchestration.graphs.ingredient_extraction import (
    ExtractionDependencies,
    build_ingredient_extraction_graph,
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


def test_development_factory_uses_the_stable_exposed_runtime() -> None:
    runtime = extraction_graph.get_development_ingredient_extraction_runtime()

    assert extraction_graph.get_development_ingredient_extraction_runtime() is runtime
    assert extraction_graph.build_development_ingredient_extraction_graph() is (
        runtime.graph
    )


@pytest.mark.asyncio
async def test_development_runtime_lifecycle_allows_seeding_and_invocation(
    project_tmp_path,
) -> None:
    session_store = SessionStore(ttl_seconds=21_600)
    artifact_store = ArtifactStore(
        project_tmp_path / "owned-development-artifacts",
        ttl_seconds=21_600,
    )
    runtime = extraction_graph.DevelopmentIngredientExtractionRuntime(
        ExtractionDependencies(
            FakeExtractor(
                ExtractionResult(detected=[{"name": "Potato", "confidence": 0.88}])
            ),
            session_store,
            artifact_store,
        )
    )

    await runtime.startup()
    try:
        session = await session_store.create()
        artifact = await artifact_store.write(
            b"ingredient-image",
            "image/png",
            ".png",
            owner_session_id=session.id,
        )

        result = await runtime.graph.ainvoke(
            {"session_id": session.id, "artifact_id": artifact.id}
        )
    finally:
        await runtime.shutdown()

    assert result["stage"] is SessionStage.REVIEWING_INGREDIENTS


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
    completion_observer: asyncio.Task[Session] | None = None

    async def record_progress(value: int) -> None:
        nonlocal completion_observer
        progress_updates.append(value)
        if value == 100:
            completion_observer = asyncio.create_task(
                extraction_context.session_store.require(extraction_context.session.id)
            )
            await asyncio.sleep(0)
            assert not completion_observer.done()

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
    assert completion_observer is not None
    observed_at_completion = await completion_observer
    assert observed_at_completion.stage is SessionStage.REVIEWING_INGREDIENTS
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
async def test_bound_runner_reuses_application_owned_dependencies(
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

    runner = extraction_graph.build_ingredient_extraction_runner(dependencies)

    result = await runner(
        extraction_context.session.id,
        extraction_context.artifact_id,
        record_progress,
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


@pytest.mark.asyncio
async def test_graph_rejects_an_artifact_owned_by_another_session(
    extraction_context: ExtractionContext,
) -> None:
    other_session = await extraction_context.session_store.create()
    graph = build_ingredient_extraction_graph(
        ExtractionDependencies(
            FakeExtractor(
                ExtractionResult(detected=[{"name": "Potato", "confidence": 0.88}])
            ),
            extraction_context.session_store,
            extraction_context.artifact_store,
        )
    )

    with pytest.raises(AppError) as raised:
        await graph.ainvoke(
            {
                "session_id": other_session.id,
                "artifact_id": extraction_context.artifact_id,
            }
        )

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert await extraction_context.session_store.require(other_session.id) == (
        other_session
    )


@pytest.mark.asyncio
async def test_final_progress_failure_preserves_the_unmodified_session(
    extraction_context: ExtractionContext,
) -> None:
    progress_updates: list[int] = []

    async def fail_at_completion(value: int) -> None:
        progress_updates.append(value)
        if value == 100:
            raise RuntimeError("progress persistence failed")

    graph = build_ingredient_extraction_graph(
        ExtractionDependencies(
            FakeExtractor(
                ExtractionResult(detected=[{"name": "Potato", "confidence": 0.88}])
            ),
            extraction_context.session_store,
            extraction_context.artifact_store,
            progress=fail_at_completion,
        )
    )

    with pytest.raises(RuntimeError, match="progress persistence failed"):
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
    assert progress_updates == [15, 65, 85, 100]


@pytest.mark.asyncio
async def test_stale_final_save_does_not_report_completion(
    extraction_context: ExtractionContext,
) -> None:
    progress_updates: list[int] = []

    async def make_session_stale(value: int) -> None:
        progress_updates.append(value)
        if value == 85:
            await extraction_context.session_store.replace(
                extraction_context.session.model_copy(
                    update={"stage": SessionStage.RECIPES_READY}
                )
            )

    graph = build_ingredient_extraction_graph(
        ExtractionDependencies(
            FakeExtractor(
                ExtractionResult(detected=[{"name": "Potato", "confidence": 0.88}])
            ),
            extraction_context.session_store,
            extraction_context.artifact_store,
            progress=make_session_stale,
        )
    )

    with pytest.raises(AppError) as raised:
        await graph.ainvoke(
            {
                "session_id": extraction_context.session.id,
                "artifact_id": extraction_context.artifact_id,
            }
        )

    stored = await extraction_context.session_store.require(
        extraction_context.session.id
    )
    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert stored.stage is SessionStage.RECIPES_READY
    assert progress_updates == [15, 65, 85]
