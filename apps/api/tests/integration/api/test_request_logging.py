import json
import logging
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import RequestIdMiddleware
from core.errors import install_error_handlers
from core.logging import configure_logging


def _app() -> FastAPI:
    configure_logging("INFO")
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/validated/{value}")
    async def validated(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("provider_secret=canary-provider-secret")

    @app.post("/upload")
    async def upload(payload: dict[str, object]) -> dict[str, bool]:
        return {"ok": bool(payload)}

    return app


def _completion_records(
    capsys: pytest.CaptureFixture[str],
) -> list[dict[str, object]]:
    return [
        record
        for record in (
            json.loads(line) for line in capsys.readouterr().err.splitlines()
        )
        if record["event"] == "request_completed"
    ]


@pytest.mark.parametrize(
    ("path", "status_code"),
    [
        ("/ok", 200),
        ("/validated/not-an-integer", 422),
        ("/unexpected", 500),
    ],
)
def test_request_completion_is_logged_once_for_every_response(
    capsys: pytest.CaptureFixture[str],
    path: str,
    status_code: int,
) -> None:
    response = TestClient(_app(), raise_server_exceptions=False).get(
        path,
        headers={"X-Request-ID": "request-1"},
    )

    records = _completion_records(capsys)
    assert response.status_code == status_code
    assert response.headers["X-Request-ID"] == "request-1"
    assert records == [
        {
            "event": "request_completed",
            "request_id": "request-1",
            "method": "GET",
            "path": path,
            "status_code": status_code,
            "duration_ms": records[0]["duration_ms"],
        }
    ]
    assert records[0]["duration_ms"] >= 0


def test_request_id_is_generated_and_matches_the_response(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = TestClient(_app()).get("/ok")

    record = _completion_records(capsys)[0]
    assert UUID(response.headers["X-Request-ID"]).version == 4
    assert record["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize("unsafe_request_id", ["request\tinjected", "a" * 129])
def test_unsafe_caller_request_id_is_replaced(
    capsys: pytest.CaptureFixture[str],
    unsafe_request_id: str,
) -> None:
    response = TestClient(_app()).get(
        "/ok",
        headers={"X-Request-ID": unsafe_request_id},
    )

    record = _completion_records(capsys)[0]
    assert UUID(response.headers["X-Request-ID"]).version == 4
    assert record["request_id"] == response.headers["X-Request-ID"]
    assert unsafe_request_id not in response.text


def test_request_body_and_binary_canaries_never_enter_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = TestClient(_app()).post(
        "/upload",
        headers={"X-Request-ID": "request-body"},
        json={
            "password": "canary-password",
            "image": "data:image/png;base64,Y2FuYXJ5LWltYWdl",
        },
    )

    output = capsys.readouterr().err
    assert response.status_code == 200
    assert "canary" not in output
    assert "password" not in output
    assert "base64" not in output
    assert json.loads(output)["path"] == "/upload"


def test_repeated_app_construction_does_not_duplicate_request_records(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _app()
    response = TestClient(_app()).get(
        "/ok",
        headers={"X-Request-ID": "request-once"},
    )

    records = _completion_records(capsys)
    assert response.status_code == 200
    assert len(records) == 1


def test_unexpected_request_error_does_not_attach_an_unsafe_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app()

    with caplog.at_level(logging.ERROR, logger="core.errors"):
        response = TestClient(app, raise_server_exceptions=False).get(
            "/unexpected",
            headers={"X-Request-ID": "request-safe-error"},
        )

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "request_internal_failure"
    )
    assert response.status_code == 500
    assert failure.error_code == "internal_error"
    assert failure.exception_type == "RuntimeError"
    assert failure.exc_info is None
