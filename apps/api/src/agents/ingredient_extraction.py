"""Ollama adapter for extracting visible ingredients from an image."""

from base64 import b64encode
from typing import Protocol

from langchain_core.messages import HumanMessage

from core import Agent, Settings
from domain.ingredients import ExtractionResult
from services.llm import get_model
from services.structured_output import StructuredModel, invoke_structured
from services.tracing import TracingService

EMPTY_DETECTION_WARNING = "No ingredients were confidently detected. Add them manually."
EXTRACTION_PROMPT = (
    "Report only visible food ingredients. "
    "Do not infer pantry items. "
    "List every food ingredient you can see, including uncertain ones. "
    "Assign each a confidence from 0.0 to 1.0. "
    "Use confidence below 0.5 instead of omitting an uncertain ingredient. "
    "Return an empty detected list only when the image contains no food."
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
        tracing: TracingService | None = None,
    ) -> None:
        self._model = model
        self._settings = settings
        self._tracing = tracing

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        """Return visible ingredients and warn when recognition is empty."""
        model = self._model
        if model is None:
            model = get_model(
                Agent.INGREDIENT_EXTRACTION,
                thinking=False,
                # Free the vision model's VRAM before the gpt-oss stages begin.
                keep_alive=0,
                settings=self._settings,
            ).with_structured_output(ExtractionResult)

        async def invoke_local_model() -> ExtractionResult:
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

        if self._tracing is None:
            return await invoke_local_model()
        return await self._tracing.invoke_vision(
            image,
            media_type,
            invoke_local_model,
        )
