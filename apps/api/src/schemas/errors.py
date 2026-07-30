"""Public error response schemas."""

from pydantic import BaseModel, Field

from core.errors import ErrorCode


class ErrorDetail(BaseModel):
    """The stable error payload returned to API clients."""

    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    retryable: bool
    request_id: str
    session_id: str | None = None
    job_id: str | None = None


class ErrorResponse(BaseModel):
    """A stable envelope for all API error responses."""

    error: ErrorDetail
