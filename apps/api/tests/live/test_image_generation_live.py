import os
from pathlib import Path

import pytest
from PIL import Image

from core.config import Model, Settings
from domain.images import ImageGenerationRequest
from services.image_generation import OllamaImageGenerator

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("COOK_MANTRA_RUN_LIVE") != "1",
        reason="Set COOK_MANTRA_RUN_LIVE=1 to run real Ollama image generation.",
    ),
]


@pytest.mark.asyncio
async def test_real_image_generator_returns_a_verified_256_square_image(
    project_tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None)
    progress_values: list[int] = []

    async def record_progress(value: int) -> None:
        progress_values.append(value)

    async with OllamaImageGenerator(
        base_url=str(settings.ollama_base_url),
        model=Model.Z_IMAGE,
        timeout_seconds=settings.image_timeout_seconds,
    ) as generator:
        generated = await generator.generate(
            ImageGenerationRequest(
                prompt=(
                    "A plated Indian tomato curry, realistic food photography, "
                    "natural light, no text, no logos, no people"
                ),
                width=256,
                height=256,
                steps=settings.image_steps,
            ),
            record_progress,
        )

    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[generated.media_type]
    image_path = project_tmp_path / f"live-dish-preview{suffix}"
    image_path.write_bytes(generated.data)

    with Image.open(image_path) as image:
        image.verify()
    with Image.open(image_path) as image:
        assert image.size == (256, 256)

    assert generated.width == 256
    assert generated.height == 256
    assert progress_values[-1] == 100
