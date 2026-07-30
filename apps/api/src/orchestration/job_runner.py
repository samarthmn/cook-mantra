"""In-process execution for background jobs."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from core.errors import AppError, ErrorCode
from core.logging import log_context
from domain.jobs import Job, JobError, JobOperation
from repositories.job_store import JobStore

type ProgressReporter = Callable[[int], Awaitable[None]]
type JobWorker = Callable[[ProgressReporter], Awaitable[dict[str, object]]]

logger = logging.getLogger(__name__)


class JobRunner:
    """Run stored background jobs with a bounded level of concurrency."""

    def __init__(
        self,
        store: JobStore,
        max_concurrent_jobs: int,
        max_queued_jobs: int = 4,
    ) -> None:
        self._store = store
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._capacity = max_concurrent_jobs + max_queued_jobs
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False

    @property
    def active_count(self) -> int:
        """Return the number of runner-owned executing or queued tasks."""
        return len(self._tasks)

    async def submit(
        self,
        operation: JobOperation,
        session_id: str,
        worker: JobWorker,
    ) -> Job:
        """Create a queued job and schedule its worker."""
        submitted_at = perf_counter()
        async with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Job runner is shut down.")
            if len(self._tasks) >= self._capacity:
                with log_context(session_id=session_id):
                    logger.info(
                        "job_rejected",
                        extra={
                            "event": "job_rejected",
                            "operation": operation.value,
                            "duration_ms": round(
                                (perf_counter() - submitted_at) * 1_000,
                                3,
                            ),
                            "final_status": "rejected",
                            "error_code": ErrorCode.SERVICE_BUSY.value,
                        },
                    )
                raise AppError(
                    code=ErrorCode.SERVICE_BUSY,
                    message="The service is busy. Try again shortly.",
                    status_code=503,
                    retryable=True,
                )
            job = await self._store.create(operation, session_id)
            with log_context(session_id=session_id, job_id=job.id):
                self._tasks[job.id] = asyncio.create_task(
                    self._execute(
                        job.id,
                        operation,
                        session_id,
                        worker,
                    )
                )
            return job

    async def wait(self, job_id: str) -> None:
        """Wait for a submitted task; used by tests."""
        task = self._tasks.get(job_id)
        if task is not None:
            await task

    async def shutdown(self) -> None:
        """Cancel and await every currently active job task."""
        async with self._lifecycle_lock:
            self._closed = True
            tasks = list(self._tasks.items())
        for _, task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for job_id, _ in tasks:
            self._tasks.pop(job_id, None)

    async def _execute(
        self,
        job_id: str,
        operation: JobOperation,
        session_id: str,
        worker: JobWorker,
    ) -> None:
        started_at = perf_counter()
        final_status = "failed"
        error_code: str | None = ErrorCode.INTERNAL_ERROR.value
        with log_context(session_id=session_id, job_id=job_id):
            try:
                try:
                    async with self._semaphore:
                        await self._store.mark_running(job_id)

                        async def report(progress: int) -> None:
                            await self._store.update_progress(job_id, progress)

                        result = await worker(report)
                        await self._store.mark_succeeded(job_id, result)
                    final_status = "succeeded"
                    error_code = None
                except AppError as error:
                    error_code = error.code.value
                    await self._record_failure(
                        job_id,
                        JobError(
                            code=error.code,
                            message=error.message,
                            details=error.details,
                            retryable=error.retryable,
                        ),
                    )
                except Exception as error:
                    logger.error(
                        "job_internal_failure",
                        extra={
                            "event": "job_internal_failure",
                            "operation": operation.value,
                            "error_code": ErrorCode.INTERNAL_ERROR.value,
                            "exception_type": type(error).__name__,
                        },
                    )
                    await self._record_failure(
                        job_id,
                        JobError(
                            code=ErrorCode.INTERNAL_ERROR,
                            message="An unexpected error occurred.",
                            retryable=False,
                        ),
                    )
            except asyncio.CancelledError:
                final_status = "cancelled"
                error_code = "cancelled"
                raise
            finally:
                completion: dict[str, object] = {
                    "event": "job_completed",
                    "operation": operation.value,
                    "duration_ms": round(
                        (perf_counter() - started_at) * 1_000,
                        3,
                    ),
                    "final_status": final_status,
                }
                if error_code is not None:
                    completion["error_code"] = error_code
                logger.info("job_completed", extra=completion)
                self._tasks.pop(job_id, None)

    async def _record_failure(self, job_id: str, error: JobError) -> None:
        try:
            await self._store.mark_failed(job_id, error)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "job_failure_persistence_failed",
                extra={
                    "event": "job_failure_persistence_failed",
                    "error_code": ErrorCode.INTERNAL_ERROR.value,
                    "exception_type": type(error).__name__,
                },
            )
