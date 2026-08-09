import pytest
from tests.tracing_support import enabled_tracing

from agents.ingredient_extraction import OllamaIngredientExtractor
from core.config import PROJECT_ROOT, Settings
from core.logging import log_context
from domain.ingredients import ExtractionResult
from domain.model_runtime import AgentRole, ModelMessage


class StructuredModel:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.messages: list[ModelMessage] = []

    async def ainvoke(self, messages: list[ModelMessage]) -> ExtractionResult:
        self.messages = messages
        return self.result


class StructuredModelFactory:
    def __init__(self, result: ExtractionResult) -> None:
        self._result = result

    def with_structured_output(
        self,
        schema: type[ExtractionResult],
    ) -> StructuredModel:
        assert schema is ExtractionResult
        return StructuredModel(self._result)


@pytest.mark.asyncio
async def test_extractor_sends_visible_ingredient_multimodal_message() -> None:
    model = StructuredModel(
        ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.94}])
    )
    extractor = OllamaIngredientExtractor(model=model)

    result = await extractor.extract(b"png-bytes", "image/png")

    assert result.detected[0].name == "Tomato"
    assert len(model.messages) == 1
    message = model.messages[0]
    assert isinstance(message, ModelMessage)
    prompt = message.content.lower()
    assert "only visible food ingredients" in prompt
    assert "do not infer pantry items" in prompt
    assert "confidence from 0.0 to 1.0" in prompt
    assert "including uncertain ones" in prompt
    assert "short canonical culinary names" in prompt
    assert "without container, location, plating, or quantity descriptors" in prompt
    assert "blueberries, not blueberries in bowl" in prompt
    assert "avocado, not avocado slice" in prompt
    assert "grapefruit, not grapefruit half" in prompt
    assert "below 0.5 instead of omitting" in prompt
    assert "empty detected list only when the image contains no food" in prompt

    assert message.image == b"png-bytes"
    assert message.media_type == "image/png"


@pytest.mark.asyncio
async def test_extractor_warns_when_no_ingredients_are_detected() -> None:
    model = StructuredModel(ExtractionResult(detected=[]))
    extractor = OllamaIngredientExtractor(model=model)

    result = await extractor.extract(b"png-bytes", "image/png")

    assert result.warnings == [
        "No ingredients were confidently detected. Add them manually."
    ]


@pytest.mark.asyncio
async def test_extractor_builds_exact_role_schema_from_injected_factory() -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "adapter-settings-test",
        llm_timeout_seconds=17,
    )

    class ModelFactory:
        def __init__(self) -> None:
            self.calls: list[tuple[AgentRole, type[ExtractionResult]]] = []

        def build(
            self,
            role: AgentRole,
            schema: type[ExtractionResult],
        ) -> StructuredModel:
            self.calls.append((role, schema))
            return StructuredModel(
                ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.94}])
            )

    factory = ModelFactory()
    extractor = OllamaIngredientExtractor(settings=settings, model_factory=factory)

    result = await extractor.extract(b"png-bytes", "image/png")

    assert result.detected[0].name == "Tomato"
    assert factory.calls == [(AgentRole.INGREDIENT_EXTRACTOR, ExtractionResult)]


@pytest.mark.asyncio
async def test_extractor_traces_only_safe_summaries_around_full_local_image() -> None:
    canary = b"extractor-vision-canary"
    model = StructuredModel(
        ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.94}])
    )
    tracing, client = enabled_tracing()
    extractor = OllamaIngredientExtractor(model=model, tracing=tracing)

    with log_context(session_id="session-1", job_id="job-1"):
        result = await extractor.extract(canary, "image/png")

    assert result.detected[0].name == "Tomato"
    assert model.messages[0].image == canary
    assert model.messages[0].media_type == "image/png"
    captured = repr(client.calls)
    assert canary.decode() not in captured
    assert "ZXh0cmFjdG9yLXZpc2lvbi1jYW5hcnk=" not in captured
    assert "Tomato" not in captured
    assert "'detected_count': 1" in captured
