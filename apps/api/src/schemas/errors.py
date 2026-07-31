"""Public error response schemas."""

from pydantic import BaseModel, ConfigDict, Field

from core.errors import ErrorCode

ERROR_DETAIL_EXAMPLE = {
    "code": "resource_not_found",
    "message": "Session was not found.",
    "details": {},
    "retryable": False,
    "request_id": "request-123",
    "session_id": "session-123",
    "job_id": None,
}


class ErrorDetail(BaseModel):
    """The stable error payload returned to API clients."""

    model_config = ConfigDict(json_schema_extra={"examples": [ERROR_DETAIL_EXAMPLE]})

    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    retryable: bool
    request_id: str
    session_id: str | None = None
    job_id: str | None = None


class ErrorResponse(BaseModel):
    """A stable envelope for all API error responses."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"error": ERROR_DETAIL_EXAMPLE}]}
    )

    error: ErrorDetail
