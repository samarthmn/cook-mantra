"""Background job query routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.dependencies import get_job_store
from repositories.job_store import JobStore
from schemas.errors import ErrorResponse
from schemas.jobs import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    operation_id="getJob",
    summary="Get a background job",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job not found.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Invalid job identifier.",
        },
    },
)
async def get_job(
    job_id: str,
    job_store: Annotated[JobStore, Depends(get_job_store)],
) -> JobResponse:
    """Return the current public state of one background job."""
    return JobResponse.model_validate(await job_store.require(job_id))
