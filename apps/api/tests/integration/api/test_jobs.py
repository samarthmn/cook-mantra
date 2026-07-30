from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from core.config import Settings
from domain.jobs import JobOperation


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": [],
            "missing": [],
        }


def settings_for(artifact_root: Path) -> Settings:
    return Settings(_env_file=None, artifact_root=artifact_root)


def test_job_can_be_read_through_its_public_schema(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )

    with TestClient(app) as client:
        job = client.portal.call(
            app.state.job_store.create,
            JobOperation.EXTRACT_INGREDIENTS,
            "session-1",
        )
        response = client.get(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": job.id,
        "operation": "extract_ingredients",
        "session_id": "session-1",
        "status": "queued",
        "progress": 0,
        "result": None,
        "warnings": [],
        "error": None,
        "created_at": job.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": job.updated_at.isoformat().replace("+00:00", "Z"),
    }


def test_unknown_job_uses_standard_not_found_error(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/jobs/missing-job",
            headers={"X-Request-ID": "req-missing-job"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "Job was not found.",
            "details": {},
            "retryable": False,
            "request_id": "req-missing-job",
            "session_id": None,
            "job_id": "missing-job",
        }
    }
