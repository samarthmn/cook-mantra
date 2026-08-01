import os
from pathlib import Path

import pytest
from PIL import Image

from core.config import Settings
from domain.images import ImageGenerationRequest
from services.image_generation import BeastImageGenerator

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("COOK_MANTRA_RUN_LIVE") != "1",
        reason="Set COOK_MANTRA_RUN_LIVE=1 to run real Beast image generation.",
    ),
]


@pytest.mark.asyncio
async def test_real_image_generator_returns_a_verified_256_square_image(
    project_tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None)
    if settings.beast_base_url is None or settings.beast_api_key is None:
        pytest.fail("BEAST_BASE_URL and BEAST_API_KEY are required for this live test")
    progress_values: list[int] = []

    async def record_progress(value: int) -> None:
        progress_values.append(value)

    # Requires the Beast host to report the image model as available; otherwise
    # the job terminates as failed and this test raises image_provider_unavailable.
    async with BeastImageGenerator(
        base_url=str(settings.beast_base_url),
        api_key=settings.beast_api_key.get_secret_value(),
        model=settings.beast_image_model,
        timeout_seconds=settings.image_timeout_seconds,
        poll_interval_seconds=settings.beast_poll_interval_seconds,
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
