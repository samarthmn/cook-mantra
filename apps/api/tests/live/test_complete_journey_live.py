import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image, ImageDraw

from api.app import create_app
from core.config import PROJECT_ROOT, Settings
from domain.jobs import JobOperation, JobStatus
from domain.sessions import SessionStage
from schemas.jobs import JobResponse
from schemas.sessions import QueuedJobResponse, SessionResponse

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("COOK_MANTRA_RUN_LIVE") != "1",
        reason="Set COOK_MANTRA_RUN_LIVE=1 to run the complete live API journey.",
    ),
]

_JOB_TIMEOUT_SECONDS = 900.0
_POLL_INTERVAL_SECONDS = 0.25


@pytest.fixture
def live_workspace() -> Iterator[Path]:
    runtime_root = PROJECT_ROOT / "tmp"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="cook-mantra-live-journey-",
        dir=runtime_root,
    ) as directory:
        yield Path(directory)


def _write_ingredient_image(path: Path) -> None:
    image = Image.new("RGB", (640, 320), "#f8f4ea")
    drawing = ImageDraw.Draw(image)
    drawing.ellipse((70, 55, 275, 260), fill="#d94137", outline="#8b201b", width=5)
    drawing.ellipse(
        (365, 55, 570, 260),
        fill="#e7d5a5",
        outline="#886b34",
        width=5,
    )
    drawing.text((135, 275), "tomato", fill="#231f20")
    drawing.text((430, 275), "onion", fill="#231f20")
    image.save(path, format="PNG")


def _require_success(response: Response, expected_status: int = 200) -> object:
    assert response.status_code == expected_status, response.text
    return response.json()


def _wait_for_job(
    client: TestClient,
    job_id: str,
    operation: JobOperation,
) -> JobResponse:
    deadline = monotonic() + _JOB_TIMEOUT_SECONDS
    previous_progress = 0
    while monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        job = JobResponse.model_validate(_require_success(response))
        assert job.operation is operation
        assert job.progress >= previous_progress
        previous_progress = job.progress
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            assert job.status is JobStatus.SUCCEEDED, job.model_dump(mode="json")
            assert job.progress == 100
            assert job.result is not None
            return job
        sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"Job {job_id} did not finish within {_JOB_TIMEOUT_SECONDS:g} seconds."
    )


def test_complete_live_api_journey(live_workspace: Path) -> None:
    ingredient_path = live_workspace / "ingredients.png"
    downloaded_preview_path = live_workspace / "dish-preview.png"
    _write_ingredient_image(ingredient_path)

    settings = Settings(
        _env_file=None,
        artifact_root=live_workspace / "artifacts",
        image_width=256,
        image_height=256,
    )
    with TestClient(create_app(settings=settings)) as client:
        with ingredient_path.open("rb") as image:
            created_response = client.post(
                "/api/v1/sessions",
                files={"image": (ingredient_path.name, image, "image/png")},
            )
        created = QueuedJobResponse.model_validate(
            _require_success(created_response, 202)
        )
        _wait_for_job(
            client,
            created.job_id,
            JobOperation.EXTRACT_INGREDIENTS,
        )

        reviewing = SessionResponse.model_validate(
            _require_success(client.get(f"/api/v1/sessions/{created.session_id}"))
        )
        assert reviewing.stage is SessionStage.REVIEWING_INGREDIENTS
        assert reviewing.ingredients

        reviewed_payload = {
            "ingredients": [
                {
                    "id": ingredient.id,
                    "name": ingredient.name,
                    "confirmed": True,
                }
                for ingredient in reviewing.ingredients
            ]
        }
        reviewed = SessionResponse.model_validate(
            _require_success(
                client.put(
                    f"/api/v1/sessions/{created.session_id}/ingredients",
                    json=reviewed_payload,
                )
            )
        )
        assert reviewed.ingredients
        assert all(ingredient.confirmed for ingredient in reviewed.ingredients)

        confirmed = SessionResponse.model_validate(
            _require_success(
                client.post(
                    f"/api/v1/sessions/{created.session_id}/ingredients/confirm"
                )
            )
        )
        assert confirmed.stage is SessionStage.INGREDIENTS_CONFIRMED

        preferences = {
            "dietary_preferences": [],
            "allergens": [],
            "preferred_cuisines": ["Indian"],
            "max_total_minutes": 45,
            "servings": 2,
            "option_count": 1,
        }
        options_queued = QueuedJobResponse.model_validate(
            _require_success(
                client.post(
                    f"/api/v1/sessions/{created.session_id}/recipe-options",
                    json=preferences,
                ),
                202,
            )
        )
        _wait_for_job(
            client,
            options_queued.job_id,
            JobOperation.GENERATE_OPTIONS,
        )

        options_ready = SessionResponse.model_validate(
            _require_success(client.get(f"/api/v1/sessions/{created.session_id}"))
        )
        assert options_ready.stage is SessionStage.OPTIONS_READY
        assert len(options_ready.recipe_options) == 1
        option = options_ready.recipe_options[0]
        assert option.nutrition is not None
        assert option.preview is not None

        preview_response = client.get(f"/api/v1/artifacts/{option.preview.artifact_id}")
        assert preview_response.status_code == 200
        assert preview_response.headers["content-type"] in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }
        downloaded_preview_path.write_bytes(preview_response.content)
        with Image.open(downloaded_preview_path) as preview:
            preview.verify()

        recipes_queued = QueuedJobResponse.model_validate(
            _require_success(
                client.post(
                    f"/api/v1/sessions/{created.session_id}/recipes",
                    json={"option_ids": [option.id]},
                ),
                202,
            )
        )
        _wait_for_job(
            client,
            recipes_queued.job_id,
            JobOperation.GENERATE_RECIPES,
        )

        completed = SessionResponse.model_validate(
            _require_success(client.get(f"/api/v1/sessions/{created.session_id}"))
        )
        assert completed.stage is SessionStage.RECIPES_READY
        recipe = completed.complete_recipes[option.id]
        assert recipe.option_id == option.id
        assert recipe.ingredients
        assert [step.number for step in recipe.steps] == list(
            range(1, len(recipe.steps) + 1)
        )

    assert ingredient_path.exists()
    assert downloaded_preview_path.exists()
