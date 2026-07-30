import asyncio

import pytest

from core.errors import AppError, ErrorCode
from domain.jobs import JobOperation, JobStatus
from orchestration.job_runner import JobRunner
from repositories.job_store import JobStore


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
