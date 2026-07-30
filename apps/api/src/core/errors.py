"""Stable public API errors and FastAPI exception handlers."""

import logging
from enum import StrEnum
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.http import REQUEST_ID_HEADER

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Error codes that form the public API contract."""

    INVALID_REQUEST = "invalid_request"
    RESOURCE_NOT_FOUND = "resource_not_found"
    INVALID_SESSION_TRANSITION = "invalid_session_transition"
    INGREDIENTS_NOT_CONFIRMED = "ingredients_not_confirmed"
    RECIPE_DUPLICATE = "recipe_duplicate"
    OLLAMA_UNAVAILABLE = "ollama_unavailable"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_OUTPUT_INVALID = "model_output_invalid"
    OPERATION_TIMED_OUT = "operation_timed_out"
    SERVICE_BUSY = "service_busy"
    ARTIFACT_FAILURE = "artifact_failure"
    INTERNAL_ERROR = "internal_error"


class AppError(Exception):
    """An expected application error with a safe client-facing response."""

    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str,
        status_code: int,
        retryable: bool,
        details: dict[str, object] | None = None,
        session_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}
        self.session_id = session_id
        self.job_id = job_id


def install_error_handlers(app: FastAPI) -> None:
    """Install handlers that return the public error response envelope."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)


async def _handle_app_error(request: Request, error: AppError) -> JSONResponse:
    return _error_response(
        request,
        code=error.code,
        message=error.message,
        status_code=error.status_code,
        retryable=error.retryable,
        details=error.details,
        session_id=error.session_id,
        job_id=error.job_id,
    )


async def _handle_validation_error(
    request: Request, _: RequestValidationError
) -> JSONResponse:
    return _error_response(
        request,
        code=ErrorCode.INVALID_REQUEST,
        message="The request is invalid.",
        status_code=422,
        retryable=False,
    )


async def _handle_http_error(
    request: Request, error: StarletteHTTPException
) -> JSONResponse:
    if error.status_code == 404:
        code = ErrorCode.RESOURCE_NOT_FOUND
        message = "Resource was not found."
    elif error.status_code < 500:
        code = ErrorCode.INVALID_REQUEST
        message = "The request is invalid."
    else:
        code = ErrorCode.INTERNAL_ERROR
        message = "An unexpected error occurred."

    return _error_response(
        request,
        code=code,
        message=message,
        status_code=error.status_code,
        retryable=False,
        headers=error.headers,
    )


async def _handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled API exception", extra={"request_id": _request_id(request)}
    )
    return _error_response(
        request,
        code=ErrorCode.INTERNAL_ERROR,
        message="An unexpected error occurred.",
        status_code=500,
        retryable=False,
    )


def _error_response(
    request: Request,
    *,
    code: ErrorCode,
    message: str,
    status_code: int,
    retryable: bool,
    details: dict[str, object] | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    from schemas.errors import ErrorDetail, ErrorResponse

    request_id = _request_id(request)
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            details=details or {},
            retryable=retryable,
            request_id=request_id,
            session_id=session_id,
            job_id=job_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())
