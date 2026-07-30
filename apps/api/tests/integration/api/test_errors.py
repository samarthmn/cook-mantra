from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import RequestIdMiddleware
from core.errors import AppError, ErrorCode, install_error_handlers


def create_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    return app


def test_app_error_uses_stable_public_shape() -> None:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise AppError(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Session was not found.",
            status_code=404,
            retryable=False,
            session_id="session-1",
        )

    response = TestClient(app).get("/boom", headers={"X-Request-ID": "req-1"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "req-1"
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "Session was not found.",
            "details": {},
            "retryable": False,
            "request_id": "req-1",
            "session_id": "session-1",
            "job_id": None,
        }
    }


def test_request_id_is_generated_when_the_caller_does_not_supply_one() -> None:
    app = create_app()

    @app.get("/ok")
    def ok() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get("/ok")

    assert response.status_code == 200
    assert UUID(response.headers["X-Request-ID"]).version == 4


def test_validation_errors_use_the_invalid_request_contract() -> None:
    app = create_app()

    @app.get("/recipes/{recipe_id}")
    def recipe(recipe_id: int) -> dict[str, int]:
        return {"recipe_id": recipe_id}

    response = TestClient(app).get(
        "/recipes/not-a-number", headers={"X-Request-ID": "req-validation"}
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request is invalid.",
            "details": {},
            "retryable": False,
            "request_id": "req-validation",
            "session_id": None,
            "job_id": None,
        }
    }


def test_framework_not_found_uses_the_resource_not_found_contract() -> None:
    response = TestClient(create_app()).get(
        "/missing-route",
        headers={"X-Request-ID": "req-framework-not-found"},
    )

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "req-framework-not-found"
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "Resource was not found.",
            "details": {},
            "retryable": False,
            "request_id": "req-framework-not-found",
            "session_id": None,
            "job_id": None,
        }
    }


def test_framework_method_not_allowed_uses_the_invalid_request_contract() -> None:
    app = create_app()

    @app.get("/read-only")
    def read_only() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).post(
        "/read-only",
        headers={"X-Request-ID": "req-method-not-allowed"},
    )

    assert response.status_code == 405
    assert response.headers["X-Request-ID"] == "req-method-not-allowed"
    assert response.headers["allow"] == "GET"
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request is invalid.",
            "details": {},
            "retryable": False,
            "request_id": "req-method-not-allowed",
            "session_id": None,
            "job_id": None,
        }
    }


def test_unexpected_errors_do_not_expose_exception_details() -> None:
    app = create_app()

    @app.get("/unexpected")
    def unexpected() -> None:
        raise RuntimeError("sensitive implementation detail")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/unexpected", headers={"X-Request-ID": "req-unexpected"}
    )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-unexpected"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "details": {},
            "retryable": False,
            "request_id": "req-unexpected",
            "session_id": None,
            "job_id": None,
        }
    }
