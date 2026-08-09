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
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
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


def test_browser_upload_preflight_allows_the_runtime_revision_header(
    client: TestClient,
) -> None:
    response = client.options(
        "/api/v1/sessions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "Content-Type, X-Cook-Mantra-Runtime-Revision"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ("http://localhost:3000")
    assert (
        "x-cook-mantra-runtime-revision"
        in response.headers["access-control-allow-headers"].lower()
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost:3000.evil.example",
        "null",
        "not-an-origin",
        "http://localhost:3900",
        "http://192.168.0.221:3000",
    ],
)
def test_unconfigured_origins_receive_no_allow_origin_header(
    client: TestClient,
    origin: str,
) -> None:
    response = client.get("/api/v1/health", headers={"Origin": origin})

    assert "access-control-allow-origin" not in response.headers
