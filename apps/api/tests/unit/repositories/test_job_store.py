from datetime import UTC, datetime, timedelta

import pytest

from core.errors import AppError, ErrorCode
from domain.jobs import JobError, JobOperation, JobStatus
from repositories.job_store import JobStore


@pytest.mark.asyncio
async def test_job_moves_through_valid_states() -> None:
    store = JobStore(ttl_seconds=21_600)
    job = await store.create(JobOperation.EXTRACT_INGREDIENTS, "session-1")

    running = await store.mark_running(job.id)
    succeeded = await store.mark_succeeded(job.id, {"ingredient_count": 2})

    assert running.status is JobStatus.RUNNING
    assert succeeded.status is JobStatus.SUCCEEDED
    assert succeeded.progress == 100
    assert succeeded.result == {"ingredient_count": 2}


@pytest.mark.asyncio
async def test_job_reads_do_not_expose_stored_state() -> None:
    store = JobStore(ttl_seconds=21_600)
    job = await store.create(JobOperation.EXTRACT_INGREDIENTS, "session-1")
    await store.mark_succeeded(job.id, {"ingredient_count": 2})

    read = await store.require(job.id)
    read.result["ingredient_count"] = 99  # type: ignore[index]

    stored = await store.require(job.id)

    assert stored.result == {"ingredient_count": 2}


@pytest.mark.asyncio
async def test_writing_a_result_does_not_retain_the_callers_mapping() -> None:
    store = JobStore(ttl_seconds=21_600)
    job = await store.create(JobOperation.EXTRACT_INGREDIENTS, "session-1")
    result = {"ingredient_count": 2}

    await store.mark_succeeded(job.id, result)
    result["ingredient_count"] = 99

    assert (await store.require(job.id)).result == {"ingredient_count": 2}


@pytest.mark.asyncio
async def test_writing_an_error_does_not_retain_the_callers_details() -> None:
    store = JobStore(ttl_seconds=21_600)
    job = await store.create(JobOperation.EXTRACT_INGREDIENTS, "session-1")
    error = JobError(
        code=ErrorCode.INTERNAL_ERROR,
        message="The operation failed.",
        details={"attempt": 1},
        retryable=False,
    )

    await store.mark_failed(job.id, error)
    error.details["attempt"] = 2

    assert (await store.require(job.id)).error.details == {"attempt": 1}


@pytest.mark.asyncio
async def test_out_of_range_progress_is_rejected_without_changing_the_job() -> None:
    store = JobStore(ttl_seconds=21_600)
    job = await store.create(JobOperation.EXTRACT_INGREDIENTS, "session-1")

    with pytest.raises(ValueError):
        await store.update_progress(job.id, 101)

    assert (await store.require(job.id)).progress == 0


@pytest.mark.asyncio
async def test_missing_job_raises_the_standard_not_found_error() -> None:
    store = JobStore(ttl_seconds=21_600)

    with pytest.raises(AppError) as raised:
        await store.require("missing-job")

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert raised.value.status_code == 404
    assert raised.value.retryable is False
    assert raised.value.job_id == "missing-job"


@pytest.mark.asyncio
async def test_expired_jobs_are_deleted() -> None:
    store = JobStore(ttl_seconds=10)
    job = await store.create(JobOperation.EXTRACT_INGREDIENTS, "session-1")

    removed = await store.delete_expired(now=datetime.now(UTC) + timedelta(seconds=11))

    assert removed == [job.id]
    assert await store.get(job.id) is None
