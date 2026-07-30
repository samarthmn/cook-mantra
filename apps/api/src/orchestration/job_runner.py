"""In-process execution for background jobs."""

import asyncio
from collections.abc import Awaitable, Callable

from core.errors import AppError, ErrorCode
from domain.jobs import Job, JobError, JobOperation
from repositories.job_store import JobStore

type ProgressReporter = Callable[[int], Awaitable[None]]
type JobWorker = Callable[[ProgressReporter], Awaitable[dict[str, object]]]


class JobRunner:
    """Run stored background jobs with a bounded level of concurrency."""

    def __init__(self, store: JobStore, max_concurrent_jobs: int) -> None:
        self._store = store
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def submit(
        self,
        operation: JobOperation,
        session_id: str,
        worker: JobWorker,
    ) -> Job:
        """Create a queued job and schedule its worker."""
        job = await self._store.create(operation, session_id)
        self._tasks[job.id] = asyncio.create_task(self._execute(job.id, worker))
        return job

    async def wait(self, job_id: str) -> None:
        """Wait for a submitted task; used by tests."""
        task = self._tasks.get(job_id)
        if task is not None:
            await task

    async def shutdown(self) -> None:
        """Cancel and await every currently active job task."""
        tasks = list(self._tasks.items())
        for _, task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for job_id, _ in tasks:
            self._tasks.pop(job_id, None)

    async def _execute(self, job_id: str, worker: JobWorker) -> None:
        try:
            async with self._semaphore:
                await self._store.mark_running(job_id)

                async def report(progress: int) -> None:
                    await self._store.update_progress(job_id, progress)

                result = await worker(report)
                await self._store.mark_succeeded(job_id, result)
        except asyncio.CancelledError:
            raise
        except AppError as error:
            await self._store.mark_failed(
                job_id,
                JobError(
                    code=error.code,
                    message=error.message,
                    details=error.details,
                    retryable=error.retryable,
                ),
            )
        except Exception:
            await self._store.mark_failed(
                job_id,
                JobError(
                    code=ErrorCode.INTERNAL_ERROR,
                    message="An unexpected error occurred.",
                    retryable=False,
                ),
            )
        finally:
            self._tasks.pop(job_id, None)
