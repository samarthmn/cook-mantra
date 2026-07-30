"""Domain models for background jobs."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from core.errors import ErrorCode


class JobOperation(StrEnum):
    """Operations that may run in the background."""

    EXTRACT_INGREDIENTS = "extract_ingredients"
    GENERATE_OPTIONS = "generate_options"
    GENERATE_MORE_OPTIONS = "generate_more_options"
    GENERATE_RECIPES = "generate_recipes"


class JobStatus(StrEnum):
    """Lifecycle states for a background job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobError(BaseModel):
    """Safe error details retained on a failed job."""

    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    retryable: bool


class Job(BaseModel):
    """The public state of an in-process background job."""

    id: str
    operation: JobOperation
    session_id: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    result: dict[str, object] | None = None
    warnings: list[str] = Field(default_factory=list)
    error: JobError | None = None
    created_at: datetime
    updated_at: datetime
