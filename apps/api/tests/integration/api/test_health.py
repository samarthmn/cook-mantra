from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.ingredient_extraction import OllamaIngredientExtractor
from agents.master_chef import OllamaMasterChef
from agents.nutrition import OllamaNutritionAgent
from agents.specialized_recipe import OllamaSpecializedRecipeAgent
from api.app import create_app
from core.config import Settings
from services.ollama_health import OllamaHealthService
from tests.tracing_support import enabled_tracing


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": ["qwen3.5:9b"],
            "missing": [],
        }


class ExplodingOllama:
    async def inspect(self) -> dict[str, object]:
        raise AssertionError("The process health route must not inspect Ollama.")


class MissingModelOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": ["qwen3.5:9b"],
            "missing": ["qwen3.5:27b"],
        }


class FalseyTracingService:
    def __bool__(self) -> bool:
        return False


class RecordingArtifactStore:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._shutdown_error = shutdown_error

    async def startup(self) -> None:
        self._events.append("artifact.startup")

    async def shutdown(self) -> None:
        self._events.append("artifact.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error


class RecordingJobRunner:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._shutdown_error = shutdown_error

    async def shutdown(self) -> None:
        self._events.append("runner.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error


class RecordingCleanupSupervisor:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._shutdown_error = shutdown_error

    async def startup(self) -> None:
        self._events.append("cleanup.startup")

    async def shutdown(self) -> None:
        self._events.append("cleanup.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error


def settings_for(artifact_root: Path) -> Settings:
    return Settings(_env_file=None, artifact_root=artifact_root)


def test_health_is_process_only(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ExplodingOllama(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_shares_one_tracing_service_across_all_real_agents(
    project_tmp_path: Path,
) -> None:
    tracing, _ = enabled_tracing()

    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        tracing_service=tracing,
    )

    assert app.state.tracing_service is tracing
    assert isinstance(app.state.ingredient_extractor, OllamaIngredientExtractor)
    assert app.state.ingredient_extractor._tracing is tracing
    assert isinstance(
        app.state.recipe_options_dependencies.master_chef,
        OllamaMasterChef,
    )
    assert app.state.recipe_options_dependencies.master_chef._tracing is tracing
    assert isinstance(
        app.state.recipe_options_dependencies.nutrition_agent,
        OllamaNutritionAgent,
    )
    assert app.state.recipe_options_dependencies.nutrition_agent._tracing is tracing
    assert isinstance(
        app.state.complete_recipes_dependencies.agent,
        OllamaSpecializedRecipeAgent,
    )
    assert app.state.complete_recipes_dependencies.agent._tracing is tracing


def test_app_preserves_a_falsey_injected_tracing_double(
    project_tmp_path: Path,
) -> None:
    tracing = FalseyTracingService()

    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        tracing_service=tracing,
    )

    assert app.state.tracing_service is tracing
    assert app.state.ingredient_extractor._tracing is tracing


def test_ready_reports_model_inventory(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ollama": {
            "reachable": True,
            "available_models": ["qwen3.5:9b"],
            "missing": [],
        },
    }


def test_ready_maps_missing_models_to_the_public_error(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=MissingModelOllama(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ready",
            headers={"X-Request-ID": "req-missing-model"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "model_not_found",
            "message": "Required Ollama models are not available.",
            "details": {"missing_models": ["qwen3.5:27b"]},
            "retryable": False,
            "request_id": "req-missing-model",
            "session_id": None,
            "job_id": None,
        }
    }


def test_ready_hides_ollama_connection_details(project_tmp_path: Path) -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "connection refused at private-host:11434",
            request=request,
        )

    ollama_health = OllamaHealthService(
        "http://ollama.local:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(fail),
    )
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ollama_health,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ready",
            headers={"X-Request-ID": "req-ollama-down"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "ollama_unavailable",
            "message": "Ollama is unavailable.",
            "details": {},
            "retryable": True,
            "request_id": "req-ollama-down",
            "session_id": None,
            "job_id": None,
        }
    }
    assert "private-host" not in response.text


def test_lifespan_releases_artifacts_when_job_shutdown_fails(
    project_tmp_path: Path,
) -> None:
    events: list[str] = []
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )
    app.state.artifact_store = RecordingArtifactStore(events)
    app.state.job_runner = RecordingJobRunner(
        events,
        shutdown_error=RuntimeError("runner shutdown failed"),
    )
    app.state.cleanup_supervisor = RecordingCleanupSupervisor(events)

    with (
        pytest.raises(RuntimeError, match="runner shutdown failed"),
        TestClient(app),
    ):
        pass

    assert events == [
        "artifact.startup",
        "cleanup.startup",
        "cleanup.shutdown",
        "runner.shutdown",
        "artifact.shutdown",
    ]


def test_lifespan_releases_runtime_resources_when_cleanup_shutdown_fails(
    project_tmp_path: Path,
) -> None:
    events: list[str] = []
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )
    app.state.artifact_store = RecordingArtifactStore(events)
    app.state.job_runner = RecordingJobRunner(events)
    app.state.cleanup_supervisor = RecordingCleanupSupervisor(
        events,
        shutdown_error=RuntimeError("cleanup shutdown failed"),
    )

    with (
        pytest.raises(RuntimeError, match="cleanup shutdown failed"),
        TestClient(app),
    ):
        pass

    assert events == [
        "artifact.startup",
        "cleanup.startup",
        "cleanup.shutdown",
        "runner.shutdown",
        "artifact.shutdown",
    ]
