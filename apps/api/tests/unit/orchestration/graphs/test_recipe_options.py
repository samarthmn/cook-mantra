import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from langgraph_api.asyncio import as_asynccontextmanager
from tests.tracing_support import enabled_tracing

from agents.master_chef import OllamaMasterChef
from core.config import Agent
from core.errors import AppError, ErrorCode
from core.logging import log_context
from domain.ingredients import Ingredient, IngredientSource
from domain.recipe_option_service import (
    OptionGenerationContext,
    begin_option_generation,
)
from domain.recipe_options import (
    Difficulty,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
)
from domain.sessions import Session, SessionStage
from orchestration.graphs import recipe_options as option_graph
from orchestration.graphs.recipe_options import (
    RecipeOptionDependencies,
    build_recipe_options_graph,
)
from repositories.session_store import PreCommitHook, SessionStore
from services.concurrency import ModelCallLimiter


def draft(name: str) -> RecipeOptionDraft:
    return RecipeOptionDraft(
        name=name,
        summary=f"A practical {name}.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato"],
    )


def stored_option(name: str) -> RecipeOption:
    return RecipeOption(**draft(name).model_dump())


class FakeMasterChef:
    def __init__(
        self,
        responses: list[list[RecipeOptionDraft] | BaseException],
    ) -> None:
        self.responses = responses
        self.calls: list[set[str]] = []

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        assert ingredients == ["Tomato"]
        self.calls.append(set(excluded_names))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, BaseException):
            raise response
        assert len(response) == preferences.option_count
        return [option.model_copy(deep=True) for option in response]


class TraceCapturingMasterChef(FakeMasterChef):
    def __init__(self) -> None:
        super().__init__([[draft("Tomato Curry")]])
        self.tracing, _ = enabled_tracing()
        self.configs: list[dict[str, object]] = []

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        async def capture(config: dict[str, object]) -> None:
            self.configs.append(config)

        await self.tracing.invoke_text(Agent.MASTER_CHEF, capture)
        return await super().generate(ingredients, preferences, excluded_names)


class CountingLimiter(ModelCallLimiter):
    def __init__(self, max_concurrent_calls: int) -> None:
        super().__init__(max_concurrent_calls)
        self.call_count = 0

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        self.call_count += 1
        return await super().run(operation)


class BlockingMasterChef:
    def __init__(
        self,
        result: list[RecipeOptionDraft] | None = None,
    ) -> None:
        self.result = result or [draft("Tomato Curry")]
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        self.started.set()
        await self.release.wait()
        return [option.model_copy(deep=True) for option in self.result]


class PausingRollbackStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.pause_rollbacks = False
        self.rollback_started = asyncio.Event()
        self.allow_rollback = asyncio.Event()

    async def replace(
        self,
        session: Session,
        *,
        before_commit: PreCommitHook | None = None,
    ) -> Session:
        if (
            self.pause_rollbacks
            and session.stage is not SessionStage.GENERATING_OPTIONS
        ):
            self.rollback_started.set()
            await self.allow_rollback.wait()
        return await super().replace(session, before_commit=before_commit)


class FailingReadyCommitStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.fail_ready_commits = False
        self.ready_commit_started = asyncio.Event()
        self.allow_ready_failure = asyncio.Event()

    async def replace(
        self,
        session: Session,
        *,
        before_commit: PreCommitHook | None = None,
    ) -> Session:
        if self.fail_ready_commits and session.stage is SessionStage.OPTIONS_READY:
            self.ready_commit_started.set()
            await self.allow_ready_failure.wait()
            raise AppError(
                code=ErrorCode.INVALID_SESSION_TRANSITION,
                message="The session changed before this update could be applied.",
                status_code=409,
                retryable=True,
            )
        return await super().replace(session, before_commit=before_commit)


@dataclass
class GenerationFixture:
    store: SessionStore
    previous: Session
    generating: Session
    context: OptionGenerationContext


async def make_generation(
    *,
    store: SessionStore | None = None,
    more: bool = False,
    option_count: int = 2,
    previous_options: list[RecipeOption] | None = None,
    previous_preferences: RecipePreferences | None = None,
    previous_exclusions: set[str] | None = None,
    previous_batch_number: int = 0,
    previous_generation_id: str | None = None,
) -> GenerationFixture:
    resolved_store = store or SessionStore(ttl_seconds=21_600)
    stage = SessionStage.OPTIONS_READY if more else SessionStage.INGREDIENTS_CONFIRMED
    created = await resolved_store.create(stage=stage)
    previous = await resolved_store.replace(
        created.model_copy(
            deep=True,
            update={
                "ingredients": [
                    Ingredient(
                        id="ingredient-1",
                        name="Tomato",
                        source=IngredientSource.DETECTED,
                        confidence=0.93,
                        confirmed=True,
                    ),
                    Ingredient(
                        id="ingredient-2",
                        name="Onion",
                        source=IngredientSource.PANTRY_SUGGESTION,
                        confirmed=False,
                    ),
                ],
                "preferences": previous_preferences or RecipePreferences(),
                "recipe_options": previous_options or [],
                "excluded_recipe_names": previous_exclusions or set(),
                "option_batch_number": previous_batch_number,
                "option_generation_id": previous_generation_id,
                "warnings": ["Keep this session warning."],
                "updated_at": created.updated_at,
            },
        )
    )
    generating, _, context = begin_option_generation(
        previous,
        RecipePreferences(option_count=option_count),
        more=more,
    )
    persisted = await resolved_store.replace(
        generating.model_copy(update={"updated_at": previous.updated_at})
    )
    return GenerationFixture(
        store=resolved_store,
        previous=previous,
        generating=persisted,
        context=context,
    )


def graph_for(
    fixture: GenerationFixture,
    chef: FakeMasterChef | BlockingMasterChef,
    *,
    limiter: ModelCallLimiter | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
):
    dependencies = RecipeOptionDependencies(
        master_chef=chef,
        session_store=fixture.store,
        model_call_limiter=limiter or ModelCallLimiter(max_concurrent_calls=2),
    )
    if progress is not None:
        dependencies = RecipeOptionDependencies(
            master_chef=chef,
            session_store=fixture.store,
            model_call_limiter=dependencies.model_call_limiter,
            progress=progress,
        )
    return build_recipe_options_graph(dependencies)


def invocation(fixture: GenerationFixture) -> dict[str, object]:
    return {
        "session_id": fixture.generating.id,
        "preferences": fixture.generating.preferences,
        "more": fixture.context.more,
        "generation_context": fixture.context,
    }


@pytest.mark.asyncio
async def test_workflow_commits_text_options_atomically() -> None:
    fixture = await make_generation(option_count=2)
    chef = FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Rice")]])
    limiter = CountingLimiter(max_concurrent_calls=2)
    progress_updates: list[int] = []
    observed_before_commit: Session | None = None

    async def record_progress(value: int) -> None:
        nonlocal observed_before_commit
        progress_updates.append(value)
        if value == 80:
            observed_before_commit = await fixture.store.require(fixture.generating.id)

    graph = graph_for(
        fixture,
        chef,
        limiter=limiter,
        progress=record_progress,
    )
    assert "enrich_previews" not in graph.get_graph().nodes

    result = await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert result["stage"] is SessionStage.OPTIONS_READY
    assert result["option_ids"] == [option.id for option in saved.recipe_options]
    assert result["batch_number"] == 1
    assert [option.name for option in saved.recipe_options] == [
        "Tomato Curry",
        "Tomato Rice",
    ]
    assert saved.recipe_options[0].nutrition is None
    assert saved.recipe_options[1].nutrition is None
    assert "preview" not in RecipeOption.model_fields
    assert "warnings" not in RecipeOption.model_fields
    assert saved.excluded_recipe_names == {"tomato curry", "tomato rice"}
    assert limiter.call_count == 1
    assert progress_updates == sorted(progress_updates)
    assert progress_updates == [10, 15, 80]
    assert observed_before_commit is not None
    assert observed_before_commit.stage is SessionStage.GENERATING_OPTIONS


@pytest.mark.asyncio
async def test_duplicate_retries_report_progress_and_log_each_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = await make_generation(
        more=True,
        option_count=2,
        previous_options=[stored_option("Tomato Curry")],
        previous_exclusions={" Historic Dish "},
        previous_batch_number=0,
    )
    chef = FakeMasterChef(
        [
            [draft(" tomato  CURRY "), draft("Potato Curry")],
            [draft("Onion Soup"), draft(" onion   soup ")],
            [draft("Bean Stew"), draft("bean stew")],
            [draft("Unreachable One"), draft("Unreachable Two")],
        ]
    )
    limiter = CountingLimiter(max_concurrent_calls=2)
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    graph = graph_for(
        fixture,
        chef,
        limiter=limiter,
        progress=record_progress,
    )
    caplog.set_level(logging.INFO, logger=option_graph.__name__)

    with pytest.raises(AppError) as raised:
        await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert raised.value.code is ErrorCode.RECIPE_DUPLICATE
    assert len(chef.calls) == 3
    assert chef.calls[0] == {"tomato curry", "historic dish"}
    assert chef.calls[1] == {
        "tomato curry",
        "historic dish",
        "potato curry",
    }
    assert chef.calls[2] == {
        "tomato curry",
        "historic dish",
        "potato curry",
        "onion soup",
    }
    assert limiter.call_count == 3
    assert progress_updates == [10, 15, 25, 35]
    retry_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "recipe_option_duplicate_retry"
    ]
    assert [record.attempt_number for record in retry_records] == [1, 2]
    assert [record.option_names for record in retry_records] == [
        [" tomato  CURRY ", "Potato Curry"],
        ["Onion Soup", " onion   soup "],
    ]
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )


@pytest.mark.asyncio
async def test_more_appends_using_context_even_when_batch_number_is_zero() -> None:
    previous_option = stored_option("Tomato Curry")
    fixture = await make_generation(
        more=True,
        option_count=1,
        previous_options=[previous_option],
        previous_exclusions={"tomato curry"},
        previous_batch_number=0,
    )
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Rice")]]),
    )

    result = await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert [option.id for option in saved.recipe_options] == [
        previous_option.id,
        saved.recipe_options[1].id,
    ]
    assert [option.name for option in saved.recipe_options] == [
        "Tomato Curry",
        "Tomato Rice",
    ]
    assert saved.option_batch_number == 1
    assert result["option_ids"] == [saved.recipe_options[1].id]
    assert result["batch_number"] == 1


@pytest.mark.asyncio
async def test_fatal_failure_restores_the_complete_context_snapshot() -> None:
    previous_preferences = RecipePreferences(
        option_count=3,
        preferred_cuisines=["Japanese"],
    )
    previous_option = stored_option("Tomato Curry")
    fixture = await make_generation(
        more=True,
        option_count=1,
        previous_options=[previous_option],
        previous_preferences=previous_preferences,
        previous_exclusions={"tomato curry", "historic dish"},
        previous_batch_number=7,
        previous_generation_id="earlier-completed-generation",
    )
    graph = graph_for(
        fixture,
        FakeMasterChef([RuntimeError("chef unavailable")]),
    )

    with pytest.raises(RuntimeError, match="chef unavailable"):
        await graph.ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )
    assert saved.preferences == previous_preferences
    assert saved.recipe_options == [previous_option]
    assert saved.excluded_recipe_names == {"tomato curry", "historic dish"}
    assert saved.option_batch_number == 7
    assert saved.option_generation_id == "earlier-completed-generation"


@pytest.mark.asyncio
async def test_stale_attempt_cannot_commit_or_restore_over_a_newer_attempt() -> None:
    fixture = await make_generation(option_count=1)
    chef = BlockingMasterChef()
    graph = graph_for(fixture, chef)
    stale_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await chef.started.wait()
        newer_generating, _, newer_context = begin_option_generation(
            fixture.previous,
            RecipePreferences(option_count=2),
            more=False,
        )
        current = await fixture.store.require(fixture.generating.id)
        newer_saved = await fixture.store.replace(
            newer_generating.model_copy(update={"updated_at": current.updated_at})
        )
        chef.release.set()

        with pytest.raises(AppError) as raised:
            await stale_task
    finally:
        chef.release.set()
        if not stale_task.done():
            stale_task.cancel()
        await asyncio.gather(stale_task, return_exceptions=True)

    saved = await fixture.store.require(fixture.generating.id)
    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert saved == newer_saved
    assert saved.option_generation_id == newer_context.generation_id
    assert saved.preferences.option_count == 2


@pytest.mark.asyncio
async def test_cancellation_waits_for_safe_rollback_before_propagating() -> None:
    store = PausingRollbackStore()
    fixture = await make_generation(
        store=store,
        option_count=1,
        previous_exclusions={"preserve this"},
        previous_batch_number=4,
    )
    chef = BlockingMasterChef()
    graph = graph_for(fixture, chef)
    store.pause_rollbacks = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await chef.started.wait()
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
        chef.release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    saved = await store.require(fixture.generating.id)
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )


@pytest.mark.asyncio
async def test_cancellation_wins_when_pending_commit_later_fails() -> None:
    store = FailingReadyCommitStore()
    fixture = await make_generation(store=store, option_count=1)
    graph = graph_for(
        fixture,
        FakeMasterChef([[draft("Tomato Curry")]]),
    )
    store.fail_ready_commits = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(store.ready_commit_started.wait(), timeout=1)
        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        store.allow_ready_failure.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task
    finally:
        store.allow_ready_failure.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    saved = await store.require(fixture.generating.id)
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )


@pytest.mark.asyncio
async def test_bound_runner_uses_the_persisted_attempt_and_reports_progress() -> None:
    fixture = await make_generation(option_count=1)
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    dependencies = RecipeOptionDependencies(
        master_chef=FakeMasterChef([[draft("Tomato Curry")]]),
        session_store=fixture.store,
        model_call_limiter=ModelCallLimiter(max_concurrent_calls=2),
    )
    runner = option_graph.build_recipe_options_runner(dependencies)

    result = await runner(
        fixture.generating.id,
        fixture.generating.preferences,
        False,
        fixture.context,
        record_progress,
    )

    assert result["stage"] is SessionStage.OPTIONS_READY
    assert progress_updates == [10, 15, 80]


@pytest.mark.asyncio
async def test_graph_attaches_the_target_batch_to_each_text_model_call() -> None:
    fixture = await make_generation(option_count=1)
    master_chef = TraceCapturingMasterChef()
    limiter = CountingLimiter(max_concurrent_calls=2)
    progress_updates: list[int] = []

    async def record_progress(value: int) -> None:
        progress_updates.append(value)

    graph = build_recipe_options_graph(
        RecipeOptionDependencies(
            master_chef=master_chef,
            session_store=fixture.store,
            model_call_limiter=limiter,
            progress=record_progress,
        )
    )

    with log_context(session_id=fixture.generating.id, job_id="job-1"):
        result = await graph.ainvoke(
            {
                "session_id": fixture.generating.id,
                "preferences": fixture.generating.preferences,
                "more": False,
                "generation_context": fixture.context,
            }
        )

    assert master_chef.configs[0]["metadata"]["batch_number"] == 1
    assert result["stage"] is SessionStage.OPTIONS_READY
    assert progress_updates == [10, 15, 80]
    assert limiter.call_count == 1


@pytest.mark.asyncio
async def test_development_factory_is_stable_and_uses_one_settings_object() -> None:
    option_graph.get_development_recipe_options_runtime.cache_clear()

    runtime = option_graph.get_development_recipe_options_runtime()

    try:
        assert option_graph.get_development_recipe_options_runtime() is runtime
        async with as_asynccontextmanager(
            option_graph.build_development_recipe_options_graph()
        ) as configured_graph:
            assert configured_graph is runtime.graph
        assert isinstance(runtime.dependencies.master_chef, OllamaMasterChef)
        assert runtime.dependencies.master_chef._settings is runtime.settings
        assert runtime.dependencies.master_chef._tracing.enabled is False
        assert not hasattr(runtime.dependencies, "dish_previews")
        assert not hasattr(runtime.dependencies, "dish_previews_enabled")
        assert runtime.graph.get_graph().nodes
    finally:
        option_graph.get_development_recipe_options_runtime.cache_clear()
