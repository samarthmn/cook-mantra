import pytest
from langchain_core.messages import HumanMessage

from agents.ingredient_extraction import OllamaIngredientExtractor
from domain.ingredients import ExtractionResult


class StructuredModel:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.messages: list[HumanMessage] = []

    async def ainvoke(self, messages: list[HumanMessage]) -> ExtractionResult:
        self.messages = messages
        return self.result


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
    assert "confidence from 0 to 1" in prompt
    assert "empty list when uncertain" in prompt

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
