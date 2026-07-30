"""Public background-job response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.errors import ErrorCode
from domain.jobs import JobOperation, JobStatus


class JobErrorResponse(BaseModel):
    """Safe error retained on a failed background job."""

    model_config = ConfigDict(from_attributes=True)

    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    retryable: bool


class JobResponse(BaseModel):
    """Public representation of a background job."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    operation: JobOperation
    session_id: str
    status: JobStatus
    progress: int
    result: dict[str, object] | None
    warnings: list[str]
    error: JobErrorResponse | None
    created_at: datetime
    updated_at: datetime
