import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from tests.unit.orchestration.graphs.test_recipe_options import (
    CountingLimiter,
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

from core.config import Model, Settings
from domain.images import DishPreview
from domain.recipe_options import NutritionEstimate, RecipeOptionDraft
from orchestration.graphs import recipe_options as option_graph
from orchestration.graphs.recipe_options import (
    RecipeOptionDependencies,
    build_recipe_options_graph,
)
from services.artifacts import ArtifactStore
from services.concurrency import ModelCallLimiter
from services.dish_previews import DishPreviewService
from services.image_generation import OllamaImageGenerator


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


class SharedConcurrencyProbe:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.active_kinds: set[str] = set()
        self.two_kinds_started = asyncio.Event()
        self.release = asyncio.Event()

    async def hold(self, kind: str) -> None:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.active_kinds.add(kind)
        if self.active == 2 and self.active_kinds == {"nutrition", "preview"}:
            self.two_kinds_started.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1


class ProbedNutritionAgent:
    def __init__(self, probe: SharedConcurrencyProbe) -> None:
        self._probe = probe

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: object,
    ) -> NutritionEstimate:
        await self._probe.hold("nutrition")
        return estimate(240 if option.name == "Tomato Curry" else 180)


class ProbedDishPreviewService:
    def __init__(self, probe: SharedConcurrencyProbe) -> None:
        self._probe = probe

    async def generate(
        self,
        session_id: str,
        option: RecipeOptionDraft,
        progress: Callable[[int], Awaitable[None]],
    ) -> DishPreview:
        await self._probe.hold("preview")
        return DishPreview(
            artifact_id=f"preview-{option.name.casefold().replace(' ', '-')}"
        )


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
async def test_image_failure_preserves_option_with_only_image_warning() -> None:
    fixture = await make_generation(option_count=1)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
        FakeNutritionAgent({"Tomato Curry": estimate(321)}),
        previews=RecordingDishPreviewService(
            {"Tomato Curry": RuntimeError("image unavailable")}
        ),
    )

    result = await graph.ainvoke(invocation(fixture))

    option = result["recipe_options"][0]
    assert option.name == "Tomato Curry"
    assert option.nutrition == estimate(321)
    assert option.preview is None
    assert option.warnings == ["Dish preview unavailable."]


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
async def test_each_option_schedules_nutrition_and_preview_through_shared_limiter() -> (
    None
):
    fixture = await make_generation(option_count=2)
    probe = SharedConcurrencyProbe()
    limiter = CountingLimiter(max_concurrent_calls=2)
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
        await asyncio.wait_for(probe.two_kinds_started.wait(), timeout=1)
        assert probe.maximum_active == 2
        assert probe.active_kinds == {"nutrition", "preview"}
        assert limiter.call_count == 5
        probe.release.set()
        await graph_task
    finally:
        probe.release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert probe.maximum_active == 2


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

    assert progress_updates[:2] == [10, 45]
    assert progress_updates[-1] == 80
    assert progress_updates == sorted(progress_updates)
    assert 79 in progress_updates
    assert 100 not in progress_updates
    assert all(0 <= value < 100 for value in progress_updates)


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
async def test_development_runtime_owns_real_image_resources_without_server_call(
    project_tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "development-preview-runtime",
    )
    runtime = option_graph.DevelopmentRecipeOptionsRuntime(settings)
    preview_service = runtime.dependencies.dish_previews
    image_generator = preview_service._image_generator
    artifact_store = preview_service._artifact_store

    assert isinstance(preview_service, DishPreviewService)
    assert isinstance(image_generator, OllamaImageGenerator)
    assert isinstance(artifact_store, ArtifactStore)
    assert preview_service._settings is settings
    assert image_generator._model == Model.Z_IMAGE
    assert runtime.dependencies.master_chef._settings is settings
    assert runtime.dependencies.nutrition_agent._settings is settings

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
