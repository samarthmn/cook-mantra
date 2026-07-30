import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from functools import partial
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from agents.ingredient_extraction import IngredientExtractor
from agents.specialized_recipe import (
    OllamaSpecializedRecipeAgent,
)
from api.app import create_app
from api.dependencies import get_job_runner, get_session_store
from api.routes import recipes as recipe_routes
from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.ingredients import ExtractionResult, Ingredient, IngredientSource
from domain.jobs import JobOperation
from domain.recipe_options import Difficulty, RecipeOption, RecipePreferences
from domain.recipe_service import RecipeGenerationContext
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeIngredient,
    RecipeStep,
)
from domain.sessions import Session, SessionStage
from orchestration.graphs.complete_recipes import (
    CompleteRecipesOutput,
)
from repositories.session_store import SessionStore

type ProgressReporter = Callable[[int], Awaitable[None]]


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": [],
            "missing": [],
        }


class UnusedIngredientExtractor(IngredientExtractor):
    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        raise AssertionError("Ingredient extraction is not used by these tests.")


class UnusedSpecializedRecipeAgent:
    async def generate(
        self,
        option: RecipeOption,
        confirmed_ingredients: list[str],
        preferences: RecipePreferences,
    ) -> CompleteRecipe:
        raise AssertionError("Complete-recipe generation is not used by this test.")


class CapturingCompleteRecipesRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RecipeGenerationContext, ProgressReporter]] = []
        self.output: CompleteRecipesOutput | None = None

    async def __call__(
        self,
        session_id: str,
        generation_context: RecipeGenerationContext,
        progress: ProgressReporter,
    ) -> CompleteRecipesOutput:
        self.calls.append((session_id, generation_context, progress))
        if self.output is not None:
            return self.output
        selected = list(generation_context.selected_option_ids)
        return {
            "session_id": session_id,
            "complete_recipes": [],
            "recipe_failures": [],
            "stage": SessionStage.RECIPES_READY,
            "selected_option_ids": selected,
            "recipe_option_ids": selected,
            "failed_option_ids": [],
        }


class FakeSpecializedRecipeAgent:
    def __init__(
        self,
        results: dict[str, CompleteRecipe | BaseException],
    ) -> None:
        self.results = results
        self.calls: list[str] = []

    async def generate(
        self,
        option: RecipeOption,
        confirmed_ingredients: list[str],
        preferences: RecipePreferences,
    ) -> CompleteRecipe:
        self.calls.append(option.id)
        result = self.results[option.id]
        if isinstance(result, BaseException):
            raise result
        return result.model_copy(deep=True)


class FailingJobRunner:
    async def submit(self, operation, session_id, worker):
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Complete recipe generation could not be queued.",
            status_code=503,
            retryable=True,
            session_id=session_id,
        )


class BlockingJobRunner:
    def __init__(self) -> None:
        self.submit_started = asyncio.Event()

    async def submit(self, operation, session_id, worker):
        self.submit_started.set()
        await asyncio.Event().wait()


class PausingRollbackSessionStore(SessionStore):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.pause_rollbacks = False
        self.rollback_started = asyncio.Event()
        self.allow_rollback = asyncio.Event()

    async def replace(self, session, *, before_commit=None):
        if (
            self.pause_rollbacks
            and session.stage is not SessionStage.GENERATING_RECIPES
        ):
            self.rollback_started.set()
            await self.allow_rollback.wait()
        return await super().replace(session, before_commit=before_commit)


class AdvancingFailingJobRunner:
    """Commit a newer result before surfacing the route's submission failure."""

    def __init__(
        self,
        store: SessionStore,
        replacement_recipe: CompleteRecipe,
    ) -> None:
        self.store = store
        self.replacement_recipe = replacement_recipe

    async def submit(self, operation, session_id, worker):
        current = await self.store.require(session_id)
        await self.store.replace(
            current.model_copy(
                update={
                    "stage": SessionStage.RECIPES_READY,
                    "recipe_generation_id": None,
                    "complete_recipes": {
                        self.replacement_recipe.option_id: self.replacement_recipe
                    },
                    "updated_at": current.updated_at,
                }
            )
        )
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Queue ownership was not transferred.",
            status_code=503,
            retryable=True,
            session_id=session_id,
        )


def stored_option(option_id: str, name: str) -> RecipeOption:
    return RecipeOption(
        id=option_id,
        name=name,
        summary=f"A practical {name}.",
        cuisine="Indian",
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
        total_minutes=30,
        ingredients=[
            RecipeIngredient(
                name="Tomato",
                quantity="2 medium",
                availability=IngredientAvailability.AVAILABLE,
            )
        ],
        steps=[
            RecipeStep(
                number=1,
                instruction="Cook the tomato until tender.",
                duration_minutes=10,
            )
        ],
    )


async def store_options_session(
    store: SessionStore,
    *,
    stage: SessionStage = SessionStage.OPTIONS_READY,
    options: list[RecipeOption] | None = None,
) -> Session:
    created = await store.create(stage=stage)
    return await store.replace(
        created.model_copy(
            update={
                "ingredients": [
                    Ingredient(
                        id="ingredient-tomato",
                        name="Tomato",
                        source=IngredientSource.DETECTED,
                        confidence=0.98,
                        confirmed=True,
                    ),
                    Ingredient(
                        id="ingredient-salt",
                        name="Salt",
                        source=IngredientSource.PANTRY_SUGGESTION,
                        confirmed=False,
                    ),
                ],
                "preferences": RecipePreferences(servings=2, option_count=2),
                "recipe_options": options
                or [
                    stored_option("option-curry", "Tomato Curry"),
                    stored_option("option-soup", "Tomato Soup"),
                ],
                "warnings": ["Keep this warning."],
                "updated_at": created.updated_at,
            }
        )
    )


def poll_job(client: TestClient, job_id: str):
    for _ in range(100):
        response = client.get(f"/api/v1/jobs/{job_id}")
        if response.json()["status"] in {"succeeded", "failed"}:
            return response
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish.")


@pytest.fixture
def graph_spy() -> CapturingCompleteRecipesRunner:
    return CapturingCompleteRecipesRunner()


@pytest.fixture
def app(
    project_tmp_path: Path,
    graph_spy: CapturingCompleteRecipesRunner,
) -> FastAPI:
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        specialized_recipe_agent=UnusedSpecializedRecipeAgent(),
    )
    application.state.complete_recipes_runner = graph_spy
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def options_session(client: TestClient) -> Session:
    return client.portal.call(
        store_options_session,
        client.app.state.session_store,
    )


def test_recipe_request_returns_job_and_passes_exact_generation_context(
    client: TestClient,
    options_session: Session,
    graph_spy: CapturingCompleteRecipesRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returned_contexts: list[RecipeGenerationContext] = []
    real_begin = recipe_routes.begin_recipe_generation

    def capture_context(session, option_ids):
        result = real_begin(session, option_ids)
        returned_contexts.append(result[3])
        return result

    monkeypatch.setattr(recipe_routes, "begin_recipe_generation", capture_context)

    response = client.post(
        f"/api/v1/sessions/{options_session.id}/recipes",
        json={"option_ids": ["option-soup", "option-curry"]},
    )

    assert response.status_code == 202
    assert response.json()["session_id"] == options_session.id
    assert response.json()["job_id"]
    job = client.portal.call(
        client.app.state.job_store.require,
        response.json()["job_id"],
    )
    client.portal.call(
        client.app.state.job_runner.wait,
        response.json()["job_id"],
    )
    saved = client.portal.call(
        client.app.state.session_store.require,
        options_session.id,
    )

    assert job.operation is JobOperation.GENERATE_RECIPES
    assert saved.stage is SessionStage.GENERATING_RECIPES
    assert saved.recipe_generation_id is not None
    assert len(returned_contexts) == 1
    assert len(graph_spy.calls) == 1
    session_id, context, _ = graph_spy.calls[0]
    assert session_id == options_session.id
    assert context is returned_contexts[0]
    assert context.selected_option_ids == ("option-soup", "option-curry")
    assert context.generation_id == saved.recipe_generation_id


def test_successful_job_exposes_complete_recipe_from_stored_workflow_output(
    project_tmp_path: Path,
) -> None:
    curry = stored_option("option-curry", "Tomato Curry")
    recipe = complete_recipe(curry)
    agent = FakeSpecializedRecipeAgent({curry.id: recipe})
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        specialized_recipe_agent=agent,
    )

    with TestClient(application) as test_client:
        session = test_client.portal.call(
            partial(
                store_options_session,
                application.state.session_store,
                options=[curry],
            )
        )
        queued = test_client.post(
            f"/api/v1/sessions/{session.id}/recipes",
            json={"option_ids": [curry.id]},
        )
        job = poll_job(test_client, queued.json()["job_id"])
        saved = test_client.get(f"/api/v1/sessions/{session.id}")

    assert queued.status_code == 202
    assert job.json()["status"] == "succeeded"
    assert job.json()["result"] == {
        "selected_option_ids": [curry.id],
        "recipe_option_ids": [curry.id],
        "failed_option_ids": [],
    }
    assert saved.json()["stage"] == "recipes_ready"
    assert saved.json()["complete_recipes"][curry.id]["name"] == "Tomato Curry"
    assert saved.json()["recipe_failures"] == {}


def test_partial_success_keeps_recipe_and_safe_failure(
    project_tmp_path: Path,
) -> None:
    curry = stored_option("option-curry", "Tomato Curry")
    soup = stored_option("option-soup", "Tomato Soup")
    agent = FakeSpecializedRecipeAgent(
        {
            curry.id: complete_recipe(curry),
            soup.id: AppError(
                code=ErrorCode.MODEL_OUTPUT_INVALID,
                message="The model returned invalid structured output.",
                status_code=502,
                retryable=True,
            ),
        }
    )
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        specialized_recipe_agent=agent,
    )

    with TestClient(application) as test_client:
        session = test_client.portal.call(
            partial(
                store_options_session,
                application.state.session_store,
                options=[curry, soup],
            )
        )
        queued = test_client.post(
            f"/api/v1/sessions/{session.id}/recipes",
            json={"option_ids": [curry.id, soup.id]},
        )
        job = poll_job(test_client, queued.json()["job_id"])
        saved = test_client.get(f"/api/v1/sessions/{session.id}")

    assert job.json()["status"] == "succeeded"
    assert job.json()["result"] == {
        "selected_option_ids": [curry.id, soup.id],
        "recipe_option_ids": [curry.id],
        "failed_option_ids": [soup.id],
    }
    assert list(saved.json()["complete_recipes"]) == [curry.id]
    assert saved.json()["recipe_failures"][soup.id] == {
        "option_id": soup.id,
        "code": "model_output_invalid",
        "message": "The model returned invalid structured output.",
        "retryable": True,
    }


def test_all_failed_job_restores_exact_previous_session(
    project_tmp_path: Path,
) -> None:
    curry = stored_option("option-curry", "Tomato Curry")
    failure = AppError(
        code=ErrorCode.MODEL_OUTPUT_INVALID,
        message="The model returned invalid structured output.",
        status_code=502,
        retryable=True,
    )
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        specialized_recipe_agent=FakeSpecializedRecipeAgent({curry.id: failure}),
    )

    with TestClient(application) as test_client:
        previous = test_client.portal.call(
            partial(
                store_options_session,
                application.state.session_store,
                options=[curry],
            )
        )
        queued = test_client.post(
            f"/api/v1/sessions/{previous.id}/recipes",
            json={"option_ids": [curry.id]},
        )
        job = poll_job(test_client, queued.json()["job_id"])
        restored = test_client.portal.call(
            application.state.session_store.require,
            previous.id,
        )

    assert job.json()["status"] == "failed"
    assert job.json()["error"]["code"] == "model_output_invalid"
    assert restored.model_dump(exclude={"updated_at"}) == previous.model_dump(
        exclude={"updated_at"}
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"option_ids": []},
        {"option_ids": ["option-curry", "option-curry"]},
        {"option_ids": [" "]},
        {"option_ids": [f"option-{index}" for index in range(7)]},
    ],
)
def test_recipe_request_rejects_invalid_selections(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    response = client.post("/api/v1/sessions/unused/recipes", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_recipe_request_rejects_unknown_option(
    client: TestClient,
    options_session: Session,
) -> None:
    response = client.post(
        f"/api/v1/sessions/{options_session.id}/recipes",
        json={"option_ids": ["unknown-option"]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Unknown recipe option."


def test_recipe_request_rejects_wrong_stage(client: TestClient) -> None:
    session = client.portal.call(
        partial(
            store_options_session,
            client.app.state.session_store,
            stage=SessionStage.REVIEWING_INGREDIENTS,
        )
    )

    response = client.post(
        f"/api/v1/sessions/{session.id}/recipes",
        json={"option_ids": ["option-curry"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_session_transition"


def test_submission_failure_restores_exact_previous_session(
    app: FastAPI,
) -> None:
    app.dependency_overrides[get_job_runner] = FailingJobRunner

    with TestClient(app) as test_client:
        previous = test_client.portal.call(
            store_options_session,
            app.state.session_store,
        )
        response = test_client.post(
            f"/api/v1/sessions/{previous.id}/recipes",
            json={"option_ids": ["option-curry"]},
        )
        restored = test_client.portal.call(
            app.state.session_store.require,
            previous.id,
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ollama_unavailable"
    assert restored.model_dump(exclude={"updated_at"}) == previous.model_dump(
        exclude={"updated_at"}
    )


@pytest.mark.asyncio
async def test_request_cancellation_drains_rollback_despite_repeated_cancel(
    project_tmp_path: Path,
) -> None:
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        specialized_recipe_agent=UnusedSpecializedRecipeAgent(),
    )
    session_store = PausingRollbackSessionStore()
    job_runner = BlockingJobRunner()
    previous = await store_options_session(session_store)
    session_store.pause_rollbacks = True
    application.dependency_overrides[get_session_store] = lambda: session_store
    application.dependency_overrides[get_job_runner] = lambda: job_runner

    transport = ASGITransport(app=application)
    request_task: asyncio.Task | None = None
    rollback_wait: asyncio.Task | None = None
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        request_task = asyncio.create_task(
            client.post(
                f"/api/v1/sessions/{previous.id}/recipes",
                json={"option_ids": ["option-curry"]},
            )
        )
        submit_wait = asyncio.create_task(job_runner.submit_started.wait())
        try:
            completed, _ = await asyncio.wait(
                {request_task, submit_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert submit_wait in completed
            assert not request_task.done()

            request_task.cancel()
            rollback_wait = asyncio.create_task(session_store.rollback_started.wait())
            completed, _ = await asyncio.wait(
                {request_task, rollback_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert rollback_wait in completed
            assert not request_task.done()

            request_task.cancel()
            await asyncio.sleep(0)
            assert not request_task.done()

            session_store.allow_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await request_task
            restored = await session_store.require(previous.id)
            assert restored.model_dump(exclude={"updated_at"}) == previous.model_dump(
                exclude={"updated_at"}
            )
        finally:
            session_store.allow_rollback.set()
            if not submit_wait.done():
                submit_wait.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await submit_wait
            if rollback_wait is not None and not rollback_wait.done():
                rollback_wait.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await rollback_wait
            if request_task is not None and not request_task.done():
                request_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request_task


def test_stale_submission_rollback_cannot_erase_newer_result(
    project_tmp_path: Path,
) -> None:
    curry = stored_option("option-curry", "Tomato Curry")
    newer_recipe = complete_recipe(curry)
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        specialized_recipe_agent=UnusedSpecializedRecipeAgent(),
    )
    runner = AdvancingFailingJobRunner(
        application.state.session_store,
        newer_recipe,
    )
    application.dependency_overrides[get_job_runner] = lambda: runner

    with TestClient(application) as client:
        previous = client.portal.call(
            partial(
                store_options_session,
                application.state.session_store,
                options=[curry],
            )
        )
        response = client.post(
            f"/api/v1/sessions/{previous.id}/recipes",
            json={"option_ids": [curry.id]},
        )
        stored = client.portal.call(
            application.state.session_store.require,
            previous.id,
        )

    assert response.status_code == 503
    assert stored.stage is SessionStage.RECIPES_READY
    assert stored.complete_recipes == {curry.id: newer_recipe}


def test_job_result_is_detached_from_mutable_workflow_output(
    client: TestClient,
    options_session: Session,
    graph_spy: CapturingCompleteRecipesRunner,
) -> None:
    graph_spy.output = {
        "session_id": options_session.id,
        "complete_recipes": [],
        "recipe_failures": [],
        "stage": SessionStage.RECIPES_READY,
        "selected_option_ids": ["option-curry"],
        "recipe_option_ids": ["option-curry"],
        "failed_option_ids": [],
    }
    response = client.post(
        f"/api/v1/sessions/{options_session.id}/recipes",
        json={"option_ids": ["option-curry"]},
    )
    client.portal.call(client.app.state.job_runner.wait, response.json()["job_id"])

    graph_spy.output["selected_option_ids"].append("mutated")
    graph_spy.output["recipe_option_ids"].clear()
    job = client.portal.call(
        client.app.state.job_store.require,
        response.json()["job_id"],
    )

    assert job.result == {
        "selected_option_ids": ["option-curry"],
        "recipe_option_ids": ["option-curry"],
        "failed_option_ids": [],
    }


def test_app_wires_recipe_agent_to_exact_settings_store_and_shared_limiter(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_model_construction(*args, **kwargs):
        raise AssertionError("Application construction must not open a model.")

    monkeypatch.setattr(
        "agents.specialized_recipe.get_model",
        reject_model_construction,
    )
    settings = Settings(_env_file=None, artifact_root=project_tmp_path)

    application = create_app(
        settings=settings,
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
    )

    dependencies = application.state.complete_recipes_dependencies
    assert isinstance(dependencies.agent, OllamaSpecializedRecipeAgent)
    assert dependencies.agent._settings is settings
    assert dependencies.session_store is application.state.session_store
    assert dependencies.model_call_limiter is application.state.model_call_limiter
    assert application.state.complete_recipes_runner


def test_recipe_route_openapi_uses_public_contracts(client: TestClient) -> None:
    operation = client.app.openapi()["paths"]["/api/v1/sessions/{session_id}/recipes"][
        "post"
    ]

    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QueuedJobResponse"
    }
    for status_code in ("404", "409", "422"):
        assert operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ErrorResponse"}
