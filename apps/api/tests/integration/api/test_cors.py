from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.config import Settings


@pytest.fixture
def client(project_tmp_path: Path) -> TestClient:
    app = create_app(
        Settings(_env_file=None, artifact_root=project_tmp_path),
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "http://127.0.0.1:3000"],
)
def test_local_frontend_origins_receive_cors_headers(
    client: TestClient,
    origin: str,
) -> None:
    preflight = client.options(
        "/api/v1/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Request-ID, Content-Type",
        },
    )
    simple = client.get("/api/v1/health", headers={"Origin": origin})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "GET" in preflight.headers["access-control-allow-methods"]
    assert "x-request-id" in preflight.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in preflight.headers
    assert simple.headers["access-control-allow-origin"] == origin
    assert simple.headers["access-control-expose-headers"] == "X-Request-ID"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost:3000.evil.example",
        "null",
        "not-an-origin",
        "http://127.0.0.1:3001",
    ],
)
def test_unconfigured_origins_receive_no_allow_origin_header(
    client: TestClient,
    origin: str,
) -> None:
    response = client.get("/api/v1/health", headers={"Origin": origin})

    assert "access-control-allow-origin" not in response.headers
