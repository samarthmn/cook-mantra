import asyncio

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from core.errors import AppError, ErrorCode
from domain.ingredients import ExtractionResult
from services.structured_output import invoke_structured


class SequencedStructuredModel:
    def __init__(self, outcomes: list[BaseException | ExtractionResult]) -> None:
        self.outcomes = outcomes
        self.attempts = 0

    async def ainvoke(self, messages: list[object]) -> ExtractionResult:
        outcome = self.outcomes[self.attempts]
        self.attempts += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ConfigCapturingStructuredModel:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.configs: list[dict[str, object]] = []

    async def ainvoke(
        self,
        messages: list[object],
        *,
        config: dict[str, object],
    ) -> ExtractionResult:
        self.configs.append(config)
        return self.result


class RetryingConfigCapturingModel:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.attempts = 0
        self.configs: list[dict[str, object]] = []

    async def ainvoke(
        self,
        messages: list[object],
        *,
        config: dict[str, object],
    ) -> ExtractionResult:
        self.attempts += 1
        self.configs.append(config)
        if self.attempts == 1:
            raise OutputParserException("invalid JSON")
        return self.result


def invalid_extraction_result() -> ValidationError:
    with pytest.raises(ValidationError) as raised:
        ExtractionResult.model_validate(
            {"detected": [{"name": "Tomato", "confidence": 1.5}]}
        )
    return raised.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parsing_error",
    [
        OutputParserException("invalid JSON"),
        invalid_extraction_result(),
    ],
)
async def test_parsing_failure_is_retried_before_returning_valid_result(
    parsing_error: Exception,
) -> None:
    expected = ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.94}])
    model = SequencedStructuredModel([parsing_error, expected])

    result = await invoke_structured(model, ["extract ingredients"])

    assert result is expected
    assert model.attempts == 2


@pytest.mark.asyncio
async def test_exhausted_parsing_failures_map_to_model_output_invalid() -> None:
    model = SequencedStructuredModel(
        [
            OutputParserException("invalid JSON"),
            invalid_extraction_result(),
        ]
    )

    with pytest.raises(AppError) as raised:
        await invoke_structured(model, ["extract ingredients"])

    assert raised.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert raised.value.status_code == 502
    assert raised.value.retryable is True
    assert model.attempts == 2


@pytest.mark.asyncio
async def test_exhausted_transient_timeouts_map_to_operation_timed_out() -> None:
    request = httpx.Request("POST", "http://ollama.test/api/chat")
    model = SequencedStructuredModel(
        [
            httpx.ReadTimeout("first timeout", request=request),
            httpx.ReadTimeout("second timeout", request=request),
        ]
    )

    with pytest.raises(AppError) as raised:
        await invoke_structured(model, ["extract ingredients"])

    assert raised.value.code is ErrorCode.OPERATION_TIMED_OUT
    assert raised.value.status_code == 504
    assert raised.value.retryable is True
    assert model.attempts == 2


@pytest.mark.asyncio
async def test_attempts_are_capped_at_two() -> None:
    request = httpx.Request("POST", "http://ollama.test/api/chat")
    model = SequencedStructuredModel(
        [
            httpx.ReadTimeout("first timeout", request=request),
            httpx.ReadTimeout("second timeout", request=request),
            ExtractionResult(detected=[]),
        ]
    )

    with pytest.raises(AppError):
        await invoke_structured(model, ["extract ingredients"], max_attempts=5)

    assert model.attempts == 2


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry() -> None:
    model = SequencedStructuredModel(
        [
            asyncio.CancelledError(),
            ExtractionResult(detected=[]),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        await invoke_structured(model, ["extract ingredients"])

    assert model.attempts == 1


@pytest.mark.asyncio
async def test_safe_runnable_config_reaches_the_structured_model() -> None:
    expected = ExtractionResult(detected=[])
    model = ConfigCapturingStructuredModel(expected)
    config = {
        "tags": ["cook-mantra", "master_chef"],
        "metadata": {
            "agent": "master_chef",
            "model": "qwen3.5:27b",
            "session_id": "session-1",
            "job_id": "job-1",
        },
    }

    result = await invoke_structured(
        model,
        ["generate options"],
        config=config,
    )

    assert result is expected
    assert model.configs == [config]


@pytest.mark.asyncio
async def test_safe_runnable_config_preserves_the_two_attempt_retry_limit() -> None:
    expected = ExtractionResult(detected=[])
    model = RetryingConfigCapturingModel(expected)
    config = {
        "tags": ["cook-mantra", "nutrition"],
        "metadata": {
            "agent": "nutrition",
            "model": "gpt-oss:20b",
            "session_id": "session-1",
            "job_id": "job-1",
        },
    }

    result = await invoke_structured(model, ["estimate nutrition"], config=config)

    assert result is expected
    assert model.attempts == 2
    assert model.configs == [config, config]


@pytest.mark.asyncio
async def test_empty_model_response_is_retried_then_reported_as_retryable() -> None:
    """A model that yields nothing raises a bare ValueError from the client."""
    model = SequencedStructuredModel(
        [
            ValueError("No data received from Ollama stream."),
            ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.9}]),
        ]
    )

    result = await invoke_structured(model, [])

    assert model.attempts == 2
    assert result.detected[0].name == "Tomato"


@pytest.mark.asyncio
async def test_persistently_empty_model_response_is_not_an_internal_error() -> None:
    """Escaping as internal_error would be non-retryable and undiagnosable."""
    model = SequencedStructuredModel(
        [
            ValueError("No data received from Ollama stream."),
            ValueError("No data received from Ollama stream."),
        ]
    )

    with pytest.raises(AppError) as raised:
        await invoke_structured(model, [])

    assert raised.value.code is ErrorCode.OLLAMA_UNAVAILABLE
    assert raised.value.retryable is True
    assert raised.value.status_code == 503
