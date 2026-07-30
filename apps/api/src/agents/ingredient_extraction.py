"""Ollama adapter for extracting visible ingredients from an image."""

from base64 import b64encode
from typing import Protocol

from langchain_core.messages import HumanMessage

from core import Agent, Settings
from domain.ingredients import ExtractionResult
from services.llm import get_model
from services.structured_output import StructuredModel, invoke_structured

EMPTY_DETECTION_WARNING = "No ingredients were confidently detected. Add them manually."
EXTRACTION_PROMPT = (
    "Report only visible food ingredients. "
    "Do not infer pantry items. "
    "Assign confidence from 0 to 1. "
    "Return an empty list when uncertain."
)


class IngredientExtractor(Protocol):
    """Extract visible ingredients from image bytes."""

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        raise NotImplementedError


class OllamaIngredientExtractor:
    """Extract ingredients with Ollama's configured vision model."""

    def __init__(
        self,
        model: StructuredModel[ExtractionResult] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._model = model
        self._settings = settings

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        """Return visible ingredients and warn when recognition is empty."""
        model = self._model
        if model is None:
            model = get_model(
                Agent.INGREDIENT_EXTRACTION,
                thinking=False,
                settings=self._settings,
            ).with_structured_output(ExtractionResult)

        encoded_image = b64encode(image).decode("ascii")
        message = HumanMessage(
            content=[
                {"type": "text", "text": EXTRACTION_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{encoded_image}",
                    },
                },
            ]
        )
        result = await invoke_structured(model, [message])
        if result.detected or EMPTY_DETECTION_WARNING in result.warnings:
            return result

        return result.model_copy(
            update={"warnings": [*result.warnings, EMPTY_DETECTION_WARNING]}
        )
