"""Provider-neutral agent for extracting visible ingredients from an image."""

from typing import Protocol

from core import Settings
from core.runtime_config import editable_instruction_for, get_runtime_snapshot
from domain.ingredients import ExtractionResult
from domain.model_prompts import compose_model_prompt
from domain.model_runtime import AgentRole, ModelMessage
from services.model_runtime import RuntimeStructuredModelFactory, StructuredModelFactory
from services.structured_output import StructuredModel, invoke_structured
from services.tracing import TracingService

EMPTY_DETECTION_WARNING = "No ingredients were confidently detected. Add them manually."
EXTRACTION_PROMPT = (
    "Report only visible food ingredients. "
    "Do not infer pantry items. "
    "List every food ingredient you can see, including uncertain ones. "
    "Use short canonical culinary names without container, location, plating, or "
    "quantity descriptors: blueberries, not blueberries in bowl; avocado, not "
    "avocado slice; grapefruit, not grapefruit half. "
    "Assign each a confidence from 0.0 to 1.0. "
    "Use confidence below 0.5 instead of omitting an uncertain ingredient. "
    "Return an empty detected list only when the image contains no food."
)


class IngredientExtractor(Protocol):
    """Extract visible ingredients from image bytes."""

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        raise NotImplementedError


class IngredientExtractionAgent:
    """Extract ingredients with the configured role-level vision model."""

    def __init__(
        self,
        model: StructuredModel[ExtractionResult] | None = None,
        settings: Settings | None = None,
        tracing: TracingService | None = None,
        editable_instruction: str | None = None,
        model_factory: StructuredModelFactory | None = None,
    ) -> None:
        self._model = model
        self._settings = settings
        self._tracing = tracing
        self._editable_instruction = editable_instruction
        self._model_factory = model_factory

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        """Return visible ingredients and warn when recognition is empty."""
        model = self._model
        if model is None:
            factory = self._model_factory or RuntimeStructuredModelFactory(
                get_runtime_snapshot()
            )
            model = factory.build(AgentRole.INGREDIENT_EXTRACTOR, ExtractionResult)

        async def invoke_local_model() -> ExtractionResult:
            prompt = compose_model_prompt(
                protected_invariant=EXTRACTION_PROMPT,
                editable_instruction=(
                    self._editable_instruction
                    if self._editable_instruction is not None
                    else editable_instruction_for(AgentRole.INGREDIENT_EXTRACTOR)
                ),
                untrusted_data={"media_type": media_type},
                schema_name="ExtractionResult",
            )
            message = ModelMessage(
                role="user",
                content=prompt,
                image=image,
                media_type=media_type,
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


# Compatibility name retained for callers outside the provider-neutral composition.
OllamaIngredientExtractor = IngredientExtractionAgent
