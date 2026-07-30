import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from functools import partial
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from agents.ingredient_extraction import IngredientExtractor
from agents.master_chef import OllamaMasterChef
from agents.nutrition import OllamaNutritionAgent
from api.app import create_app
from api.dependencies import get_job_runner, get_session_store
from api.routes import recipe_options as recipe_option_routes
from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.ingredients import ExtractionResult, Ingredient, IngredientSource
from domain.jobs import JobOperation
from domain.recipe_option_service import OptionGenerationContext
from domain.recipe_options import (
    Difficulty,
    NutritionEstimate,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
)
from domain.sessions import Session, SessionStage
from orchestration.graphs.recipe_options import RecipeOptionOutput, RecipeOptionsRunner
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


class CapturingRecipeOptionsRunner:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                str,
                RecipePreferences | Mapping[str, object],
                bool,
                OptionGenerationContext,
            ]
        ] = []

    async def __call__(
        self,
        session_id: str,
        preferences: RecipePreferences | Mapping[str, object],
        more: bool,
        generation_context: OptionGenerationContext,
        progress: ProgressReporter,
    ) -> RecipeOptionOutput:
        self.calls.append(
            (session_id, preferences, more, generation_context),
        )
        return {
            "session_id": session_id,
            "recipe_options": [],
            "stage": SessionStage.OPTIONS_READY,
            "option_ids": [],
            "batch_number": 0,
        }


class FakeMasterChef:
    def __init__(self, responses: list[list[RecipeOptionDraft]]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[str], RecipePreferences, set[str]]] = []

    async def generate(
        self,
        ingredients: list[str],
        preferences: RecipePreferences,
        excluded_names: set[str],
    ) -> list[RecipeOptionDraft]:
        self.calls.append(
            (
                list(ingredients),
                preferences.model_copy(deep=True),
                set(excluded_names),
            )
        )
        return [
            option.model_copy(deep=True)
            for option in self.responses[len(self.calls) - 1]
        ]


class FakeNutritionAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RecipePreferences]] = []

    async def estimate(
        self,
        option: RecipeOptionDraft,
        preferences: RecipePreferences,
    ) -> NutritionEstimate:
        self.calls.append(
            (option.name, preferences.model_copy(deep=True)),
        )
        return NutritionEstimate(
            calories_kcal=240,
            protein_g=8,
            carbohydrates_g=32,
            fat_g=9,
        )


class FailingJobRunner:
    async def submit(self, operation, session_id, worker):
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Recipe generation could not be queued.",
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
            and session.stage is not SessionStage.GENERATING_OPTIONS
        ):
            self.rollback_started.set()
            await self.allow_rollback.wait()
        return await super().replace(session, before_commit=before_commit)


class DelayedFirstCommittedResult:
    """Hold the first graph result after commit while a later batch completes."""

    def __init__(self, delegate: RecipeOptionsRunner) -> None:
        self._delegate = delegate
        self.first_committed = asyncio.Event()
        self.release_first = asyncio.Event()

    async def __call__(
        self,
        session_id: str,
        preferences: RecipePreferences | Mapping[str, object],
        more: bool,
        generation_context: OptionGenerationContext,
        progress: ProgressReporter,
    ) -> RecipeOptionOutput:
        result = await self._delegate(
            session_id,
            preferences,
            more,
            generation_context,
            progress,
        )
        if not more:
            self.first_committed.set()
            await self.release_first.wait()
        return result


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


def poll_job(client: TestClient, job_id: str):
    for _ in range(100):
        response = client.get(f"/api/v1/jobs/{job_id}")
        if response.json()["status"] in {"succeeded", "failed"}:
            return response
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish.")


@pytest.fixture
def graph_spy() -> CapturingRecipeOptionsRunner:
    return CapturingRecipeOptionsRunner()


@pytest.fixture
def app(
    project_tmp_path: Path,
    graph_spy: CapturingRecipeOptionsRunner,
) -> FastAPI:
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
    )
    application.state.recipe_options_runner = graph_spy
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


async def store_session(
    store: SessionStore,
    *,
    stage: SessionStage,
    options: list[RecipeOption] | None = None,
    exclusions: set[str] | None = None,
    batch_number: int = 0,
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
                        confidence=0.96,
                        confirmed=True,
                    ),
                    Ingredient(
                        id="ingredient-salt",
                        name="Salt",
                        source=IngredientSource.PANTRY_SUGGESTION,
                        confirmed=False,
                    ),
                ],
                "recipe_options": options or [],
                "excluded_recipe_names": exclusions or set(),
                "option_batch_number": batch_number,
                "warnings": ["Keep this warning."],
                "updated_at": created.updated_at,
            }
        )
    )


@pytest.fixture
def confirmed_session(client: TestClient) -> Session:
    return client.portal.call(
        partial(
            store_session,
            client.app.state.session_store,
            stage=SessionStage.INGREDIENTS_CONFIRMED,
        )
    )


@pytest.fixture
def options_session(client: TestClient) -> Session:
    return client.portal.call(
        partial(
            store_session,
            client.app.state.session_store,
            stage=SessionStage.OPTIONS_READY,
            options=[
                stored_option("Tomato Curry"),
                stored_option("  Lentil   Soup  "),
            ],
            exclusions={"PASTA"},
            batch_number=1,
        )
    )


def test_first_option_request_returns_job_and_persists_generating_state(
    client: TestClient,
    confirmed_session: Session,
    graph_spy: CapturingRecipeOptionsRunner,
) -> None:
    response = client.post(
        f"/api/v1/sessions/{confirmed_session.id}/recipe-options",
        json={"servings": 2, "option_count": 4},
    )

    assert response.status_code == 202
    assert response.json()["session_id"] == confirmed_session.id
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
        confirmed_session.id,
    )

    assert job.operation is JobOperation.GENERATE_OPTIONS
    assert saved.stage is SessionStage.GENERATING_OPTIONS
    assert saved.preferences == RecipePreferences(servings=2, option_count=4)
    assert saved.option_generation_id is not None
    assert len(graph_spy.calls) == 1
    session_id, preferences, more, context = graph_spy.calls[0]
    assert session_id == confirmed_session.id
    assert preferences == saved.preferences
    assert more is False
    assert context.generation_id == saved.option_generation_id
    assert context.previous_stage is SessionStage.INGREDIENTS_CONFIRMED
    assert context.more is False


def test_more_persists_every_canonical_shown_name_and_uses_more_operation(
    client: TestClient,
    options_session: Session,
    graph_spy: CapturingRecipeOptionsRunner,
) -> None:
    response = client.post(
        f"/api/v1/sessions/{options_session.id}/recipe-options/more",
        json={"option_count": 2},
    )

    assert response.status_code == 202
    client.portal.call(
        client.app.state.job_runner.wait,
        response.json()["job_id"],
    )
    job = client.portal.call(
        client.app.state.job_store.require,
        response.json()["job_id"],
    )
    saved = client.portal.call(
        client.app.state.session_store.require,
        options_session.id,
    )

    assert job.operation is JobOperation.GENERATE_MORE_OPTIONS
    assert saved.stage is SessionStage.GENERATING_OPTIONS
    assert saved.excluded_recipe_names == {
        "tomato curry",
        "lentil soup",
        "pasta",
    }
    assert len(graph_spy.calls) == 1
    assert graph_spy.calls[0][2] is True
    assert graph_spy.calls[0][3].more is True
    assert graph_spy.calls[0][3].previous_stage is SessionStage.OPTIONS_READY


def test_worker_receives_the_exact_context_returned_by_domain_service(
    client: TestClient,
    confirmed_session: Session,
    graph_spy: CapturingRecipeOptionsRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returned_contexts: list[OptionGenerationContext] = []
    real_begin = recipe_option_routes.begin_option_generation

    def capture_context(session, preferences, more):
        result = real_begin(session, preferences, more)
        returned_contexts.append(result[2])
        return result

    monkeypatch.setattr(
        recipe_option_routes,
        "begin_option_generation",
        capture_context,
    )

    response = client.post(
        f"/api/v1/sessions/{confirmed_session.id}/recipe-options",
        json={"option_count": 1},
    )
    assert response.status_code == 202
    client.portal.call(
        client.app.state.job_runner.wait,
        response.json()["job_id"],
    )

    assert len(returned_contexts) == 1
    assert len(graph_spy.calls) == 1
    assert graph_spy.calls[0][3] is returned_contexts[0]


@pytest.mark.parametrize(
    ("more", "stage"),
    [
        (False, SessionStage.REVIEWING_INGREDIENTS),
        (False, SessionStage.OPTIONS_READY),
        (False, SessionStage.GENERATING_OPTIONS),
        (True, SessionStage.INGREDIENTS_CONFIRMED),
        (True, SessionStage.GENERATING_OPTIONS),
    ],
)
def test_option_requests_reject_wrong_stages(
    client: TestClient,
    more: bool,
    stage: SessionStage,
) -> None:
    session = client.portal.call(
        partial(
            store_session,
            client.app.state.session_store,
            stage=stage,
        )
    )
    suffix = "/more" if more else ""

    response = client.post(
        f"/api/v1/sessions/{session.id}/recipe-options{suffix}",
        json={"option_count": 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_session_transition"
    expected_stage = "options_ready" if more else "ingredients_confirmed"
    assert response.json()["error"]["message"] == (
        f"Session must be {expected_stage} for this operation."
    )
    assert response.json()["error"]["session_id"] == session.id


@pytest.mark.parametrize("suffix", ["", "/more"])
def test_option_requests_validate_recipe_preferences(
    client: TestClient,
    suffix: str,
) -> None:
    response = client.post(
        f"/api/v1/sessions/unused/recipe-options{suffix}",
        json={"servings": 0, "option_count": 7},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("suffix", ["", "/more"])
def test_option_requests_use_standard_unknown_session_error(
    client: TestClient,
    suffix: str,
) -> None:
    response = client.post(
        f"/api/v1/sessions/missing-session/recipe-options{suffix}",
        json={"option_count": 1},
    )

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "resource_not_found",
        "message": "Session was not found.",
        "details": {},
        "retryable": False,
        "request_id": response.headers["X-Request-ID"],
        "session_id": "missing-session",
        "job_id": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/sessions/{session_id}/recipe-options",
        "/api/v1/sessions/{session_id}/recipe-options/more",
    ],
)
def test_option_route_openapi_errors_use_public_envelope(
    client: TestClient,
    path: str,
) -> None:
    responses = client.app.openapi()["paths"][path]["post"]["responses"]
    error_schemas = {
        status_code: responses.get(status_code, {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
        for status_code in ("404", "409", "422")
    }

    assert error_schemas == {
        "404": {"$ref": "#/components/schemas/ErrorResponse"},
        "409": {"$ref": "#/components/schemas/ErrorResponse"},
        "422": {"$ref": "#/components/schemas/ErrorResponse"},
    }


def test_job_producing_routes_share_generic_queued_job_contract(
    client: TestClient,
) -> None:
    document = client.app.openapi()
    operations = [
        document["paths"]["/api/v1/sessions"]["post"],
        document["paths"]["/api/v1/sessions/{session_id}/recipe-options"]["post"],
        document["paths"]["/api/v1/sessions/{session_id}/recipe-options/more"]["post"],
    ]

    for operation in operations:
        assert operation["responses"]["202"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/QueuedJobResponse"}

    component = document["components"]["schemas"]["QueuedJobResponse"]
    assert component["title"] == "QueuedJobResponse"
    assert component["description"] == (
        "Identifiers returned after a background job is queued."
    )
    assert set(component["properties"]) == {"session_id", "job_id"}
    assert component["required"] == ["session_id", "job_id"]
    assert "SessionCreatedResponse" not in document["components"]["schemas"]


def test_app_wires_recipe_graph_to_exact_settings_stores_and_shared_limiter(
    project_tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, artifact_root=project_tmp_path)

    application = create_app(
        settings=settings,
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
    )

    dependencies = application.state.recipe_options_dependencies
    assert isinstance(dependencies.master_chef, OllamaMasterChef)
    assert isinstance(dependencies.nutrition_agent, OllamaNutritionAgent)
    assert dependencies.master_chef._settings is settings
    assert dependencies.nutrition_agent._settings is settings
    assert dependencies.session_store is application.state.session_store
    assert dependencies.model_call_limiter is application.state.model_call_limiter


def test_first_successful_job_polls_to_committed_session_through_real_boundaries(
    project_tmp_path: Path,
) -> None:
    chef = FakeMasterChef([[draft("Tomato Curry"), draft("Tomato Soup")]])
    nutrition = FakeNutritionAgent()
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        master_chef=chef,
        nutrition_agent=nutrition,
    )

    with TestClient(application) as test_client:
        confirmed = test_client.portal.call(
            partial(
                store_session,
                application.state.session_store,
                stage=SessionStage.INGREDIENTS_CONFIRMED,
            )
        )
        queued = test_client.post(
            f"/api/v1/sessions/{confirmed.id}/recipe-options",
            json={"servings": 3, "option_count": 2},
        )
        assert queued.status_code == 202

        job_response = poll_job(test_client, queued.json()["job_id"])
        session_response = test_client.get(
            f"/api/v1/sessions/{confirmed.id}",
        )

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "succeeded"
    assert session_response.status_code == 200
    assert session_response.json()["stage"] == "options_ready"
    assert session_response.json()["option_batch_number"] == 1
    assert [item["name"] for item in session_response.json()["recipe_options"]] == [
        "Tomato Curry",
        "Tomato Soup",
    ]
    assert all(
        item["nutrition"] is not None
        for item in session_response.json()["recipe_options"]
    )
    assert job_response.json()["result"] == {
        "option_ids": [
            item["id"] for item in session_response.json()["recipe_options"]
        ],
        "batch_number": 1,
    }
    assert chef.calls == [
        (
            ["Tomato"],
            RecipePreferences(servings=3, option_count=2),
            set(),
        )
    ]
    assert [name for name, _ in nutrition.calls] == [
        "Tomato Curry",
        "Tomato Soup",
    ]


def test_more_crosses_domain_and_real_graph_with_all_canonical_shown_names(
    project_tmp_path: Path,
) -> None:
    chef = FakeMasterChef([[draft("Fresh Tomato Rice")]])
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        master_chef=chef,
        nutrition_agent=FakeNutritionAgent(),
    )

    with TestClient(application) as test_client:
        previous = test_client.portal.call(
            partial(
                store_session,
                application.state.session_store,
                stage=SessionStage.OPTIONS_READY,
                options=[
                    stored_option("Tomato Curry"),
                    stored_option("  Lentil   Soup  "),
                ],
                exclusions={"PASTA"},
                batch_number=1,
            )
        )
        queued = test_client.post(
            f"/api/v1/sessions/{previous.id}/recipe-options/more",
            json={"option_count": 1},
        )
        assert queued.status_code == 202
        job_response = poll_job(test_client, queued.json()["job_id"])
        session_response = test_client.get(
            f"/api/v1/sessions/{previous.id}",
        )

    assert chef.calls[0][2] == {
        "tomato curry",
        "lentil soup",
        "pasta",
    }
    assert session_response.json()["stage"] == "options_ready"
    assert session_response.json()["option_batch_number"] == 2
    assert [item["name"] for item in session_response.json()["recipe_options"]] == [
        "Tomato Curry",
        "  Lentil   Soup  ",
        "Fresh Tomato Rice",
    ]
    assert job_response.json()["status"] == "succeeded"
    assert job_response.json()["result"] == {
        "option_ids": [session_response.json()["recipe_options"][-1]["id"]],
        "batch_number": 2,
    }


def test_overlapping_jobs_report_their_own_committed_batch(
    project_tmp_path: Path,
) -> None:
    chef = FakeMasterChef(
        [
            [draft("Batch A Tomato Curry")],
            [draft("Batch B Tomato Rice")],
        ]
    )
    application = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path,
            max_concurrent_jobs=2,
        ),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        master_chef=chef,
        nutrition_agent=FakeNutritionAgent(),
    )
    delayed_runner = DelayedFirstCommittedResult(
        application.state.recipe_options_runner
    )
    application.state.recipe_options_runner = delayed_runner

    with TestClient(application) as test_client:
        confirmed = test_client.portal.call(
            partial(
                store_session,
                application.state.session_store,
                stage=SessionStage.INGREDIENTS_CONFIRMED,
            )
        )
        first_job = test_client.post(
            f"/api/v1/sessions/{confirmed.id}/recipe-options",
            json={"option_count": 1},
        )
        assert first_job.status_code == 202

        try:
            test_client.portal.call(delayed_runner.first_committed.wait)
            after_first = test_client.get(
                f"/api/v1/sessions/{confirmed.id}",
            ).json()
            first_option_id = after_first["recipe_options"][0]["id"]
            assert after_first["option_batch_number"] == 1

            second_job = test_client.post(
                f"/api/v1/sessions/{confirmed.id}/recipe-options/more",
                json={"option_count": 1},
            )
            assert second_job.status_code == 202
            second_job_response = poll_job(
                test_client,
                second_job.json()["job_id"],
            )
            after_second = test_client.get(
                f"/api/v1/sessions/{confirmed.id}",
            ).json()
            second_option_id = after_second["recipe_options"][1]["id"]
            assert second_job_response.json()["result"] == {
                "option_ids": [second_option_id],
                "batch_number": 2,
            }

            test_client.portal.call(delayed_runner.release_first.set)
            first_job_response = poll_job(
                test_client,
                first_job.json()["job_id"],
            )
        finally:
            test_client.portal.call(delayed_runner.release_first.set)

    assert first_job_response.json()["result"] == {
        "option_ids": [first_option_id],
        "batch_number": 1,
    }


def test_submit_failure_restores_complete_previous_option_context(
    project_tmp_path: Path,
) -> None:
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        master_chef=FakeMasterChef([[draft("Unused")]]),
        nutrition_agent=FakeNutritionAgent(),
    )
    application.dependency_overrides[get_job_runner] = FailingJobRunner

    with TestClient(application) as test_client:
        stored = test_client.portal.call(
            partial(
                store_session,
                application.state.session_store,
                stage=SessionStage.OPTIONS_READY,
                options=[stored_option("Tomato Curry")],
                exclusions={"tomato curry"},
                batch_number=7,
            )
        )
        previous = test_client.portal.call(
            application.state.session_store.replace,
            stored.model_copy(
                update={
                    "preferences": RecipePreferences(
                        dietary_preferences=["Vegan"],
                        preferred_cuisines=["Indian"],
                        servings=5,
                        option_count=1,
                    ),
                    "option_generation_id": "previous-generation",
                    "warnings": ["Preserve this warning."],
                    "updated_at": stored.updated_at,
                }
            ),
        )

        response = test_client.post(
            f"/api/v1/sessions/{previous.id}/recipe-options/more",
            json={"servings": 2, "option_count": 1},
        )
        saved = test_client.portal.call(
            application.state.session_store.require,
            previous.id,
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ollama_unavailable"
    assert response.json()["error"]["session_id"] == previous.id
    assert saved.stage is SessionStage.OPTIONS_READY
    assert saved.model_dump(exclude={"updated_at"}) == previous.model_dump(
        exclude={"updated_at"}
    )


@pytest.mark.asyncio
async def test_request_cancellation_drains_full_rollback_despite_repeated_cancel(
    project_tmp_path: Path,
) -> None:
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        master_chef=FakeMasterChef([[draft("Unused")]]),
        nutrition_agent=FakeNutritionAgent(),
    )
    session_store = PausingRollbackSessionStore()
    job_runner = BlockingJobRunner()
    previous = await store_session(
        session_store,
        stage=SessionStage.OPTIONS_READY,
        options=[stored_option("Tomato Curry")],
        exclusions={"tomato curry"},
        batch_number=3,
    )
    previous = await session_store.replace(
        previous.model_copy(
            update={
                "preferences": RecipePreferences(
                    dietary_preferences=["Vegetarian"],
                    servings=4,
                    option_count=1,
                ),
                "option_generation_id": "previous-generation",
                "warnings": ["Keep cancellation context."],
                "updated_at": previous.updated_at,
            }
        )
    )
    session_store.pause_rollbacks = True
    application.dependency_overrides[get_session_store] = lambda: session_store
    application.dependency_overrides[get_job_runner] = lambda: job_runner

    transport = ASGITransport(app=application)
    request_task: asyncio.Task | None = None
    rollback_wait: asyncio.Task | None = None
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        request_task = asyncio.create_task(
            async_client.post(
                f"/api/v1/sessions/{previous.id}/recipe-options/more",
                json={"servings": 2, "option_count": 1},
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
            generating = await session_store.require(previous.id)
            assert generating.stage is SessionStage.GENERATING_OPTIONS

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
            assert restored.stage is SessionStage.OPTIONS_READY
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


@pytest.mark.asyncio
async def test_submit_error_rollback_drains_when_request_is_repeatedly_cancelled(
    project_tmp_path: Path,
) -> None:
    application = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
        master_chef=FakeMasterChef([[draft("Unused")]]),
        nutrition_agent=FakeNutritionAgent(),
    )
    session_store = PausingRollbackSessionStore()
    previous = await store_session(
        session_store,
        stage=SessionStage.OPTIONS_READY,
        options=[stored_option("Tomato Curry")],
        exclusions={"tomato curry"},
        batch_number=5,
    )
    previous = await session_store.replace(
        previous.model_copy(
            update={
                "preferences": RecipePreferences(servings=6, option_count=1),
                "option_generation_id": "previous-generation",
                "warnings": ["Keep submit-error context."],
                "updated_at": previous.updated_at,
            }
        )
    )
    session_store.pause_rollbacks = True
    application.dependency_overrides[get_session_store] = lambda: session_store
    application.dependency_overrides[get_job_runner] = FailingJobRunner

    transport = ASGITransport(app=application)
    request_task: asyncio.Task | None = None
    rollback_wait: asyncio.Task | None = None
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        request_task = asyncio.create_task(
            async_client.post(
                f"/api/v1/sessions/{previous.id}/recipe-options/more",
                json={"servings": 2, "option_count": 1},
            )
        )
        rollback_wait = asyncio.create_task(session_store.rollback_started.wait())

        try:
            completed, _ = await asyncio.wait(
                {request_task, rollback_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert rollback_wait in completed
            assert not request_task.done()

            request_task.cancel()
            await asyncio.sleep(0)
            assert not request_task.done()
            request_task.cancel()
            await asyncio.sleep(0)
            assert not request_task.done()

            session_store.allow_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await request_task

            restored = await session_store.require(previous.id)
            assert restored.stage is SessionStage.OPTIONS_READY
            assert restored.model_dump(exclude={"updated_at"}) == previous.model_dump(
                exclude={"updated_at"}
            )
        finally:
            session_store.allow_rollback.set()
            if rollback_wait is not None and not rollback_wait.done():
                rollback_wait.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await rollback_wait
            if request_task is not None and not request_task.done():
                request_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request_task
