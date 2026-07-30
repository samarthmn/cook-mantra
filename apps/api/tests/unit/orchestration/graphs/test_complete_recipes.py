import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from langgraph.errors import NodeCancelledError

from agents.specialized_recipe import (
    OllamaSpecializedRecipeAgent,
    SpecializedRecipeAgent,
)
from core.config import PROJECT_ROOT, Settings
from core.errors import AppError, ErrorCode
from domain.ingredients import Ingredient, IngredientSource
from domain.recipe_options import Difficulty, RecipeOption, RecipePreferences
from domain.recipe_service import (
    RecipeGenerationContext,
    begin_recipe_generation,
)
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeFailure,
    RecipeIngredient,
    RecipeStep,
)
from domain.sessions import Session, SessionStage
from orchestration.graphs import complete_recipes as recipe_graph
from orchestration.graphs.complete_recipes import (
    CompleteRecipeDependencies,
    build_complete_recipes_graph,
)
from repositories.session_store import PreCommitHook, SessionStore
from services.concurrency import ModelCallLimiter


def stored_option(
    option_id: str,
    name: str,
    *,
    cuisine: str = "Indian",
) -> RecipeOption:
    return RecipeOption(
        id=option_id,
        name=name,
        summary=f"A practical {name}.",
        cuisine=cuisine,
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato"],
    )


def complete_recipe(option: RecipeOption, *, servings: int = 2) -> CompleteRecipe:
    return CompleteRecipe(
        option_id=option.id,
        name=option.name,
        cuisine=option.cuisine,
        servings=servings,
        total_minutes=option.total_minutes,
        ingredients=(
            RecipeIngredient(
                name="Tomato",
                quantity="3 medium",
                availability=IngredientAvailability.AVAILABLE,
            ),
        ),
        steps=(RecipeStep(number=1, instruction="Cook until tender."),),
    )


class ScriptedAgent:
    def __init__(
        self,
        outcomes: dict[str, CompleteRecipe | BaseException],
    ) -> None:
        self.outcomes = outcomes
        self.calls: list[
            tuple[
                RecipeOption, list[str], RecipePreferences, asyncio.Task[object] | None
            ]
        ] = []

    async def generate(
        self,
        option: RecipeOption,
        confirmed_ingredients: list[str],
        preferences: RecipePreferences,
    ) -> CompleteRecipe:
        self.calls.append(
            (
                option.model_copy(deep=True),
                list(confirmed_ingredients),
                preferences.model_copy(deep=True),
                asyncio.current_task(),
            )
        )
        outcome = self.outcomes[option.id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome.model_copy(deep=True)


class ControlledAgent:
    def __init__(
        self,
        outcomes: dict[str, CompleteRecipe | BaseException],
    ) -> None:
        self.outcomes = outcomes
        self.release = {option_id: asyncio.Event() for option_id in outcomes}
        self.all_started = asyncio.Event()
        self.calls: list[str] = []
        self.completed: list[str] = []
        self.cancelled: list[str] = []
        self.active = 0
        self.maximum_active = 0

    async def generate(
        self,
        option: RecipeOption,
        confirmed_ingredients: list[str],
        preferences: RecipePreferences,
    ) -> CompleteRecipe:
        assert confirmed_ingredients == ["Tomato", "Onion"]
        assert preferences.servings == 2
        self.calls.append(option.id)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if len(self.calls) == len(self.outcomes):
            self.all_started.set()
        try:
            await self.release[option.id].wait()
            outcome = self.outcomes[option.id]
            if isinstance(outcome, BaseException):
                raise outcome
            self.completed.append(option.id)
            return outcome.model_copy(deep=True)
        except asyncio.CancelledError:
            self.cancelled.append(option.id)
            raise
        finally:
            self.active -= 1


class FirstCompletesOthersBlockAgent:
    def __init__(
        self,
        recipes: dict[str, CompleteRecipe],
    ) -> None:
        self.recipes = recipes
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled: list[str] = []

    async def generate(
        self,
        option: RecipeOption,
        confirmed_ingredients: list[str],
        preferences: RecipePreferences,
    ) -> CompleteRecipe:
        if option.id == next(iter(self.recipes)):
            return self.recipes[option.id].model_copy(deep=True)
        self.blocked.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(option.id)
            raise
        return self.recipes[option.id].model_copy(deep=True)


class FatalAgentSignal(BaseException):
    pass


class PrimaryGraphFailure(RuntimeError):
    pass


class CountingLimiter(ModelCallLimiter):
    def __init__(self, max_concurrent_calls: int) -> None:
        super().__init__(max_concurrent_calls)
        self.call_count = 0

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        self.call_count += 1
        return await super().run(operation)


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
        if self.pause_rollbacks and session.stage in {
            SessionStage.OPTIONS_READY,
            SessionStage.RECIPES_READY,
        }:
            self.rollback_started.set()
            await self.allow_rollback.wait()
        return await super().replace(session, before_commit=before_commit)


class FailingRecipeCommitStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.fail_recipe_commit = False

    async def replace(
        self,
        session: Session,
        *,
        before_commit: PreCommitHook | None = None,
    ) -> Session:
        if self.fail_recipe_commit and session.stage is SessionStage.RECIPES_READY:
            self.fail_recipe_commit = False
            raise AppError(
                code=ErrorCode.INVALID_SESSION_TRANSITION,
                message="The session changed before this update could be applied.",
                status_code=409,
                retryable=True,
                session_id=session.id,
            )
        return await super().replace(session, before_commit=before_commit)


class CommitThenSuspendStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.suspend_recipe_return = False
        self.recipe_committed = asyncio.Event()
        self.allow_recipe_return = asyncio.Event()

    async def replace(
        self,
        session: Session,
        *,
        before_commit: PreCommitHook | None = None,
    ) -> Session:
        stored = await super().replace(session, before_commit=before_commit)
        if self.suspend_recipe_return and stored.stage is SessionStage.RECIPES_READY:
            self.recipe_committed.set()
            await self.allow_recipe_return.wait()
        return stored


class TrackingStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.replacement_stages: list[SessionStage] = []

    async def replace(
        self,
        session: Session,
        *,
        before_commit: PreCommitHook | None = None,
    ) -> Session:
        self.replacement_stages.append(session.stage)
        return await super().replace(session, before_commit=before_commit)


@dataclass
class GenerationFixture:
    store: SessionStore
    previous: Session
    generating: Session
    context: RecipeGenerationContext
    selected: list[RecipeOption]


async def make_generation(
    *,
    store: SessionStore | None = None,
    selected_ids: tuple[str, ...] = ("option-1", "option-2"),
    previous_stage: SessionStage = SessionStage.OPTIONS_READY,
) -> GenerationFixture:
    resolved_store = store or SessionStore(ttl_seconds=21_600)
    created = await resolved_store.create(stage=previous_stage)
    options = [
        stored_option("option-1", "Tomato Curry"),
        stored_option("option-2", "Onion Soup", cuisine="French"),
        stored_option("option-3", "Spinach Rice"),
    ]
    prior_recipe = (
        {"option-1": complete_recipe(options[0])}
        if previous_stage is SessionStage.RECIPES_READY
        else {}
    )
    previous = await resolved_store.replace(
        created.model_copy(
            update={
                "ingredients": [
                    Ingredient(
                        id="ingredient-1",
                        name="Tomato",
                        source=IngredientSource.DETECTED,
                        confidence=0.98,
                        confirmed=True,
                    ),
                    Ingredient(
                        id="ingredient-2",
                        name="Onion",
                        source=IngredientSource.PANTRY_SUGGESTION,
                        confirmed=True,
                    ),
                    Ingredient(
                        id="ingredient-3",
                        name="Garlic",
                        source=IngredientSource.PANTRY_SUGGESTION,
                        confirmed=False,
                    ),
                ],
                "preferences": RecipePreferences(
                    preferred_cuisines=["Indian"],
                    servings=2,
                    option_count=3,
                ),
                "recipe_options": options,
                "complete_recipes": prior_recipe,
                "warnings": ["Preserve this warning."],
                "updated_at": created.updated_at,
            }
        )
    )
    generating, selected, _, context = begin_recipe_generation(
        previous,
        selected_ids,
    )
    persisted = await resolved_store.replace(
        generating.model_copy(update={"updated_at": previous.updated_at})
    )
    return GenerationFixture(
        store=resolved_store,
        previous=previous,
        generating=persisted,
        context=context,
        selected=selected,
    )


def invocation(
    fixture: GenerationFixture,
    *,
    option_ids: list[str] | None = None,
    previous_stage: SessionStage | str | None = None,
) -> dict[str, object]:
    return {
        "session_id": fixture.generating.id,
        "generation_context": fixture.context,
        "option_ids": option_ids
        if option_ids is not None
        else list(fixture.context.selected_option_ids),
        "previous_stage": previous_stage
        if previous_stage is not None
        else fixture.context.previous_stage,
    }


def graph_for(
    fixture: GenerationFixture,
    agent: SpecializedRecipeAgent,
    *,
    limiter: ModelCallLimiter | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
):
    dependencies = CompleteRecipeDependencies(
        agent=agent,
        session_store=fixture.store,
        model_call_limiter=limiter or ModelCallLimiter(max_concurrent_calls=2),
    )
    if progress is not None:
        dependencies = CompleteRecipeDependencies(
            agent=agent,
            session_store=fixture.store,
            model_call_limiter=dependencies.model_call_limiter,
            progress=progress,
        )
    return build_complete_recipes_graph(dependencies)


def assert_exact_restore(fixture: GenerationFixture, saved: Session) -> None:
    assert saved.model_copy(update={"updated_at": fixture.previous.updated_at}) == (
        fixture.previous
    )


@pytest.mark.asyncio
async def test_selected_options_run_concurrently_through_the_shared_limit() -> None:
    fixture = await make_generation(selected_ids=("option-1", "option-2", "option-3"))
    outcomes = {option.id: complete_recipe(option) for option in fixture.selected}
    agent = ControlledAgent(outcomes)
    limiter = CountingLimiter(max_concurrent_calls=2)
    graph = graph_for(fixture, agent, limiter=limiter)
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        while len(agent.calls) < 2:
            await asyncio.sleep(0)
        assert agent.maximum_active == 2
        for release in agent.release.values():
            release.set()
        await graph_task
    finally:
        for release in agent.release.values():
            release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert agent.maximum_active == 2
    assert limiter.call_count == 3
    assert sorted(agent.calls) == ["option-1", "option-2", "option-3"]
    assert len({task for *_, task in agent.calls}) == 3


@pytest.mark.asyncio
async def test_out_of_order_settlement_returns_context_order_from_stored_session() -> (
    None
):
    fixture = await make_generation(selected_ids=("option-3", "option-1", "option-2"))
    outcomes = {option.id: complete_recipe(option) for option in fixture.selected}
    agent = ControlledAgent(outcomes)
    graph = graph_for(
        fixture,
        agent,
        limiter=ModelCallLimiter(max_concurrent_calls=3),
    )
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(agent.all_started.wait(), timeout=1)
        for option_id in ("option-2", "option-1", "option-3"):
            agent.release[option_id].set()
            while option_id not in agent.completed:
                await asyncio.sleep(0)
        result = await graph_task
    finally:
        for release in agent.release.values():
            release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert agent.completed == ["option-2", "option-1", "option-3"]
    assert [recipe.option_id for recipe in result["complete_recipes"]] == [
        "option-3",
        "option-1",
        "option-2",
    ]
    assert result["recipe_option_ids"] == ["option-3", "option-1", "option-2"]
    assert result["selected_option_ids"] == ["option-3", "option-1", "option-2"]
    assert result["failed_option_ids"] == []


@pytest.mark.asyncio
async def test_one_failed_recipe_preserves_success_and_commits_once() -> None:
    store = TrackingStore()
    fixture = await make_generation(store=store)
    store.replacement_stages.clear()
    failure = AppError(
        code=ErrorCode.OPERATION_TIMED_OUT,
        message="Recipe generation timed out.",
        status_code=504,
        retryable=True,
        details={"provider": "private"},
        session_id="private-session",
        job_id="private-job",
    )
    agent = ScriptedAgent(
        {
            "option-1": complete_recipe(fixture.selected[0]),
            "option-2": failure,
        }
    )

    result = await graph_for(fixture, agent).ainvoke(invocation(fixture))

    saved = await store.require(fixture.generating.id)
    assert result["stage"] is SessionStage.RECIPES_READY
    assert [recipe.option_id for recipe in result["complete_recipes"]] == ["option-1"]
    assert result["recipe_failures"] == [
        RecipeFailure(
            option_id="option-2",
            code=ErrorCode.OPERATION_TIMED_OUT,
            message="Recipe generation timed out.",
            retryable=True,
        )
    ]
    assert saved.complete_recipes == {"option-1": complete_recipe(fixture.selected[0])}
    assert saved.recipe_failures == {"option-2": result["recipe_failures"][0]}
    assert store.replacement_stages == [SessionStage.RECIPES_READY]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous_stage", "selected_ids"),
    [
        (SessionStage.OPTIONS_READY, ("option-1", "option-2")),
        (SessionStage.RECIPES_READY, ("option-2", "option-3")),
    ],
)
async def test_all_failed_restores_exact_ready_snapshot(
    previous_stage: SessionStage,
    selected_ids: tuple[str, ...],
) -> None:
    fixture = await make_generation(
        previous_stage=previous_stage,
        selected_ids=selected_ids,
    )
    agent = ScriptedAgent(
        {
            option.id: RuntimeError(f"private failure for {option.id}")
            for option in fixture.selected
        }
    )

    with pytest.raises(AppError) as raised:
        await graph_for(fixture, agent).ainvoke(invocation(fixture))

    saved = await fixture.store.require(fixture.generating.id)
    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.message == "Every selected recipe failed to generate."
    assert raised.value.retryable is True
    assert_exact_restore(fixture, saved)
    assert saved.stage is previous_stage


@pytest.mark.asyncio
async def test_generic_agent_exception_is_fixed_retryable_failure_without_text() -> (
    None
):
    fixture = await make_generation()
    private_text = "secret provider payload token=abc123"
    agent = ScriptedAgent(
        {
            "option-1": complete_recipe(fixture.selected[0]),
            "option-2": RuntimeError(private_text),
        }
    )

    result = await graph_for(fixture, agent).ainvoke(invocation(fixture))

    failure = result["recipe_failures"][0]
    assert failure == RecipeFailure(
        option_id="option-2",
        code=ErrorCode.MODEL_OUTPUT_INVALID,
        message="The model returned invalid structured output.",
        retryable=True,
    )
    assert private_text not in failure.message
    assert "RuntimeError" not in failure.message


@pytest.mark.asyncio
async def test_app_error_preserves_only_public_failure_fields() -> None:
    fixture = await make_generation()
    safe_error = AppError(
        code=ErrorCode.MODEL_NOT_FOUND,
        message="The configured model is not installed.",
        status_code=503,
        retryable=False,
        details={"secret": "do-not-store"},
        session_id="unrelated-session",
        job_id="unrelated-job",
    )
    agent = ScriptedAgent(
        {
            "option-1": complete_recipe(fixture.selected[0]),
            "option-2": safe_error,
        }
    )

    result = await graph_for(fixture, agent).ainvoke(invocation(fixture))

    assert result["recipe_failures"] == [
        RecipeFailure(
            option_id="option-2",
            code=ErrorCode.MODEL_NOT_FOUND,
            message="The configured model is not installed.",
            retryable=False,
        )
    ]
    assert result["recipe_failures"][0].model_dump() == {
        "option_id": "option-2",
        "code": ErrorCode.MODEL_NOT_FOUND,
        "message": "The configured model is not installed.",
        "retryable": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("option_ids", "previous_stage"),
    [
        (["option-2", "option-1"], SessionStage.OPTIONS_READY),
        (["option-1", "option-2"], SessionStage.RECIPES_READY),
    ],
)
async def test_transient_inputs_must_match_context_before_agent_calls(
    option_ids: list[str],
    previous_stage: SessionStage,
) -> None:
    fixture = await make_generation()
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )

    with pytest.raises(AppError) as raised:
        await graph_for(fixture, agent).ainvoke(
            invocation(
                fixture,
                option_ids=option_ids,
                previous_stage=previous_stage,
            )
        )

    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert raised.value.message == "Recipe generation input does not match the session."
    assert agent.calls == []
    assert_exact_restore(
        fixture,
        await fixture.store.require(fixture.generating.id),
    )


@pytest.mark.asyncio
async def test_forged_context_is_rejected_before_agent_calls() -> None:
    fixture = await make_generation()
    forged = fixture.context.model_copy(
        update={"selected_option_ids": ("option-2", "option-1")}
    )
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )
    payload = invocation(fixture, option_ids=["option-2", "option-1"])
    payload["generation_context"] = forged

    with pytest.raises(AppError) as raised:
        await graph_for(fixture, agent).ainvoke(payload)

    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert raised.value.message == "Recipe generation context is invalid."
    assert agent.calls == []
    assert await fixture.store.require(fixture.generating.id) == fixture.generating


@pytest.mark.asyncio
async def test_stale_attempt_cannot_generate_commit_or_restore_over_new_attempt() -> (
    None
):
    fixture = await make_generation()
    newer_generating, _, _, newer_context = begin_recipe_generation(
        fixture.previous,
        ["option-3"],
    )
    current = await fixture.store.require(fixture.generating.id)
    newer_saved = await fixture.store.replace(
        newer_generating.model_copy(update={"updated_at": current.updated_at})
    )
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )

    with pytest.raises(AppError) as raised:
        await graph_for(fixture, agent).ainvoke(invocation(fixture))

    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert agent.calls == []
    assert await fixture.store.require(fixture.generating.id) == newer_saved
    assert newer_saved.recipe_generation_id == newer_context.generation_id


@pytest.mark.asyncio
async def test_progress_is_monotonic_bounded_and_updates_after_each_settlement() -> (
    None
):
    fixture = await make_generation(selected_ids=("option-1", "option-2", "option-3"))
    updates: list[int] = []

    async def record(value: int) -> None:
        updates.append(value)

    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )

    await graph_for(fixture, agent, progress=record).ainvoke(invocation(fixture))

    assert updates == [10, 36, 63, 90]
    assert updates == sorted(set(updates))
    assert all(0 < value < 100 for value in updates)


@pytest.mark.asyncio
async def test_progress_failure_cancels_and_drains_children_then_rolls_back() -> None:
    fixture = await make_generation()
    recipes = {option.id: complete_recipe(option) for option in fixture.selected}
    agent = FirstCompletesOthersBlockAgent(recipes)

    async def fail_first_settlement(value: int) -> None:
        if value == 50:
            raise RuntimeError("job progress store unavailable")

    with pytest.raises(RuntimeError, match="job progress store unavailable"):
        await graph_for(fixture, agent, progress=fail_first_settlement).ainvoke(
            invocation(fixture)
        )

    assert agent.cancelled == ["option-2"]
    assert_exact_restore(
        fixture,
        await fixture.store.require(fixture.generating.id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "primary_failure",
    [
        PrimaryGraphFailure("recipe progress failed"),
        FatalAgentSignal(),
    ],
    ids=["normal", "fatal"],
)
async def test_primary_failure_survives_repeated_cancellation_during_rollback(
    primary_failure: BaseException,
) -> None:
    store = PausingRollbackStore()
    fixture = await make_generation(store=store)
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )

    async def fail_load_progress(value: int) -> None:
        assert value == 10
        raise primary_failure

    store.pause_rollbacks = True
    graph_task = asyncio.create_task(
        graph_for(fixture, agent, progress=fail_load_progress).ainvoke(
            invocation(fixture)
        )
    )

    try:
        await asyncio.wait_for(store.rollback_started.wait(), timeout=1)
        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        store.allow_rollback.set()
        with pytest.raises(type(primary_failure)) as raised:
            await graph_task
        assert raised.value is primary_failure
    finally:
        store.allow_rollback.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert agent.calls == []
    assert_exact_restore(fixture, await store.require(fixture.generating.id))


@pytest.mark.asyncio
async def test_cancellation_drains_every_child_and_exact_rollback_despite_repeats() -> (
    None
):
    store = PausingRollbackStore()
    fixture = await make_generation(
        store=store,
        selected_ids=("option-1", "option-2", "option-3"),
    )
    agent = ControlledAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )
    graph = graph_for(
        fixture,
        agent,
        limiter=ModelCallLimiter(max_concurrent_calls=3),
    )
    store.pause_rollbacks = True
    graph_task = asyncio.create_task(graph.ainvoke(invocation(fixture)))

    try:
        await asyncio.wait_for(agent.all_started.wait(), timeout=1)
        graph_task.cancel()
        await asyncio.wait_for(store.rollback_started.wait(), timeout=1)
        assert not graph_task.done()

        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        store.allow_rollback.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task
    finally:
        store.allow_rollback.set()
        for release in agent.release.values():
            release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert sorted(agent.cancelled) == ["option-1", "option-2", "option-3"]
    assert agent.active == 0
    assert_exact_restore(fixture, await store.require(fixture.generating.id))


@pytest.mark.asyncio
async def test_agent_cancelled_error_is_not_converted_to_partial_failure() -> None:
    fixture = await make_generation()
    recipes = {option.id: complete_recipe(option) for option in fixture.selected}
    agent = ControlledAgent(
        {
            "option-1": asyncio.CancelledError(),
            "option-2": recipes["option-2"],
        }
    )
    graph_task = asyncio.create_task(
        graph_for(
            fixture,
            agent,
            limiter=ModelCallLimiter(max_concurrent_calls=2),
        ).ainvoke(invocation(fixture))
    )

    try:
        await asyncio.wait_for(agent.all_started.wait(), timeout=1)
        agent.release["option-1"].set()
        with pytest.raises(NodeCancelledError):
            await asyncio.wait_for(graph_task, timeout=1)
    finally:
        for release in agent.release.values():
            release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert "option-2" in agent.cancelled
    assert_exact_restore(
        fixture,
        await fixture.store.require(fixture.generating.id),
    )


@pytest.mark.asyncio
async def test_agent_base_exception_cancels_children_and_is_not_a_failure() -> None:
    fixture = await make_generation()
    recipes = {option.id: complete_recipe(option) for option in fixture.selected}
    fatal = FatalAgentSignal()
    agent = ControlledAgent(
        {
            "option-1": fatal,
            "option-2": recipes["option-2"],
        }
    )
    graph_task = asyncio.create_task(
        graph_for(
            fixture,
            agent,
            limiter=ModelCallLimiter(max_concurrent_calls=2),
        ).ainvoke(invocation(fixture))
    )

    try:
        await asyncio.wait_for(agent.all_started.wait(), timeout=1)
        agent.release["option-1"].set()
        with pytest.raises(FatalAgentSignal):
            await asyncio.wait_for(graph_task, timeout=1)
    finally:
        for release in agent.release.values():
            release.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    assert "option-2" in agent.cancelled
    assert_exact_restore(
        fixture,
        await fixture.store.require(fixture.generating.id),
    )


@pytest.mark.asyncio
async def test_optimistic_recipe_replace_failure_uses_exact_rollback() -> None:
    store = FailingRecipeCommitStore()
    fixture = await make_generation(store=store)
    store.fail_recipe_commit = True
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )

    with pytest.raises(AppError) as raised:
        await graph_for(fixture, agent).ainvoke(invocation(fixture))

    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert_exact_restore(fixture, await store.require(fixture.generating.id))


@pytest.mark.asyncio
async def test_cancellation_after_successful_replace_keeps_commit_and_propagates() -> (
    None
):
    store = CommitThenSuspendStore()
    fixture = await make_generation(store=store)
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )
    store.suspend_recipe_return = True
    graph_task = asyncio.create_task(
        graph_for(fixture, agent).ainvoke(invocation(fixture))
    )

    try:
        await asyncio.wait_for(store.recipe_committed.wait(), timeout=1)
        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()
        graph_task.cancel()
        await asyncio.sleep(0)
        assert not graph_task.done()

        store.allow_recipe_return.set()
        with pytest.raises(asyncio.CancelledError):
            await graph_task
    finally:
        store.allow_recipe_return.set()
        if not graph_task.done():
            graph_task.cancel()
        await asyncio.gather(graph_task, return_exceptions=True)

    saved = await store.require(fixture.generating.id)
    assert saved.stage is SessionStage.RECIPES_READY
    assert list(saved.complete_recipes) == ["option-1", "option-2"]
    assert saved.recipe_generation_id is None


@pytest.mark.asyncio
async def test_success_returns_detached_selected_results_without_rollback() -> None:
    store = TrackingStore()
    fixture = await make_generation(
        store=store,
        previous_stage=SessionStage.RECIPES_READY,
        selected_ids=("option-3", "option-2"),
    )
    store.replacement_stages.clear()
    agent = ScriptedAgent(
        {option.id: complete_recipe(option) for option in fixture.selected}
    )

    result = await graph_for(fixture, agent).ainvoke(invocation(fixture))

    saved = await store.require(fixture.generating.id)
    assert store.replacement_stages == [SessionStage.RECIPES_READY]
    assert [recipe.option_id for recipe in result["complete_recipes"]] == [
        "option-3",
        "option-2",
    ]
    assert result["recipe_failures"] == []
    assert result["stage"] is saved.stage
    assert result["complete_recipes"][0] == saved.complete_recipes["option-3"]
    assert result["complete_recipes"][0] is not saved.complete_recipes["option-3"]
    assert list(saved.complete_recipes) == ["option-1", "option-2", "option-3"]


@pytest.mark.asyncio
async def test_bound_runner_includes_exact_context_and_progress() -> None:
    fixture = await make_generation()
    updates: list[int] = []

    async def record(value: int) -> None:
        updates.append(value)

    dependencies = CompleteRecipeDependencies(
        agent=ScriptedAgent(
            {option.id: complete_recipe(option) for option in fixture.selected}
        ),
        session_store=fixture.store,
        model_call_limiter=ModelCallLimiter(max_concurrent_calls=2),
    )
    runner = recipe_graph.build_complete_recipes_runner(dependencies)

    result = await runner(
        fixture.generating.id,
        fixture.context,
        record,
    )

    assert result["selected_option_ids"] == ["option-1", "option-2"]
    assert result["stage"] is SessionStage.RECIPES_READY
    assert updates == [10, 50, 90]


def test_real_factory_uses_one_settings_lazy_agent_store_and_limiter() -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "complete-recipe-factory-test",
        max_concurrent_model_calls=3,
    )

    dependencies = recipe_graph.build_real_complete_recipe_dependencies(settings)

    assert isinstance(dependencies.agent, OllamaSpecializedRecipeAgent)
    assert dependencies.agent._settings is settings
    assert dependencies.agent._model is None
    assert isinstance(dependencies.session_store, SessionStore)
    assert isinstance(dependencies.model_call_limiter, ModelCallLimiter)
    assert dependencies.model_call_limiter._semaphore._value == 3


def test_development_factory_is_stable_lazy_and_uses_one_settings_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "complete-recipe-development-test",
    )
    settings_calls: list[dict[str, object]] = []
    recipe_graph.get_development_complete_recipes_runtime.cache_clear()

    def build_settings(**kwargs: object) -> Settings:
        settings_calls.append(kwargs)
        return settings

    monkeypatch.setattr(recipe_graph, "Settings", build_settings)
    runtime = recipe_graph.get_development_complete_recipes_runtime()

    try:
        assert recipe_graph.get_development_complete_recipes_runtime() is runtime
        assert recipe_graph.build_development_complete_recipes_graph() is runtime.graph
        assert settings_calls == [{"_env_file": None}]
        assert runtime.settings is settings
        assert runtime.dependencies.agent._settings is settings
        assert runtime.dependencies.agent._model is None
        assert runtime.graph.get_graph().nodes
    finally:
        recipe_graph.get_development_complete_recipes_runtime.cache_clear()


def test_langgraph_configuration_exposes_valid_complete_recipe_factory() -> None:
    api_root = Path(__file__).parents[4]
    configuration = json.loads((api_root / "langgraph.json").read_text())

    source = configuration["graphs"]["complete_recipes"]
    path_text, callable_name = source.rsplit(":", maxsplit=1)

    assert (api_root / path_text).resolve() == Path(recipe_graph.__file__).resolve()
    factory = getattr(recipe_graph, callable_name)
    configured_graph = factory()
    assert configured_graph.get_graph().nodes
