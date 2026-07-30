"""Lock-protected in-memory storage for background jobs."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from core.errors import AppError, ErrorCode
from domain.jobs import Job, JobError, JobOperation, JobStatus


class JobStore:
    """Keep temporary job records safe across concurrent background tasks."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create(self, operation: JobOperation, session_id: str) -> Job:
        """Create a queued job for a session."""
        now = datetime.now(UTC)
        job = Job(
            id=str(uuid4()),
            operation=operation,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)
        return job.model_copy(deep=True)

    async def get(self, job_id: str) -> Job | None:
        """Return a detached copy of a job when it exists."""
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job is not None else None

    async def require(self, job_id: str) -> Job:
        """Return a detached job copy or raise the public not-found error."""
        async with self._lock:
            return self._require(job_id).model_copy(deep=True)

    async def mark_running(self, job_id: str) -> Job:
        """Mark a queued job as in progress."""
        return await self._update(
            job_id,
            expected_status=JobStatus.QUEUED,
            status=JobStatus.RUNNING,
        )

    async def update_progress(self, job_id: str, progress: int) -> Job:
        """Store a job's latest progress percentage."""
        return await self._update(
            job_id,
            expected_status=JobStatus.RUNNING,
            progress=progress,
        )

    async def mark_succeeded(self, job_id: str, result: dict[str, object]) -> Job:
        """Complete a job with its result."""
        return await self._update(
            job_id,
            expected_status=JobStatus.RUNNING,
            status=JobStatus.SUCCEEDED,
            progress=100,
            result=result,
            error=None,
        )

    async def mark_failed(self, job_id: str, error: JobError) -> Job:
        """Complete a job with safe error details."""
        return await self._update(
            job_id,
            expected_status=JobStatus.RUNNING,
            status=JobStatus.FAILED,
            result=None,
            error=error,
        )

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        """Delete jobs that have been inactive longer than their TTL."""
        expiry_time = now or datetime.now(UTC)
        async with self._lock:
            expired_ids = [
                job_id
                for job_id, job in self._jobs.items()
                if (expiry_time - job.updated_at).total_seconds() > self._ttl_seconds
            ]
            for job_id in expired_ids:
                del self._jobs[job_id]
            return expired_ids

    async def _update(
        self,
        job_id: str,
        *,
        expected_status: JobStatus | None = None,
        **changes: object,
    ) -> Job:
        async with self._lock:
            job = self._require(job_id)
            if expected_status is not None and job.status is not expected_status:
                raise ValueError(
                    f"Cannot update a {job.status} job; expected {expected_status}."
                )
            payload = job.model_dump()
            payload.update(changes)
            payload["updated_at"] = datetime.now(UTC)
            updated = Job.model_validate(payload)
            self._jobs[job_id] = updated.model_copy(deep=True)
            return updated.model_copy(deep=True)

    def _require(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise AppError(
                code=ErrorCode.RESOURCE_NOT_FOUND,
                message="Job was not found.",
                status_code=404,
                retryable=False,
                job_id=job_id,
            )
        return job
