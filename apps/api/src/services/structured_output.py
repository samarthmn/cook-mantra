"""Bounded retries and stable errors for structured model calls."""

from typing import Protocol

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from core.errors import AppError, ErrorCode
from domain.model_runtime import ProviderErrorKind
from services.provider_errors import ProviderInvocationError


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
        except ProviderInvocationError as error:
            if not error.error.retryable or attempt + 1 == attempt_limit:
                raise _public_provider_error(error) from error

    raise RuntimeError("Structured model invocation completed without a result.")


def _public_provider_error(error: ProviderInvocationError) -> AppError:
    mappings: dict[ProviderErrorKind, tuple[ErrorCode, int]] = {
        ProviderErrorKind.UNAVAILABLE: (ErrorCode.PROVIDER_UNAVAILABLE, 503),
        ProviderErrorKind.AUTHENTICATION_FAILED: (
            ErrorCode.PROVIDER_AUTHENTICATION_FAILED,
            401,
        ),
        ProviderErrorKind.RATE_LIMITED: (ErrorCode.PROVIDER_RATE_LIMITED, 429),
        ProviderErrorKind.PAYMENT_REQUIRED: (
            ErrorCode.PROVIDER_PAYMENT_REQUIRED,
            402,
        ),
        ProviderErrorKind.CAPABILITY_MISSING: (
            ErrorCode.MODEL_CAPABILITY_MISSING,
            503,
        ),
        ProviderErrorKind.PROTOCOL_ERROR: (ErrorCode.PROVIDER_PROTOCOL_ERROR, 502),
        ProviderErrorKind.TIMED_OUT: (ErrorCode.OPERATION_TIMED_OUT, 504),
        ProviderErrorKind.INVALID_OUTPUT: (ErrorCode.MODEL_OUTPUT_INVALID, 502),
    }
    code, status_code = mappings[error.error.kind]
    return AppError(
        code=code,
        message=error.error.message,
        status_code=status_code,
        retryable=error.error.retryable,
    )
