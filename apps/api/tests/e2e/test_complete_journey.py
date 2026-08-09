"""Network-free proof of the complete public API journey."""

from collections.abc import Iterator
from itertools import count
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from tests.e2e.fakes import (
    PNG_BYTES,
    DeterministicImageGenerator,
    DeterministicIngredientExtractor,
    DeterministicMasterChef,
    DeterministicSpecializedRecipeAgent,
)
from tests.runtime_support import (
    INSTALLED_REQUIRED_MODELS,
    image_enabled_runtime_snapshot,
)

from api.app import create_app
from core.config import Settings

_REQUEST_SEQUENCE = count(1)
pytestmark = pytest.mark.e2e


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": INSTALLED_REQUIRED_MODELS,
            "missing": [],
        }


def _make_e2e_app(
    artifact_root: Path,
    image_generator: DeterministicImageGenerator,
):
    return create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=artifact_root,
            max_concurrent_jobs=2,
            max_concurrent_model_calls=2,
        ),
        ingredient_extractor=DeterministicIngredientExtractor(),
        master_chef=DeterministicMasterChef(),
        image_generator=image_generator,
        image_runtimes={},
        specialized_recipe_agent=DeterministicSpecializedRecipeAgent(),
        ollama_health=ReadyOllama(),
        runtime_snapshot=image_enabled_runtime_snapshot(),
    )


@pytest.fixture
def ingredient_png() -> bytes:
    return PNG_BYTES


@pytest.fixture
def deterministic_image_generator() -> DeterministicImageGenerator:
    return DeterministicImageGenerator()


@pytest.fixture
def e2e_client(
    project_tmp_path: Path,
    deterministic_image_generator: DeterministicImageGenerator,
) -> Iterator[TestClient]:
    app = _make_e2e_app(
        project_tmp_path / "e2e-artifacts",
        deterministic_image_generator,
    )
    with TestClient(app) as client:
        yield client


def _request(
    client: TestClient,
    method: str,
    path: str,
    **kwargs: object,
) -> Response:
    request_id = f"e2e-request-{next(_REQUEST_SEQUENCE)}"
    headers = {"X-Request-ID": request_id, **kwargs.pop("headers", {})}
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.headers["X-Request-ID"] == request_id
    return response


def _ok(response: Response, expected_status: int = 200) -> dict[str, object]:
    assert response.status_code == expected_status, response.text
    return response.json()


def _wait_for_job(
    client: TestClient,
    job_id: str,
    *,
    operation: str,
    timeout_seconds: float = 3,
) -> dict[str, object]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = _request(client, "GET", f"/api/v1/jobs/{job_id}")
        job = _ok(response)
        if job["status"] in {"succeeded", "failed"}:
            assert job["operation"] == operation
            assert job["status"] == "succeeded"
            assert job["progress"] == 100
            assert job["error"] is None
            return job
        sleep(0.005)
    raise AssertionError(f"Job {job_id} did not finish within {timeout_seconds}s.")


def test_previous_process_revision_rejects_browser_upload_without_state(
    project_tmp_path: Path,
    ingredient_png: bytes,
) -> None:
    process_a = _make_e2e_app(
        project_tmp_path / "process-a-artifacts",
        DeterministicImageGenerator(),
    )
    process_b = _make_e2e_app(
        project_tmp_path / "process-b-artifacts",
        DeterministicImageGenerator(),
    )

    with TestClient(process_a) as client_a, TestClient(process_b) as client_b:
        stale_revision = _ok(_request(client_a, "GET", "/api/v1/runtime-status"))[
            "runtime_revision"
        ]
        assert stale_revision != process_b.state.runtime_revision
        response = _request(
            client_b,
            "POST",
            "/api/v1/sessions",
            files={"image": ("ingredients.png", ingredient_png, "image/png")},
            headers={
                "Origin": "http://localhost:3000",
                "X-Cook-Mantra-Runtime-Revision": stale_revision,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runtime_status_stale"
    assert process_b.state.session_store._sessions == {}
    assert process_b.state.job_store._jobs == {}


def test_complete_recipe_journey(
    e2e_client: TestClient,
    ingredient_png: bytes,
    deterministic_image_generator: DeterministicImageGenerator,
) -> None:
    created = _ok(
        _request(
            e2e_client,
            "POST",
            "/api/v1/sessions",
            files={"image": ("ingredients.png", ingredient_png, "image/png")},
        ),
        202,
    )
    session_id = created["session_id"]
    extraction_job = _wait_for_job(
        e2e_client,
        created["job_id"],
        operation="extract_ingredients",
    )
    assert extraction_job["result"]["session_id"] == session_id
    assert extraction_job["result"]["stage"] == "reviewing_ingredients"

    reviewing = _ok(_request(e2e_client, "GET", f"/api/v1/sessions/{session_id}"))
    assert reviewing["stage"] == "reviewing_ingredients"
    assert reviewing["warnings"] == [
        "Review every detected ingredient before continuing."
    ]
    reviewed_ingredients = []
    for ingredient in reviewing["ingredients"]:
        name = (
            "Potato (not selected)"
            if ingredient["name"] == "Potato"
            else ingredient["name"]
        )
        reviewed_ingredients.append(
            {
                "id": ingredient["id"],
                "name": name,
                "confirmed": name in {"Tomato", "Onion"},
            }
        )

    reviewed = _ok(
        _request(
            e2e_client,
            "PUT",
            f"/api/v1/sessions/{session_id}/ingredients",
            json={"ingredients": reviewed_ingredients},
        )
    )
    assert reviewed["stage"] == "reviewing_ingredients"
    assert (
        next(
            ingredient
            for ingredient in reviewed["ingredients"]
            if ingredient["name"] == "Potato (not selected)"
        )["confirmed"]
        is False
    )

    confirmed = _ok(
        _request(
            e2e_client,
            "POST",
            f"/api/v1/sessions/{session_id}/ingredients/confirm",
        )
    )
    assert confirmed["stage"] == "ingredients_confirmed"
    assert [
        ingredient["name"]
        for ingredient in confirmed["ingredients"]
        if ingredient["confirmed"]
    ] == ["Tomato", "Onion"]

    preferences = {
        "dietary_preferences": ["vegetarian"],
        "allergens": ["peanut"],
        "preferred_cuisines": ["Indian"],
        "max_total_minutes": 45,
        "servings": 2,
        "option_count": 2,
    }
    first_queued = _ok(
        _request(
            e2e_client,
            "POST",
            f"/api/v1/sessions/{session_id}/recipe-options",
            json=preferences,
        ),
        202,
    )
    first_job = _wait_for_job(
        e2e_client,
        first_queued["job_id"],
        operation="generate_options",
    )
    assert first_job["result"]["batch_number"] == 1
    assert len(first_job["result"]["option_ids"]) == 2
    assert set(first_job["result"]) == {"batch_number", "option_ids"}

    first_ready = _ok(_request(e2e_client, "GET", f"/api/v1/sessions/{session_id}"))
    assert first_ready["stage"] == "options_ready"
    assert first_ready["option_batch_number"] == 1
    first_options = first_ready["recipe_options"]
    assert [option["name"] for option in first_options] == [
        "Tomato Onion Curry",
        "Tomato Onion Soup",
    ]
    assert all(option["nutrition"] is None for option in first_options)
    assert all("preview" not in option for option in first_options)
    assert all("warnings" not in option for option in first_options)
    assert deterministic_image_generator.requests == []

    more_queued = _ok(
        _request(
            e2e_client,
            "POST",
            f"/api/v1/sessions/{session_id}/recipe-options/more",
            json=preferences,
        ),
        202,
    )
    more_job = _wait_for_job(
        e2e_client,
        more_queued["job_id"],
        operation="generate_more_options",
    )
    assert more_job["result"]["batch_number"] == 2
    assert len(more_job["result"]["option_ids"]) == 2
    assert set(more_job["result"]) == {"batch_number", "option_ids"}

    all_ready = _ok(_request(e2e_client, "GET", f"/api/v1/sessions/{session_id}"))
    all_options = all_ready["recipe_options"]
    assert all_ready["stage"] == "options_ready"
    assert all_ready["option_batch_number"] == 2
    assert len({option["name"].casefold() for option in all_options}) == 4
    assert [option["id"] for option in all_options[:2]] == [
        option["id"] for option in first_options
    ]
    assert all("preview" not in option for option in all_options)
    assert all("warnings" not in option for option in all_options)

    selected_ids = [option["id"] for option in first_options]
    recipes_queued = _ok(
        _request(
            e2e_client,
            "POST",
            f"/api/v1/sessions/{session_id}/recipes",
            json={"option_ids": selected_ids},
        ),
        202,
    )
    recipe_job = _wait_for_job(
        e2e_client,
        recipes_queued["job_id"],
        operation="generate_recipes",
    )
    assert recipe_job["result"] == {
        "selected_option_ids": selected_ids,
        "recipe_option_ids": [selected_ids[0]],
        "failed_option_ids": [selected_ids[1]],
    }

    completed = _ok(_request(e2e_client, "GET", f"/api/v1/sessions/{session_id}"))
    assert completed["stage"] == "recipes_ready"
    assert [option["id"] for option in completed["recipe_options"]] == [
        option["id"] for option in all_options
    ]
    assert all("preview" not in option for option in completed["recipe_options"])
    assert all("warnings" not in option for option in completed["recipe_options"])
    assert completed["complete_recipes"].keys() == {selected_ids[0]}
    assert completed["recipe_failures"][selected_ids[1]] == {
        "option_id": selected_ids[1],
        "code": "model_output_invalid",
        "message": "The soup recipe could not be generated.",
        "retryable": True,
    }

    recipe = completed["complete_recipes"][selected_ids[0]]
    assert recipe["option_id"] == selected_ids[0]
    preview_metadata = recipe["preview"]
    assert preview_metadata is not None
    assert preview_metadata == {
        "artifact_id": preview_metadata["artifact_id"],
        "label": "AI-generated image",
    }
    assert "Dish preview unavailable." not in recipe["warnings"]
    assert len(deterministic_image_generator.requests) == 1
    assert "Create an appetizing cooked-dish illustration." in (
        deterministic_image_generator.requests[0].prompt
    )
    preview = _request(
        e2e_client,
        "GET",
        f"/api/v1/artifacts/{preview_metadata['artifact_id']}",
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["cache-control"] == "no-store"
    assert preview.content == PNG_BYTES
    assert [ingredient["name"] for ingredient in recipe["ingredients"]] == [
        "Tomato",
        "Onion",
        "Rice",
        "Cilantro",
    ]
    assert [ingredient["availability"] for ingredient in recipe["ingredients"]] == [
        "available",
        "available",
        "missing",
        "optional",
    ]
    assert [step["number"] for step in recipe["steps"]] == [1, 2]
