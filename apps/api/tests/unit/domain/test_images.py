from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from core.config import Settings
from domain.images import DishPreview, GeneratedImage, ImageGenerationRequest
from domain.recipe_options import Difficulty, RecipeOption
from schemas.recipe_options import RecipeOptionResponse


def test_image_settings_have_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.image_width == 768
    assert settings.image_height == 768
    assert settings.image_steps is None
    assert settings.image_timeout_seconds == 600.0


@pytest.mark.parametrize("field_name", ["image_width", "image_height"])
@pytest.mark.parametrize("value", [255, 2_049])
def test_image_settings_reject_dimensions_outside_supported_range(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


@pytest.mark.parametrize("value", [0, 101])
def test_image_settings_reject_steps_outside_supported_range(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, image_steps=value)


def test_image_settings_reject_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, image_timeout_seconds=0)


def test_preview_is_always_labeled_as_illustration() -> None:
    preview = DishPreview(artifact_id="artifact-1", label="Caller supplied label")

    assert preview.label == "AI-generated illustration"


@pytest.mark.parametrize(
    ("prompt", "width", "height", "steps"),
    [
        ("", 768, 768, None),
        ("Soup", 255, 768, None),
        ("Soup", 768, 2_049, None),
        ("Soup", 768, 768, 0),
        ("Soup", 768, 768, 101),
        ("x" * 2_001, 768, 768, None),
    ],
)
def test_generation_request_rejects_invalid_inputs(
    prompt: str,
    width: int,
    height: int,
    steps: int | None,
) -> None:
    with pytest.raises(ValidationError):
        ImageGenerationRequest(
            prompt=prompt,
            width=width,
            height=height,
            steps=steps,
        )


def test_generated_image_is_immutable() -> None:
    image = GeneratedImage(
        data=b"image-bytes",
        media_type="image/png",
        width=768,
        height=768,
    )

    with pytest.raises(FrozenInstanceError):
        image.width = 256  # type: ignore[misc]


def test_recipe_option_exposes_the_labeled_preview_in_its_public_schema() -> None:
    option = RecipeOption(
        name="Tomato soup",
        summary="A quick soup.",
        cuisine="Italian",
        total_minutes=20,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato"],
        preview=DishPreview(artifact_id="artifact-1"),
    )

    response = RecipeOptionResponse.model_validate(option)

    assert response.model_dump() == {
        "name": "Tomato soup",
        "summary": "A quick soup.",
        "cuisine": "Italian",
        "total_minutes": 20,
        "difficulty": Difficulty.EASY,
        "used_ingredients": ["Tomato"],
        "missing_ingredients": [],
        "optional_ingredients": [],
        "id": option.id,
        "nutrition": None,
        "preview": {
            "artifact_id": "artifact-1",
            "label": "AI-generated illustration",
        },
        "warnings": [],
    }
