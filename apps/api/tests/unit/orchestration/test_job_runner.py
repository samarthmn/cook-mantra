import asyncio
import gc
import logging

import pytest

from core.errors import AppError, ErrorCode
from domain.jobs import JobOperation, JobStatus
from orchestration.job_runner import JobRunner
from repositories.job_store import JobStore


class BlockingCreateJobStore(JobStore):
    """Pause job creation to force a submit/shutdown ordering race."""

    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.create_started = asyncio.Event()
        self.allow_create = asyncio.Event()
        self.create_count = 0

    async def create(self, operation: JobOperation, session_id: str):
        self.create_started.set()
        await self.allow_create.wait()
        self.create_count += 1
        return await super().create(operation, session_id)


class FailingFailureStore(JobStore):
    """Make failure persistence fail after a worker's public error."""

    def __init__(self) -> None:
        super().__init__(ttl_seconds=21_600)
        self.failure_persist_attempted = asyncio.Event()

    async def mark_failed(self, job_id: str, error):
        self.failure_persist_attempted.set()
        raise RuntimeError("job store write failed")


@pytest.mark.asyncio
async def test_runner_records_progress_and_result() -> None:
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        await progress(40)
        return {"ok": True}

    job = await runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-1", worker)
    await runner.wait(job.id)
    completed = await store.require(job.id)

    assert completed.status is JobStatus.SUCCEEDED
    assert completed.progress == 100
    assert completed.result == {"ok": True}


@pytest.mark.asyncio
async def test_runner_preserves_public_app_error_from_worker() -> None:
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Ollama is temporarily unavailable.",
            status_code=503,
            retryable=True,
            details={"model": "vision"},
        )

    job = await runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-1", worker)
    await runner.wait(job.id)
    completed = await store.require(job.id)

    assert completed.status is JobStatus.FAILED
    assert completed.error is not None
    assert completed.error.code is ErrorCode.OLLAMA_UNAVAILABLE
    assert completed.error.message == "Ollama is temporarily unavailable."
    assert completed.error.details == {"model": "vision"}
    assert completed.error.retryable is True


@pytest.mark.asyncio
async def test_runner_hides_unexpected_worker_error_details() -> None:
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        raise RuntimeError("private provider failure")

    job = await runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-1", worker)
    await runner.wait(job.id)
    completed = await store.require(job.id)

    assert completed.status is JobStatus.FAILED
    assert completed.error is not None
    assert completed.error.code is ErrorCode.INTERNAL_ERROR
    assert completed.error.message == "An unexpected error occurred."
    assert completed.error.details == {}
    assert completed.error.retryable is False


@pytest.mark.asyncio
async def test_runner_logs_unexpected_worker_traceback_with_job_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        raise RuntimeError("private provider failure")

    with caplog.at_level(logging.ERROR, logger="orchestration.job_runner"):
        job = await runner.submit(
            JobOperation.EXTRACT_INGREDIENTS,
            "session-1",
            worker,
        )
        await runner.wait(job.id)

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "Unexpected background job failure"
    )
    assert record.job_id == job.id
    assert record.session_id == "session-1"
    assert record.operation == "extract_ingredients"
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError


@pytest.mark.asyncio
async def test_shutdown_cancels_active_worker_without_marking_job_failed() -> None:
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker(progress):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    job = await runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-1", worker)
    await started.wait()
    await runner.shutdown()
    incomplete = await store.require(job.id)

    assert cancelled.is_set()
    assert incomplete.status is JobStatus.RUNNING
    assert incomplete.error is None


@pytest.mark.asyncio
async def test_shutdown_closes_racing_and_future_submissions_without_live_tasks() -> (
    None
):
    store = BlockingCreateJobStore()
    runner = JobRunner(store, max_concurrent_jobs=1)
    worker_started = asyncio.Event()
    worker_cancelled = asyncio.Event()

    async def worker(progress):
        worker_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            worker_cancelled.set()
            raise

    submission = asyncio.create_task(
        runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-1", worker)
    )
    await store.create_started.wait()
    shutdown = asyncio.create_task(runner.shutdown())
    await asyncio.sleep(0)
    store.allow_create.set()
    job = await submission
    await shutdown

    with pytest.raises(RuntimeError, match="Job runner is shut down"):
        await runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-2", worker)
    await runner.shutdown()

    assert store.create_count == 1
    assert runner._tasks == {}
    assert worker_cancelled.is_set() or not worker_started.is_set()
    assert (await store.require(job.id)).status in {JobStatus.QUEUED, JobStatus.RUNNING}


@pytest.mark.asyncio
async def test_failure_persistence_error_is_observed_without_leaking_a_task() -> None:
    store = FailingFailureStore()
    runner = JobRunner(store, max_concurrent_jobs=1)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    task_errors: list[dict[str, object]] = []

    def capture_task_error(
        _: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        task_errors.append(context)

    async def worker(progress):
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Ollama is temporarily unavailable.",
            status_code=503,
            retryable=True,
        )

    loop.set_exception_handler(capture_task_error)
    try:
        await runner.submit(JobOperation.EXTRACT_INGREDIENTS, "session-1", worker)
        await store.failure_persist_attempted.wait()
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert runner._tasks == {}
    assert task_errors == []
