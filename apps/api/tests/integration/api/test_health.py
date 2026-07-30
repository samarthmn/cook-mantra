from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.config import Settings
from services.ollama_health import OllamaHealthService


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


def settings_for(artifact_root: Path) -> Settings:
    return Settings(_env_file=None, artifact_root=artifact_root)


def test_health_is_process_only(tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(tmp_path),
        ollama_health=ExplodingOllama(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_model_inventory(tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(tmp_path),
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


def test_ready_maps_missing_models_to_the_public_error(tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(tmp_path),
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


def test_ready_hides_ollama_connection_details(tmp_path: Path) -> None:
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
        settings=settings_for(tmp_path),
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


def test_lifespan_releases_artifacts_when_job_shutdown_fails(tmp_path: Path) -> None:
    events: list[str] = []
    app = create_app(
        settings=settings_for(tmp_path),
        ollama_health=ReadyOllama(),
    )
    app.state.artifact_store = RecordingArtifactStore(events)
    app.state.job_runner = RecordingJobRunner(
        events,
        shutdown_error=RuntimeError("runner shutdown failed"),
    )

    with (
        pytest.raises(RuntimeError, match="runner shutdown failed"),
        TestClient(app),
    ):
        pass

    assert events == ["artifact.startup", "runner.shutdown", "artifact.shutdown"]
