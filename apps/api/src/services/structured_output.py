"""Bounded retries and stable errors for structured model calls."""

from typing import Protocol

import httpx
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from core.errors import AppError, ErrorCode


class StructuredModel[ResultT](Protocol):
    """A model configured to return one validated result type."""

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> ResultT:
        raise NotImplementedError


async def invoke_structured[ResultT](
    model: StructuredModel[ResultT],
    messages: object,
    max_attempts: int = 2,
    *,
    config: dict[str, object] | None = None,
) -> ResultT:
    """Invoke a structured model with no more than two narrow retries."""
    attempt_limit = max(1, min(max_attempts, 2))
    for attempt in range(attempt_limit):
        try:
            if config:
                return await model.ainvoke(messages, config=config)
            return await model.ainvoke(messages)
        except (OutputParserException, ValidationError) as error:
            if attempt + 1 == attempt_limit:
                raise AppError(
                    code=ErrorCode.MODEL_OUTPUT_INVALID,
                    message="The model returned invalid structured output.",
                    status_code=502,
                    retryable=True,
                ) from error
        except (httpx.TimeoutException, TimeoutError) as error:
            if attempt + 1 == attempt_limit:
                raise AppError(
                    code=ErrorCode.OPERATION_TIMED_OUT,
                    message="The model operation timed out.",
                    status_code=504,
                    retryable=True,
                ) from error

    raise RuntimeError("Structured model invocation completed without a result.")
