import os
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from agents.ingredient_extraction import OllamaIngredientExtractor
from domain.ingredients import ExtractionResult

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("COOK_MANTRA_RUN_LIVE") != "1",
        reason="Set COOK_MANTRA_RUN_LIVE=1 to run the real Ollama extraction.",
    ),
]


def labeled_ingredient_image() -> bytes:
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
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_real_ingredient_extractor_returns_valid_structured_output() -> None:
    result = await OllamaIngredientExtractor().extract(
        labeled_ingredient_image(),
        "image/png",
    )

    validated = ExtractionResult.model_validate(result)
    assert isinstance(validated, ExtractionResult)
