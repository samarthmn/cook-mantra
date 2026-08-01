import pytest
from langchain_core.messages import HumanMessage
from tests.tracing_support import enabled_tracing

from agents import ingredient_extraction as ingredient_extraction_module
from agents.ingredient_extraction import OllamaIngredientExtractor
from core.config import PROJECT_ROOT, Agent, Settings
from core.logging import log_context
from domain.ingredients import ExtractionResult


class StructuredModel:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.messages: list[HumanMessage] = []

    async def ainvoke(self, messages: list[HumanMessage]) -> ExtractionResult:
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
    assert isinstance(message, HumanMessage)
    assert isinstance(message.content, list)
    assert len(message.content) == 2

    text_block = message.content[0]
    assert isinstance(text_block, dict)
    prompt = text_block["text"].lower()
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

    assert message.content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,cG5nLWJ5dGVz"},
    }


@pytest.mark.asyncio
async def test_extractor_warns_when_no_ingredients_are_detected() -> None:
    model = StructuredModel(ExtractionResult(detected=[]))
    extractor = OllamaIngredientExtractor(model=model)

    result = await extractor.extract(b"png-bytes", "image/png")

    assert result.warnings == [
        "No ingredients were confidently detected. Add them manually."
    ]


@pytest.mark.asyncio
async def test_extractor_forwards_its_exact_settings_to_model_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "adapter-settings-test",
        ollama_base_url="http://configured-ollama.test:11434",
        llm_timeout_seconds=17,
    )
    captured_settings: Settings | None = None

    def capture_model(
        agent: Agent,
        *,
        thinking: bool,
        keep_alive: float | str,
        settings: Settings,
    ) -> StructuredModelFactory:
        nonlocal captured_settings
        assert agent is Agent.INGREDIENT_EXTRACTION
        assert thinking is False
        assert keep_alive == 0
        captured_settings = settings
        return StructuredModelFactory(
            ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.94}])
        )

    monkeypatch.setattr(ingredient_extraction_module, "get_model", capture_model)
    extractor = OllamaIngredientExtractor(settings=settings)

    result = await extractor.extract(b"png-bytes", "image/png")

    assert result.detected[0].name == "Tomato"
    assert captured_settings is settings


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
    assert model.messages[0].content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,ZXh0cmFjdG9yLXZpc2lvbi1jYW5hcnk="},
    }
    captured = repr(client.calls)
    assert canary.decode() not in captured
    assert "ZXh0cmFjdG9yLXZpc2lvbi1jYW5hcnk=" not in captured
    assert "Tomato" not in captured
    assert "'detected_count': 1" in captured
