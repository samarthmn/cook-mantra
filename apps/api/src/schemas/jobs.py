"""Public background-job response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.errors import ErrorCode
from domain.jobs import JobOperation, JobStatus


class JobErrorResponse(BaseModel):
    """Safe error retained on a failed background job."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "code": "model_output_invalid",
                    "message": "The recipe could not be generated.",
                    "details": {},
                    "retryable": False,
                }
            ]
        },
    )

    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    retryable: bool


class JobResponse(BaseModel):
    """Public representation of a background job."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "job-123",
                    "operation": "generate_recipes",
                    "session_id": "session-123",
                    "status": "succeeded",
                    "progress": 100,
                    "result": {
                        "selected_option_ids": ["option-123"],
                        "recipe_option_ids": ["option-123"],
                        "failed_option_ids": [],
                    },
                    "warnings": [],
                    "error": None,
                    "created_at": "2026-07-30T12:00:00Z",
                    "updated_at": "2026-07-30T12:01:00Z",
                }
            ]
        },
    )

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
