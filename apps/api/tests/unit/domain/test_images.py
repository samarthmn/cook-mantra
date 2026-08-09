from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from domain.images import DishPreview, ImageGenerationRequest, VerifiedRaster
from domain.model_runtime import (
    ImageOutputFormat,
    ImageQuality,
    ImageTuning,
    ProviderImageOutput,
)


def test_image_tuning_resolves_a_code_owned_timeout_default() -> None:
    tuning = ImageTuning()

    assert tuning.width == 1024
    assert tuning.height == 1024
    assert tuning.quality is ImageQuality.MEDIUM
    assert tuning.output_format is ImageOutputFormat.WEBP
    assert tuning.timeout_seconds == 600.0


def test_generation_request_uses_one_complete_image_tuning_value() -> None:
    tuning = ImageTuning(
        width=512,
        height=640,
        quality=ImageQuality.HIGH,
        output_format=ImageOutputFormat.PNG,
        timeout_seconds=45,
    )

    request = ImageGenerationRequest(prompt="Tomato masala", tuning=tuning)

    assert request.prompt == "Tomato masala"
    assert request.tuning == tuning


def test_generation_request_hides_prompt_from_diagnostics() -> None:
    private_prompt = "private-dish-prompt-canary"

    request = ImageGenerationRequest(prompt=private_prompt, tuning=ImageTuning())

    assert request.prompt == private_prompt
    assert private_prompt not in repr(request)


@pytest.mark.parametrize(
    "legacy_field",
    [
        {"width": 512},
        {"height": 512},
        {"steps": 8},
    ],
)
def test_generation_request_rejects_legacy_provider_specific_fields(
    legacy_field: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        ImageGenerationRequest(
            prompt="Tomato masala",
            tuning=ImageTuning(),
            **legacy_field,
        )


@pytest.mark.parametrize("prompt", ["", "x" * 2_001])
def test_generation_request_rejects_invalid_prompt(prompt: str) -> None:
    with pytest.raises(ValidationError):
        ImageGenerationRequest(prompt=prompt, tuning=ImageTuning())


def test_provider_image_output_hides_sensitive_base64_from_diagnostics() -> None:
    secret_base64 = "cHJpdmF0ZS1pbWFnZS1ieXRlcw=="

    output = ProviderImageOutput(
        base64_data=secret_base64,
        media_type="image/png",
    )

    assert output.base64_data == secret_base64
    assert secret_base64 not in repr(output)


def test_provider_image_output_is_immutable() -> None:
    output = ProviderImageOutput(base64_data="AA==")

    with pytest.raises(FrozenInstanceError):
        output.media_type = "image/png"  # type: ignore[misc]


def test_verified_raster_hides_bytes_and_is_immutable() -> None:
    raster = VerifiedRaster(
        data=b"private-raster-canary",
        media_type="image/png",
        width=512,
        height=640,
    )

    assert b"private-raster-canary" not in repr(raster).encode()
    with pytest.raises(FrozenInstanceError):
        raster.width = 256  # type: ignore[misc]


def test_preview_is_always_labeled_as_ai_generated_image() -> None:
    preview = DishPreview(artifact_id="artifact-1", label="Caller supplied label")

    assert preview.label == "AI-generated image"


def test_preview_label_schema_is_a_fixed_literal() -> None:
    label_schema = DishPreview.model_json_schema()["properties"]["label"]

    assert label_schema["const"] == "AI-generated image"


def test_preview_label_cannot_change_after_construction() -> None:
    preview = DishPreview(artifact_id="artifact-1")

    with pytest.raises(ValidationError):
        preview.label = "Generated image"

    assert preview.label == "AI-generated image"
