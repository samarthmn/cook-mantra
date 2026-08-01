import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from langgraph_api.asyncio import as_asynccontextmanager
from tests.unit.orchestration.graphs.test_recipe_options import (
    CountingLimiter,
    FailingReadyCommitStore,
    FakeDishPreviewService,
    FakeMasterChef,
    FakeNutritionAgent,
    PausingRollbackStore,
    draft,
    estimate,
    graph_for,
    invocation,
    make_generation,
    stored_option,
)

from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.artifacts import ArtifactKind
from domain.images import DishPreview, GeneratedImage, ImageGenerationRequest
from domain.recipe_options import NutritionEstimate, RecipeOptionDraft
from domain.sessions import Session, SessionStage
from orchestration.graphs import recipe_options as option_graph
from orchestration.graphs.recipe_options import (
    RecipeOptionDependencies,
    build_recipe_options_graph,
)
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.concurrency import ModelCallLimiter
from services.dish_previews import DishPreviewService
from services.image_generation import BeastImageGenerator


class RecordingDishPreviewService(FakeDishPreviewService):
    def __init__(
        self,
        results: dict[str, DishPreview | BaseException] | None = None,
        progress_values: dict[str, list[int]] | None = None,
    ) -> None:
        super().__init__(results)
        self.progress_values = progress_values or {}
        self.calls: list[tuple[str, str]] = []

    async def generate(
        self,
        session_id: str,
        option: RecipeOptionDraft,
        progress: Callable[[int], Awaitable[None]],
    ) -> DishPreview:
        self.calls.append((session_id, option.name))
        for value in self.progress_values.get(option.name, []):
            await progress(value)
            await asyncio.sleep(0)
        return await super().generate(session_id, option, progress)


class EnrichmentPhaseProbe:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.completed_nutrition: set[str] = set()
        self.previews_started_after_nutrition: list[bool] = []
        self.active_previews = 0
        self.maximum_active_previews = 0
        self.two_previews_started = asyncio.Event()
        self.release_previews = asyncio.Event()

    async def finish_nutrition(self, option_name: str) -> None:
        self.events.append(f"nutrition-start:{option_name}")
        await asyncio.sleep(0)
        self.completed_nutrition.add(option_name)
        self.events.append(f"nutrition-complete:{option_name}")

    async def hold_preview(self, option_name: str) -> None:
        self.previews_started_after_nutrition.append(
            self.completed_nutrition == {"Tomato Curry", "Tomato Rice"}
        )
        self.events.append(f"preview-start:{option_name}")
        self.active_previews += 1
        self.maximum_active_previews = max(
            self.maximum_active_previews,
            self.active_previews,
        )
        if self.active_previews == 2:
            self.two_previews_started.set()
        try:
            await self.release_previews.wait()
        finally:
            self.active_previews -= 1


class ProbedNutritionAgent:
    def __init__(self, probe: EnrichmentPhaseProbe) -> None:
        self._probe = probe

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: object,
    ) -> NutritionEstimate:
        await self._probe.finish_nutrition(option.name)
        return estimate(240 if option.name == "Tomato Curry" else 180)


class ProbedDishPreviewService:
    def __init__(self, probe: EnrichmentPhaseProbe) -> None:
        self._probe = probe

    async def generate(
        self,
        session_id: str,
        option: RecipeOptionDraft,
        progress: Callable[[int], Awaitable[None]],
    ) -> DishPreview:
        await self._probe.hold_preview(option.name)
        return DishPreview(
            artifact_id=f"preview-{option.name.casefold().replace(' ', '-')}"
        )


class BlockingNutritionAgent:
    def __init__(self, expected_calls: int = 1) -> None:
        self._expected_calls = expected_calls
        self._started_count = 0
        self.cancelled_count = 0
        self.started = asyncio.Event()
        self.cancellation_received = asyncio.Event()
        self.allow_exit = asyncio.Event()

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: object,
    ) -> NutritionEstimate:
        self._started_count += 1
        if self._started_count == self._expected_calls:
            self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as cancellation:
            self.cancelled_count += 1
            if self.cancelled_count == self._expected_calls:
                self.cancellation_received.set()
            while not self.allow_exit.is_set():
                try:
                    await self.allow_exit.wait()
                except asyncio.CancelledError:
                    continue
            raise cancellation


class BlockingDishPreviewService:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(
        self,
        session_id: str,
        option: RecipeOptionDraft,
        progress: Callable[[int], Awaitable[None]],
    ) -> DishPreview:
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.cancelled.set()
        return DishPreview(artifact_id="unreachable-preview")


class BlockingDeleteDishPreviewService(FakeDishPreviewService):
    def __init__(self) -> None:
        super().__init__()
        self.delete_started = asyncio.Event()
        self.allow_delete = asyncio.Event()

    async def delete(self, artifact_id: str) -> None:
        self.delete_started.set()
        await self.allow_delete.wait()
        await super().delete(artifact_id)


class ArtifactWritingDishPreviewService:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        block_before_write: set[str] | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._block_before_write = block_before_write or set()
        self._delete_error = delete_error
        self.generated_ids: list[str] = []
        self.written = asyncio.Event()
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.blocked_cancelled = asyncio.Event()

    async def generate(
        self,
        session_id: str,
        option: RecipeOptionDraft,
        progress: Callable[[int], Awaitable[None]],
    ) -> DishPreview:
        if option.name in self._block_before_write:
            self.blocked.set()
            try:
                await self.release.wait()
            finally:
                self.blocked_cancelled.set()
        artifact = await self._artifact_store.write(
            b"generated-preview",
            "image/png",
            ".png",
            owner_session_id=session_id,
            kind=ArtifactKind.DISH_PREVIEW,
        )
        self.generated_ids.append(artifact.id)
        self.written.set()
        return DishPreview(artifact_id=artifact.id)

    async def delete(self, artifact_id: str) -> None:
        if self._delete_error is not None:
            raise self._delete_error
        await self._artifact_store.delete(artifact_id)


class PausingReadyCommitStore(PausingRollbackStore):
    def __init__(self) -> None:
        super().__init__()
        self.pause_ready_commit = False
        self.ready_commit_started = asyncio.Event()
        self.allow_ready_commit = asyncio.Event()

    async def replace(
        self,
        session: Session,
        *,
        before_commit: Callable[[], Awaitable[None]] | None = None,
    ) -> Session:
        if self.pause_ready_commit and session.stage is SessionStage.OPTIONS_READY:
            self.ready_commit_started.set()
            await self.allow_ready_commit.wait()
        return await super().replace(session, before_commit=before_commit)


class CommitThenSuspendStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.suspend_ready_return = False
        self.ready_committed = asyncio.Event()
        self.allow_ready_return = asyncio.Event()

    async def replace(
        self,
        session: Session,
        *,
        before_commit: Callable[[], Awaitable[None]] | None = None,
    ) -> Session:
        stored = await super().replace(session, before_commit=before_commit)
        if self.suspend_ready_return and stored.stage is SessionStage.OPTIONS_READY:
            self.ready_committed.set()
            await self.allow_ready_return.wait()
        return stored


class PausingCloseClient:
    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    @property
    def is_closed(self) -> bool:
        return bool(self._delegate.is_closed)

    async def aclose(self) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        await self._delegate.aclose()


def configured_recipe_options_factory() -> Callable[[], object]:
    api_root = Path(__file__).parents[4]
    configuration = json.loads((api_root / "langgraph.json").read_text())
    source = configuration["graphs"]["recipe_options"]
    path_text, callable_name = source.rsplit(":", maxsplit=1)
    assert (api_root / path_text).resolve() == Path(option_graph.__file__).resolve()
    return getattr(option_graph, callable_name)


async def run_overlapping_configured_contexts(
    factory: Callable[[], object],
) -> option_graph.DevelopmentRecipeOptionsRuntime:
    first_context = as_asynccontextmanager(factory())
    second_context = as_asynccontextmanager(factory())
    first_enter = asyncio.create_task(first_context.__aenter__())
    await asyncio.sleep(0)
    second_enter = asyncio.create_task(second_context.__aenter__())
    enter_results = await asyncio.gather(
        first_enter,
        second_enter,
        return_exceptions=True,
    )
    entered_contexts = [
        context
        for context, result in zip(
            (first_context, second_context),
            enter_results,
            strict=True,
        )
        if not isinstance(result, BaseException)
    ]
    runtime = option_graph.get_development_recipe_options_runtime()

    try:
        for result in enter_results:
            if isinstance(result, BaseException):
                raise result
        assert enter_results == [runtime.graph, runtime.graph]
        assert runtime.artifact_store._root_fd is not None
    finally:
        await asyncio.gather(
            *(
                context.__aexit__(None, None, None)
                for context in reversed(entered_contexts)
            )
        )

    assert runtime.artifact_store._root_fd is None
    assert runtime.image_generator._client.is_closed
    return runtime


async def assert_artifact_missing(
    artifact_store: ArtifactStore,
    artifact_id: str,
) -> None:
    with pytest.raises(AppError) as raised:
        await artifact_store.require(artifact_id)
    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.asyncio
async def test_option_workflow_attaches_generated_preview_to_committed_option() -> None:
    fixture = await make_generation(option_count=1)
    previews = RecordingDishPreviewService(
        {"Tomato Curry": DishPreview(artifact_id="artifact-preview-1")}
    )
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
    )

    result = await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    preview = result["recipe_options"][0].preview
    assert preview == DishPreview(artifact_id="artifact-preview-1")
    assert saved.recipe_options[0].preview == preview
    assert previews.calls == [(fixture.generating.id, "Tomato Curry")]
    assert result["option_ids"] == [saved.recipe_options[0].id]
    assert result["batch_number"] == 1


@pytest.mark.asyncio
async def test_disabled_previews_skip_generation_and_commit_without_warnings() -> None:
    fixture = await make_generation(option_count=2)
    previews = RecordingDishPreviewService()
    limiter = CountingLimiter(max_concurrent_calls=2)
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]]),
        FakeNutritionAgent(),
        previews=previews,
        limiter=limiter,
        progress=record_progress,
        dish_previews_enabled=False,
    )

    result = await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert previews.calls == []
    assert previews.deleted_artifact_ids == []
    assert limiter.call_count == 3
    assert all(option.preview is None for option in result["recipe_options"])
    assert all(
        "Dish preview unavailable." not in option.warnings
        for option in result["recipe_options"]
    )
    assert saved.stage is SessionStage.OPTIONS_READY
    assert saved.recipe_options == result["recipe_options"]
    assert result["batch_number"] == 1
    assert progress_updates == [10, 15, 45, 80]


@pytest.mark.asyncio
async def test_preview_failure_after_nutrition_commits_option_with_warning() -> None:
    fixture = await make_generation(option_count=1)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent({"Tomato Curry": estimate(321)}),
        previews=RecordingDishPreviewService(
            {
                "Tomato Curry": AppError(
                    code=ErrorCode.IMAGE_PROVIDER_UNAVAILABLE,
                    message="The image generation provider is unavailable.",
                    status_code=503,
                    retryable=True,
                )
            }
        ),
    )

    result = await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    option = result["recipe_options"][0]
    assert option.name == "Tomato Curry"
    assert option.nutrition == estimate(321)
    assert option.preview is None
    assert option.warnings == ["Dish preview unavailable."]
    assert saved.stage is SessionStage.OPTIONS_READY
    assert saved.recipe_options[0] == option


@pytest.mark.asyncio
async def test_nutrition_and_image_failures_add_independent_warnings() -> None:
    fixture = await make_generation(option_count=1)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent({"Tomato Curry": RuntimeError("nutrition unavailable")}),
        previews=RecordingDishPreviewService(
            {"Tomato Curry": RuntimeError("image unavailable")}
        ),
    )

    result = await graph.ainvoke(invocation(fixture))

    option = result["recipe_options"][0]
    assert option.nutrition is None
    assert option.preview is None
    assert option.warnings == [
        "Nutrition estimate unavailable.",
        "Dish preview unavailable.",
    ]


@pytest.mark.asyncio
async def test_enrichment_failures_log_structured_app_error_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nutrition_failure = AppError(
        code=ErrorCode.OLLAMA_UNAVAILABLE,
        message="Nutrition provider unavailable.",
        status_code=503,
        retryable=True,
        details={"provider": "ollama"},
    )
    nutrition_failure.__cause__ = ConnectionError("nutrition transport failed")
    preview_failure = AppError(
        code=ErrorCode.IMAGE_PROVIDER_UNAVAILABLE,
        message="The image generation provider is unavailable.",
        status_code=503,
        retryable=True,
        details={"provider": "beast"},
    )
    preview_failure.__cause__ = RuntimeError("preview model initialization failed")
    fixture = await make_generation(option_count=1)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent({"Tomato Curry": nutrition_failure}),
        previews=RecordingDishPreviewService({"Tomato Curry": preview_failure}),
    )
    caplog.set_level(logging.WARNING, logger=option_graph.__name__)

    result = await graph.ainvoke(invocation(fixture))

    assert result["recipe_options"][0].warnings == [
        "Nutrition estimate unavailable.",
        "Dish preview unavailable.",
    ]
    failure_records = {
        record.enrichment_type: record
        for record in caplog.records
        if getattr(record, "event", None) == "recipe_option_enrichment_failed"
    }
    assert set(failure_records) == {"nutrition", "preview"}
    nutrition_record = failure_records["nutrition"]
    assert nutrition_record.option_index == 0
    assert nutrition_record.option_name == "Tomato Curry"
    assert nutrition_record.exception_type == "AppError"
    assert nutrition_record.error_code == ErrorCode.OLLAMA_UNAVAILABLE.value
    assert nutrition_record.error_message == "Nutrition provider unavailable."
    assert nutrition_record.status_code == 503
    assert nutrition_record.retryable is True
    assert nutrition_record.error_details == {"provider": "ollama"}
    assert nutrition_record.cause_chain == [
        "AppError: Nutrition provider unavailable.",
        "ConnectionError: nutrition transport failed",
    ]
    preview_record = failure_records["preview"]
    assert preview_record.option_index == 0
    assert preview_record.option_name == "Tomato Curry"
    assert preview_record.exception_type == "AppError"
    assert preview_record.error_code == ErrorCode.IMAGE_PROVIDER_UNAVAILABLE.value
    assert (
        preview_record.error_message == "The image generation provider is unavailable."
    )
    assert preview_record.status_code == 503
    assert preview_record.retryable is True
    assert preview_record.error_details == {"provider": "beast"}
    assert preview_record.cause_chain == [
        "AppError: The image generation provider is unavailable.",
        "RuntimeError: preview model initialization failed",
    ]


@pytest.mark.asyncio
async def test_previews_start_after_nutrition_and_bypass_model_limiter() -> None:
    fixture = await make_generation(option_count=2)
    probe = EnrichmentPhaseProbe()
    limiter = CountingLimiter(max_concurrent_calls=1)
    dependencies = RecipeOptionDependencies(
        master_chef=FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]]),
        nutrition_agent=ProbedNutritionAgent(probe),
        dish_previews=ProbedDishPreviewService(probe),
        session_store=fixture.store,
        model_call_limiter=limiter,
    )
    graph = build_recipe_options_graph(dependencies)
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(probe.two_previews_started.wait(), timeout=1)
        first_preview = next(
            index
            for index, event in enumerate(probe.events)
            if event.startswith("preview-start:")
        )
        assert all(
            event.startswith("nutrition-") for event in probe.events[:first_preview]
        )
        assert probe.previews_started_after_nutrition == [True, True]
        assert probe.maximum_active_previews == 2
        assert limiter.call_count == 3
        probe.release_previews.set()
        await graph_task
    finally:
        probe.release_previews.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert probe.maximum_active_previews == 2


@pytest.mark.asyncio
async def test_preview_progress_is_monotonic_bounded_and_never_completes_job() -> None:
    fixture = await make_generation(option_count=2)
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    previews = RecordingDishPreviewService(
        progress_values={
            "Tomato Curry": [0, 70, 30, 100],
            "Tomato Rice": [40, 10, 90, 100],
        }
    )
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]]),
        FakeNutritionAgent(),
        previews=previews,
        limiter=ModelCallLimiter(max_concurrent_calls=4),
        progress=record_progress,
    )

    await graph.ainvoke(invocation(fixture))

    assert progress_updates[:3] == [10, 15, 45]
    assert progress_updates[-1] == 80
    assert progress_updates == sorted(progress_updates)
    assert 79 in progress_updates
    assert 100 not in progress_updates
    assert all(0 <= value < 100 for value in progress_updates)


@pytest.mark.asyncio
async def test_preview_callback_progress_failure_fails_job_and_rolls_back() -> None:
    fixture = await make_generation(option_count=1)
    previews = RecordingDishPreviewService(progress_values={"Tomato Curry": [100]})

    async def fail_mapped_preview_progress(value: int) -> None:
        if value == 79:
            raise RuntimeError("progress infrastructure failed")

    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
        progress=fail_mapped_preview_progress,
    )

    with pytest.raises(RuntimeError, match="progress infrastructure failed"):
        await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )
    assert previews.deleted_artifact_ids == []


@pytest.mark.asyncio
async def test_more_preserves_prior_enrichments_and_enriches_only_new_drafts() -> None:
    prior = stored_option("Tomato Curry").model_copy(
        update={
            "nutrition": estimate(999),
            "preview": DishPreview(artifact_id="prior-preview"),
            "warnings": ["Keep prior warning."],
        }
    )
    fixture = await make_generation(
        more=True,
        option_count=1,
        previous_options=[prior],
        previous_exclusions={"tomato curry"},
        previous_batch_number=7,
    )
    nutrition = FakeNutritionAgent({"Tomato Rice": estimate(222)})
    previews = RecordingDishPreviewService(
        {"Tomato Rice": DishPreview(artifact_id="new-preview")}
    )
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Rice")]]),
        nutrition,
        previews=previews,
    )

    result = await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert saved.recipe_options[0] == prior
    assert saved.recipe_options[1].nutrition == estimate(222)
    assert saved.recipe_options[1].preview == DishPreview(artifact_id="new-preview")
    assert saved.recipe_options[1].warnings == []
    assert nutrition.completed_names == ["Tomato Rice"]
    assert previews.calls == [(fixture.generating.id, "Tomato Rice")]
    assert result["option_ids"] == [saved.recipe_options[1].id]
    assert result["batch_number"] == 8


@pytest.mark.asyncio
async def test_nutrition_phase_cancellation_drains_before_starting_previews() -> None:
    fixture = await make_generation(option_count=2)
    nutrition = BlockingNutritionAgent(expected_calls=2)
    previews = RecordingDishPreviewService()
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]]),
        nutrition,
        previews=previews,
        limiter=ModelCallLimiter(max_concurrent_calls=4),
    )
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(nutrition.started.wait(), timeout=1)
        graph_task.cancel()
        await asyncio.wait_for(nutrition.cancellation_received.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not graph_task.done()

        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        nutrition.allow_exit.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task
    finally:
        nutrition.allow_exit.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    saved = await fixture.store.require(fixture.generating.id)
    assert nutrition.cancellation_received.is_set()
    assert nutrition.cancelled_count == 2
    assert previews.calls == []
    assert previews.deleted_artifact_ids == []
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )


@pytest.mark.asyncio
async def test_partial_fanout_cancellation_drains_children_and_deletes_written_preview(
    project_tmp_path: Path,
) -> None:
    artifact_store = ArtifactStore(
        project_tmp_path / "cancelled-preview-artifacts",
        ttl_seconds=60,
    )
    await artifact_store.startup()
    previews = ArtifactWritingDishPreviewService(
        artifact_store,
        block_before_write={"Tomato Rice"},
    )
    fixture = await make_generation(option_count=2)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]]),
        FakeNutritionAgent(),
        previews=previews,
        limiter=ModelCallLimiter(max_concurrent_calls=4),
    )
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(previews.written.wait(), timeout=1)
        await asyncio.wait_for(previews.blocked.wait(), timeout=1)
        graph_task.cancel()
        graph_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await graph_task

        assert previews.blocked_cancelled.is_set()
        assert len(previews.generated_ids) == 1
        await assert_artifact_missing(artifact_store, previews.generated_ids[0])
        saved = await fixture.store.require(fixture.generating.id)
        assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
            fixture.previous
        )
    finally:
        previews.release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_progress_80_failure_deletes_every_uncommitted_preview(
    project_tmp_path: Path,
) -> None:
    artifact_store = ArtifactStore(
        project_tmp_path / "progress-failed-preview-artifacts",
        ttl_seconds=60,
    )
    await artifact_store.startup()
    previews = ArtifactWritingDishPreviewService(artifact_store)
    fixture = await make_generation(option_count=2)

    async def fail_terminal_enrichment_progress(value: int) -> None:
        if value == 80:
            raise RuntimeError("job progress write failed")

    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]]),
        FakeNutritionAgent(),
        previews=previews,
        limiter=ModelCallLimiter(max_concurrent_calls=4),
        progress=fail_terminal_enrichment_progress,
    )

    try:
        with pytest.raises(RuntimeError, match="job progress write failed"):
            await graph.ainvoke(invocation(fixture))

        assert len(previews.generated_ids) == 2
        for artifact_id in previews.generated_ids:
            await assert_artifact_missing(artifact_store, artifact_id)
    finally:
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_stale_commit_deletes_only_its_attempt_previews(
    project_tmp_path: Path,
) -> None:
    artifact_store = ArtifactStore(
        project_tmp_path / "stale-preview-artifacts",
        ttl_seconds=60,
    )
    await artifact_store.startup()
    prior_artifact = await artifact_store.write(
        b"prior-preview",
        "image/png",
        ".png",
        owner_session_id="shared-session",
        kind=ArtifactKind.DISH_PREVIEW,
    )
    prior_option = stored_option("Prior Dish").model_copy(
        update={"preview": DishPreview(artifact_id=prior_artifact.id)}
    )
    store = PausingReadyCommitStore()
    fixture = await make_generation(
        store=store,
        more=True,
        option_count=1,
        previous_options=[prior_option],
        previous_exclusions={"prior dish"},
        previous_batch_number=1,
    )
    previews = ArtifactWritingDishPreviewService(artifact_store)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Stale Dish")]]),
        FakeNutritionAgent(),
        previews=previews,
    )
    store.pause_ready_commit = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(store.ready_commit_started.wait(), timeout=1)
        assert len(previews.generated_ids) == 1
        stale_artifact_id = previews.generated_ids[0]
        newer_artifact = await artifact_store.write(
            b"newer-preview",
            "image/png",
            ".png",
            owner_session_id=fixture.generating.id,
            kind=ArtifactKind.DISH_PREVIEW,
        )
        current = await store.require(fixture.generating.id)
        newer_option = stored_option("Newer Dish").model_copy(
            update={"preview": DishPreview(artifact_id=newer_artifact.id)}
        )
        store.pause_ready_commit = False
        newer_saved = await store.replace(
            current.model_copy(
                deep=True,
                update={
                    "stage": SessionStage.OPTIONS_READY,
                    "recipe_options": [prior_option, newer_option],
                    "option_generation_id": None,
                    "updated_at": current.updated_at,
                },
            )
        )
        store.allow_ready_commit.set()

        with pytest.raises(AppError) as raised:
            await graph_task

        assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
        assert await store.require(fixture.generating.id) == newer_saved
        await assert_artifact_missing(artifact_store, stale_artifact_id)
        assert (await artifact_store.require(prior_artifact.id)).id == prior_artifact.id
        assert (await artifact_store.require(newer_artifact.id)).id == newer_artifact.id
    finally:
        store.allow_ready_commit.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_cancellation_during_preview_cleanup_finishes_rollback_and_wins() -> None:
    store = FailingReadyCommitStore()
    fixture = await make_generation(store=store, option_count=1)
    previews = BlockingDeleteDishPreviewService()
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
    )
    store.fail_ready_commits = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(store.ready_commit_started.wait(), timeout=1)
        store.allow_ready_failure.set()
        await asyncio.wait_for(previews.delete_started.wait(), timeout=1)

        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        previews.allow_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task
    finally:
        store.allow_ready_failure.set()
        previews.allow_delete.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    saved = await store.require(fixture.generating.id)
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )
    assert previews.deleted_artifact_ids == ["preview-tomato-curry"]


@pytest.mark.asyncio
async def test_preview_delete_failure_logs_without_replacing_job_failure(
    project_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    artifact_store = ArtifactStore(
        project_tmp_path / "delete-failed-preview-artifacts",
        ttl_seconds=60,
    )
    await artifact_store.startup()
    delete_error = AppError(
        code=ErrorCode.ARTIFACT_FAILURE,
        message="Preview cleanup failed.",
        status_code=500,
        retryable=False,
    )
    previews = ArtifactWritingDishPreviewService(
        artifact_store,
        delete_error=delete_error,
    )
    fixture = await make_generation(option_count=1)

    async def fail_terminal_enrichment_progress(value: int) -> None:
        if value == 80:
            raise RuntimeError("primary progress failure")

    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
        progress=fail_terminal_enrichment_progress,
    )
    caplog.set_level(logging.ERROR, logger=option_graph.__name__)

    try:
        with pytest.raises(RuntimeError, match="primary progress failure"):
            await graph.ainvoke(invocation(fixture))

        assert "Preview artifact cleanup failed" in caplog.text
        assert len(previews.generated_ids) == 1
        assert (
            await artifact_store.require(previews.generated_ids[0])
        ).id == previews.generated_ids[0]
    finally:
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_successful_commit_retains_attempt_preview_artifact(
    project_tmp_path: Path,
) -> None:
    artifact_store = ArtifactStore(
        project_tmp_path / "committed-preview-artifacts",
        ttl_seconds=60,
    )
    await artifact_store.startup()
    previews = ArtifactWritingDishPreviewService(artifact_store)
    fixture = await make_generation(option_count=1)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
    )

    try:
        result = await graph.ainvoke(invocation(fixture))

        artifact_id = result["recipe_options"][0].preview.artifact_id
        assert artifact_id == previews.generated_ids[0]
        assert (await artifact_store.require(artifact_id)).id == artifact_id
    finally:
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_cancellation_after_exact_commit_retains_committed_preview(
    project_tmp_path: Path,
) -> None:
    artifact_store = ArtifactStore(
        project_tmp_path / "post-commit-cancellation-artifacts",
        ttl_seconds=60,
    )
    await artifact_store.startup()
    store = CommitThenSuspendStore()
    fixture = await make_generation(store=store, option_count=1)
    previews = ArtifactWritingDishPreviewService(artifact_store)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
    )
    store.suspend_ready_return = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(store.ready_committed.wait(), timeout=1)
        assert len(previews.generated_ids) == 1
        artifact_id = previews.generated_ids[0]

        graph_task.cancel()
        await asyncio.sleep(0)
        store.allow_ready_return.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task

        saved = await store.require(fixture.generating.id)
        assert saved.stage is SessionStage.OPTIONS_READY
        assert saved.recipe_options[0].preview == DishPreview(artifact_id=artifact_id)
        assert (await artifact_store.require(artifact_id)).id == artifact_id
    finally:
        store.allow_ready_return.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)
        await artifact_store.shutdown()


@pytest.mark.asyncio
async def test_preview_cancellation_propagates_after_exact_attempt_rollback() -> None:
    store = PausingRollbackStore()
    fixture = await make_generation(
        store=store,
        option_count=1,
        previous_options=[stored_option("Prior Dish")],
        previous_exclusions={"prior dish"},
        previous_batch_number=4,
        previous_generation_id="prior-generation",
    )
    previews = BlockingDishPreviewService()
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent(),
        previews=previews,
    )
    store.pause_rollbacks = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await previews.started.wait()
        graph_task.cancel()
        await store.rollback_started.wait()
        assert not graph_task.done()

        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        store.allow_rollback.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task
    finally:
        store.allow_rollback.set()
        previews.release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    saved = await store.require(fixture.generating.id)
    assert previews.cancelled.is_set()
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )


@pytest.mark.asyncio
async def test_configured_factory_starts_store_before_real_preview_persistence(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "configured-development-runtime",
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    option_graph.get_development_recipe_options_runtime.cache_clear()
    monkeypatch.setattr(option_graph, "Settings", lambda **_: settings)
    runtime = option_graph.get_development_recipe_options_runtime()
    requests: list[ImageGenerationRequest] = []

    async def generate_without_network(
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> GeneratedImage:
        requests.append(request)
        await progress(100)
        return GeneratedImage(
            data=b"configured-preview",
            media_type="image/png",
            width=request.width,
            height=request.height,
        )

    monkeypatch.setattr(runtime.image_generator, "generate", generate_without_network)
    factory = configured_recipe_options_factory()

    try:
        async with as_asynccontextmanager(factory()) as configured_graph:
            assert configured_graph.get_graph().nodes
            assert runtime.artifact_store._root_fd is not None
            preview = await runtime.dependencies.dish_previews.generate(
                "configured-session",
                draft("Configured Curry"),
                lambda _: asyncio.sleep(0),
            )
            stored = await runtime.artifact_store.require(preview.artifact_id)
            assert stored.path.read_bytes() == b"configured-preview"

        assert requests
        assert runtime.image_generator._client.is_closed
        assert runtime.artifact_store._root_fd is None
    finally:
        if not runtime.image_generator._client.is_closed:
            await runtime.shutdown()
        option_graph.get_development_recipe_options_runtime.cache_clear()


@pytest.mark.asyncio
async def test_configured_langgraph_factory_reference_counts_overlapping_contexts(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "overlapping-development-runtime",
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    option_graph.get_development_recipe_options_runtime.cache_clear()
    monkeypatch.setattr(option_graph, "Settings", lambda **_: settings)
    runtime = option_graph.get_development_recipe_options_runtime()
    factory = configured_recipe_options_factory()
    first_context = as_asynccontextmanager(factory())
    second_context = as_asynccontextmanager(factory())

    try:
        first_graph = await first_context.__aenter__()
        second_graph = await second_context.__aenter__()
        assert first_graph is second_graph
        assert runtime.artifact_store._root_fd is not None

        await first_context.__aexit__(None, None, None)
        assert runtime.artifact_store._root_fd is not None
        assert not runtime.image_generator._client.is_closed

        await second_context.__aexit__(None, None, None)
        assert runtime.artifact_store._root_fd is None
        assert runtime.image_generator._client.is_closed
    finally:
        if not runtime.image_generator._client.is_closed:
            await runtime.shutdown()
        option_graph.get_development_recipe_options_runtime.cache_clear()


def test_configured_factory_replaces_lifecycle_lock_between_event_loops(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "cross-loop-development-runtime",
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    option_graph.get_development_recipe_options_runtime.cache_clear()
    monkeypatch.setattr(option_graph, "Settings", lambda **_: settings)
    original_startup = option_graph.DevelopmentRecipeOptionsRuntime.startup

    async def yielding_startup(
        runtime: option_graph.DevelopmentRecipeOptionsRuntime,
    ) -> None:
        await asyncio.sleep(0)
        await original_startup(runtime)

    monkeypatch.setattr(
        option_graph.DevelopmentRecipeOptionsRuntime,
        "startup",
        yielding_startup,
    )
    factory = configured_recipe_options_factory()

    try:
        first_runtime = asyncio.run(run_overlapping_configured_contexts(factory))
        second_runtime = asyncio.run(run_overlapping_configured_contexts(factory))
    finally:
        option_graph.get_development_recipe_options_runtime.cache_clear()

    assert first_runtime is not second_runtime


@pytest.mark.asyncio
async def test_configured_langgraph_factory_exit_drains_repeated_cancellation(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "cancelled-configured-runtime",
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    option_graph.get_development_recipe_options_runtime.cache_clear()
    monkeypatch.setattr(option_graph, "Settings", lambda **_: settings)
    runtime = option_graph.get_development_recipe_options_runtime()
    delegate_client = runtime.image_generator._client
    client = PausingCloseClient(delegate_client)
    runtime.image_generator._client = client
    context = as_asynccontextmanager(configured_recipe_options_factory()())

    try:
        await context.__aenter__()
        exit_task = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.wait_for(client.close_started.wait(), timeout=1)
        exit_task.cancel()
        await asyncio.sleep(0)
        assert not exit_task.done()

        exit_task.cancel()
        await asyncio.sleep(0)
        assert not exit_task.done()

        client.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await exit_task
    finally:
        client.allow_close.set()
        if "exit_task" in locals() and not exit_task.done():
            exit_task.cancel()
            await asyncio.gather(exit_task, return_exceptions=True)
        if not delegate_client.is_closed:
            await delegate_client.aclose()
        option_graph.get_development_recipe_options_runtime.cache_clear()

    assert client.is_closed
    assert runtime.artifact_store._root_fd is None


@pytest.mark.asyncio
async def test_development_runtime_owns_real_image_resources_without_server_call(
    project_tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "development-preview-runtime",
        dish_previews_enabled=False,
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    runtime = option_graph.DevelopmentRecipeOptionsRuntime(settings)
    preview_service = runtime.dependencies.dish_previews
    image_generator = preview_service._image_generator
    artifact_store = preview_service._artifact_store

    assert isinstance(preview_service, DishPreviewService)
    assert isinstance(image_generator, BeastImageGenerator)
    assert isinstance(artifact_store, ArtifactStore)
    assert preview_service._settings is settings
    assert image_generator._model == settings.beast_image_model
    assert runtime.dependencies.master_chef._settings is settings
    assert runtime.dependencies.nutrition_agent._settings is settings
    assert runtime.dependencies.dish_previews_enabled is settings.dish_previews_enabled

    await runtime.startup()
    try:
        assert artifact_store._root_fd is not None
    finally:
        await runtime.shutdown()

    assert image_generator._client.is_closed
    assert artifact_store._root_fd is None


@pytest.mark.asyncio
async def test_development_runtime_shutdown_drains_after_repeated_cancellation(
    project_tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "cancelled-development-runtime",
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    runtime = option_graph.DevelopmentRecipeOptionsRuntime(settings)
    preview_service = runtime.dependencies.dish_previews
    image_generator = preview_service._image_generator
    artifact_store = preview_service._artifact_store
    client = PausingCloseClient(image_generator._client)
    image_generator._client = client
    await runtime.startup()
    shutdown_task = asyncio.create_task(runtime.shutdown())

    try:
        await client.close_started.wait()
        shutdown_task.cancel()
        await asyncio.sleep(0)
        assert not shutdown_task.done()

        shutdown_task.cancel()
        await asyncio.sleep(0)
        assert not shutdown_task.done()

        client.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown_task
    finally:
        client.allow_close.set()
        if not shutdown_task.done():
            shutdown_task.cancel()
        await asyncio.gather(shutdown_task, return_exceptions=True)

    assert client.is_closed
    assert artifact_store._root_fd is None
