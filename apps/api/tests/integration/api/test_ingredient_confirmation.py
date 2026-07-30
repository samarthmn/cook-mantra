from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import create_app
from core.config import Settings
from domain.ingredients import ExtractionResult, Ingredient, IngredientSource
from domain.sessions import Session, SessionStage
from repositories.session_store import SessionStore


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": [],
            "missing": [],
        }


class UnusedIngredientExtractor:
    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        raise AssertionError("Ingredient extraction is not used by these tests.")


@pytest.fixture
def app(project_tmp_path: Path) -> FastAPI:
    return create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=ReadyOllama(),
        ingredient_extractor=UnusedIngredientExtractor(),
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


async def _store_review_session(
    store: SessionStore,
    *,
    stage: SessionStage = SessionStage.REVIEWING_INGREDIENTS,
    selected: bool = False,
) -> Session:
    created = await store.create()
    return await store.replace(
        created.model_copy(
            update={
                "stage": stage,
                "ingredients": [
                    Ingredient(
                        id="detected-tomato",
                        name="Tomato",
                        source=IngredientSource.DETECTED,
                        confidence=0.91,
                        confirmed=selected,
                    ),
                    Ingredient(
                        id="pantry-salt",
                        name="Salt",
                        source=IngredientSource.PANTRY_SUGGESTION,
                    ),
                ],
            }
        )
    )


@pytest.fixture
def review_session(client: TestClient) -> Session:
    return client.portal.call(
        _store_review_session,
        client.app.state.session_store,
    )


@pytest.fixture
def selected_review_session(client: TestClient) -> Session:
    return client.portal.call(
        partial(
            _store_review_session,
            client.app.state.session_store,
            selected=True,
        )
    )


def test_review_can_rename_select_remove_and_add(
    client: TestClient,
    review_session: Session,
) -> None:
    response = client.put(
        f"/api/v1/sessions/{review_session.id}/ingredients",
        json={
            "ingredients": [
                {
                    "id": review_session.ingredients[0].id,
                    "name": "Cherry tomato",
                    "confirmed": True,
                },
                {"name": "Spinach", "confirmed": True},
            ]
        },
    )

    assert response.status_code == 200
    ingredients = response.json()["ingredients"]
    assert [item["name"] for item in ingredients] == ["Cherry tomato", "Spinach"]
    assert ingredients[0]["source"] == "detected"
    assert ingredients[0]["confidence"] == 0.91
    assert ingredients[1]["source"] == "user_added"
    assert ingredients[1]["confidence"] is None
    assert response.json() == client.get(f"/api/v1/sessions/{review_session.id}").json()
    assert [item.name for item in review_session.ingredients] == ["Tomato", "Salt"]


def test_confirm_returns_confirmed_session(
    client: TestClient,
    selected_review_session: Session,
) -> None:
    response = client.post(
        f"/api/v1/sessions/{selected_review_session.id}/ingredients/confirm"
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "ingredients_confirmed"
    assert (
        response.json()
        == client.get(f"/api/v1/sessions/{selected_review_session.id}").json()
    )
    assert selected_review_session.stage is SessionStage.REVIEWING_INGREDIENTS


def test_confirmation_requires_a_selected_ingredient(
    client: TestClient,
    review_session: Session,
) -> None:
    response = client.post(f"/api/v1/sessions/{review_session.id}/ingredients/confirm")

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid_request",
        "message": "Select at least one ingredient before confirmation.",
        "details": {},
        "retryable": False,
        "request_id": response.headers["X-Request-ID"],
        "session_id": review_session.id,
        "job_id": None,
    }


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("PUT", "/api/v1/sessions/missing-session/ingredients", {"ingredients": []}),
        (
            "POST",
            "/api/v1/sessions/missing-session/ingredients/confirm",
            None,
        ),
    ],
)
def test_ingredient_routes_use_the_standard_unknown_session_error(
    client: TestClient,
    method: str,
    path: str,
    json: dict[str, object] | None,
) -> None:
    response = client.request(method, path, json=json)

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
    "stage",
    [SessionStage.EXTRACTING, SessionStage.INGREDIENTS_CONFIRMED],
)
def test_review_is_rejected_outside_ingredient_review(
    client: TestClient,
    stage: SessionStage,
) -> None:
    session = client.portal.call(
        partial(
            _store_review_session,
            client.app.state.session_store,
            stage=stage,
        )
    )

    response = client.put(
        f"/api/v1/sessions/{session.id}/ingredients",
        json={"ingredients": [{"name": "Spinach", "confirmed": True}]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_session_transition"
    assert response.json()["error"]["message"] == (
        "Session must be reviewing_ingredients for this operation."
    )
    assert response.json()["error"]["session_id"] == session.id


@pytest.mark.parametrize(
    "stage",
    [SessionStage.EXTRACTING, SessionStage.INGREDIENTS_CONFIRMED],
)
def test_confirmation_is_rejected_outside_ingredient_review(
    client: TestClient,
    stage: SessionStage,
) -> None:
    session = client.portal.call(
        partial(
            _store_review_session,
            client.app.state.session_store,
            stage=stage,
            selected=True,
        )
    )

    response = client.post(f"/api/v1/sessions/{session.id}/ingredients/confirm")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_session_transition"
    assert response.json()["error"]["message"] == (
        "Session must be reviewing_ingredients for this operation."
    )
    assert response.json()["error"]["session_id"] == session.id


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", "detected"), ("confidence", 0.99)],
)
def test_review_rejects_server_owned_fields(
    client: TestClient,
    review_session: Session,
    field: str,
    value: str | float,
) -> None:
    response = client.put(
        f"/api/v1/sessions/{review_session.id}/ingredients",
        json={
            "ingredients": [
                {
                    "id": review_session.ingredients[0].id,
                    "name": "Cherry tomato",
                    "confirmed": True,
                    field: value,
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert (
        client.get(f"/api/v1/sessions/{review_session.id}").json()["ingredients"][0][
            "name"
        ]
        == "Tomato"
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("put", "/api/v1/sessions/{session_id}/ingredients"),
        ("post", "/api/v1/sessions/{session_id}/ingredients/confirm"),
    ],
)
def test_ingredient_route_response_examples_show_every_source(
    client: TestClient,
    method: str,
    path: str,
) -> None:
    operation = client.app.openapi()["paths"][path][method]
    examples = operation["responses"]["200"]["content"]["application/json"]["examples"]

    for example in examples.values():
        sources = {
            ingredient["source"] for ingredient in example["value"]["ingredients"]
        }
        assert sources == {"detected", "pantry_suggestion", "user_added"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("put", "/api/v1/sessions/{session_id}/ingredients"),
        ("post", "/api/v1/sessions/{session_id}/ingredients/confirm"),
    ],
)
def test_ingredient_route_openapi_errors_use_the_public_envelope(
    client: TestClient,
    method: str,
    path: str,
) -> None:
    responses = client.app.openapi()["paths"][path][method]["responses"]
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
