"""Background job query routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from api.dependencies import get_job_store
from repositories.job_store import JobStore
from schemas.jobs import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    job_store: Annotated[JobStore, Depends(get_job_store)],
) -> JobResponse:
    """Return the current public state of one background job."""
    return JobResponse.model_validate(await job_store.require(job_id))
