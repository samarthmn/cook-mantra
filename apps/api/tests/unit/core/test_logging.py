import asyncio
import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import pytest

from core.errors import AppError, ErrorCode
from core.logging import (
    cause_chain,
    configure_logging,
    current_log_context,
    log_context,
)
from domain.jobs import JobOperation
from orchestration.job_runner import JobRunner
from repositories.job_store import JobStore


def _records(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip()
    ]


@contextmanager
def _configured_logging() -> Iterator[logging.Logger]:
    configure_logging("INFO")
    yield logging.getLogger("core.logging.tests")


def test_log_context_nests_and_restores_unspecified_outer_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _configured_logging() as logger:
        with log_context(request_id="request-1", session_id="session-1"):
            logger.info("outer", extra={"event": "outer"})
            with log_context(job_id="job-1"):
                logger.info("inner", extra={"event": "inner"})
            logger.info("restored", extra={"event": "restored"})
        logger.info("empty", extra={"event": "empty"})

    records = {record["event"]: record for record in _records(capsys)}
    assert records["outer"]["request_id"] == "request-1"
    assert records["outer"]["session_id"] == "session-1"
    assert "job_id" not in records["outer"]
    assert records["inner"]["request_id"] == "request-1"
    assert records["inner"]["session_id"] == "session-1"
    assert records["inner"]["job_id"] == "job-1"
    assert records["restored"]["request_id"] == "request-1"
    assert "job_id" not in records["restored"]
    assert "request_id" not in records["empty"]
    assert "session_id" not in records["empty"]


def test_current_log_context_returns_a_detached_read_only_snapshot() -> None:
    with log_context(request_id="request-1", session_id="session-1", job_id="job-1"):
        snapshot = current_log_context()
        snapshot["job_id"] = "tampered"

        assert current_log_context() == {
            "request_id": "request-1",
            "session_id": "session-1",
            "job_id": "job-1",
        }

    assert current_log_context() == {}


@pytest.mark.asyncio
async def test_log_context_is_isolated_between_concurrent_tasks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logger = logging.getLogger("core.logging.tests")
    release = asyncio.Event()

    async def emit(request_id: str) -> None:
        with log_context(request_id=request_id):
            await release.wait()
            logger.info("task", extra={"event": "task"})

    first = asyncio.create_task(emit("request-a"))
    second = asyncio.create_task(emit("request-b"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)

    records = _records(capsys)
    assert {record["request_id"] for record in records} == {
        "request-a",
        "request-b",
    }


def test_formatter_recursively_converts_and_redacts_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CustomValue:
        pass

    with _configured_logging() as logger:
        logger.info(
            "redaction",
            extra={
                "event": "redaction",
                "api_key": "canary-top-level-key",
                "payload": {
                    "safe": ("one", 2),
                    "apiKey": "canary-api-key",
                    "nested": [
                        {"refresh_token": "canary-token"},
                        {"dishImage": "canary-image"},
                        {"value": CustomValue()},
                    ],
                    "innocent_bytes": b"canary-binary",
                    "binary": "data:image/png;base64,Y2FuYXJ5LWltYWdl",
                    "note": "api key: canary-innocuous-key",
                    "authorization": "Bearer canary-super-secret-value",
                    "url_blob": (
                        "________________________________________________________________"
                    ),
                    "wrapped_blob": (
                        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
                        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    ),
                    "wrapped_url_blob": (
                        "________________________________\r\n"
                        "________________________________"
                    ),
                },
            },
        )

    output = capsys.readouterr().err
    record = json.loads(output)
    payload = record["payload"]
    assert payload["safe"] == ["one", 2]
    assert payload["apiKey"] == "[REDACTED]"
    assert payload["nested"][0]["refresh_token"] == "[REDACTED]"
    assert payload["nested"][1]["dishImage"] == "[REDACTED]"
    assert payload["nested"][2]["value"] == "<CustomValue>"
    assert payload["innocent_bytes"] == "[REDACTED]"
    assert payload["binary"] == "[REDACTED]"
    assert payload["note"] == "[REDACTED]"
    assert payload["authorization"] == "[REDACTED]"
    assert payload["url_blob"] == "[REDACTED]"
    assert payload["wrapped_blob"] == "[REDACTED]"
    assert payload["wrapped_url_blob"] == "[REDACTED]"
    assert record["api_key"] == "[REDACTED]"
    assert "canary" not in output


def test_formatter_drops_exception_text_and_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logger = logging.getLogger("core.logging.tests")
    error = RuntimeError("provider_secret=canary-provider-secret")

    try:
        raise error
    except RuntimeError:
        logger.exception(
            "canary unsafe prose",
            extra={"event": "safe_failure", "failure": error},
        )

    output = capsys.readouterr().err
    record = json.loads(output)
    assert record["event"] == "safe_failure"
    assert record["failure"] == {"exception_type": "RuntimeError"}
    assert "canary" not in output
    assert "provider_secret" not in output
    assert "Traceback" not in output


def test_cause_chain_stops_at_suppressed_exception_context() -> None:
    try:
        raise ValueError("Bearer secret-context-canary")
    except ValueError:
        try:
            raise RuntimeError("safe normalized failure") from None
        except RuntimeError as error:
            normalized = error

    assert normalized.__cause__ is None
    assert normalized.__context__ is not None
    assert normalized.__suppress_context__ is True
    assert cause_chain(normalized) == ["RuntimeError: safe normalized failure"]


def test_configure_logging_is_idempotent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    configure_logging("INFO")

    logging.getLogger("core.logging.tests").info(
        "once",
        extra={"event": "configured_once"},
    )

    records = _records(capsys)
    assert [record["event"] for record in records] == ["configured_once"]


def test_control_characters_remain_valid_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logger = logging.getLogger("core.logging.tests")

    with log_context(request_id="request-\n-one"):
        logger.info(
            "control characters",
            extra={"event": "control_characters", "path": "/line\n\u0000"},
        )

    record = json.loads(capsys.readouterr().err)
    assert record["request_id"] == "request-\n-one"
    assert record["path"] == "/line\n\u0000"


def test_formatter_failure_is_silent_and_does_not_use_unsafe_repr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ExplodingMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("canary-getitem")

        def __iter__(self):
            raise RuntimeError("canary-iter")

        def __len__(self) -> int:
            raise RuntimeError("canary-len")

        def __repr__(self) -> str:
            raise RuntimeError("canary-repr")

    with _configured_logging() as logger:
        logger.info(
            "failure safe",
            extra={"event": "formatter_failure", "payload": ExplodingMapping()},
        )

    output = capsys.readouterr().err
    assert "canary" not in output
    if output:
        assert json.loads(output)["event"] == "formatter_failure"


@pytest.mark.asyncio
async def test_job_success_log_inherits_request_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        await progress(25)
        return {"ok": True}

    with log_context(request_id="request-success"):
        job = await runner.submit(
            JobOperation.EXTRACT_INGREDIENTS,
            "session-success",
            worker,
        )
    await runner.wait(job.id)

    completed = next(
        record for record in _records(capsys) if record["event"] == "job_completed"
    )
    assert completed["request_id"] == "request-success"
    assert completed["job_id"] == job.id
    assert completed["session_id"] == "session-success"
    assert completed["operation"] == "extract_ingredients"
    assert completed["final_status"] == "succeeded"
    assert completed["duration_ms"] >= 0
    assert "error_code" not in completed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            AppError(
                code=ErrorCode.OLLAMA_UNAVAILABLE,
                message="Ollama is temporarily unavailable.",
                status_code=503,
                retryable=True,
            ),
            "ollama_unavailable",
        ),
        (
            RuntimeError("provider_token=canary-provider-token"),
            "internal_error",
        ),
    ],
)
async def test_job_failure_logs_only_a_safe_error_code(
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_code: str,
) -> None:
    configure_logging("INFO")
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        raise error

    with log_context(request_id="request-failure"):
        job = await runner.submit(
            JobOperation.GENERATE_RECIPES,
            "session-failure",
            worker,
        )
    await runner.wait(job.id)

    output = capsys.readouterr().err
    completed = next(
        record
        for record in map(json.loads, output.splitlines())
        if record["event"] == "job_completed"
    )
    assert completed["request_id"] == "request-failure"
    assert completed["job_id"] == job.id
    assert completed["session_id"] == "session-failure"
    assert completed["operation"] == "generate_recipes"
    assert completed["final_status"] == "failed"
    assert completed["error_code"] == expected_code
    assert "canary" not in output


@pytest.mark.asyncio
async def test_job_cancellation_emits_a_safe_terminal_log(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1)
    started = asyncio.Event()

    async def worker(progress):
        started.set()
        await asyncio.Event().wait()
        return {}

    with log_context(request_id="request-cancel"):
        job = await runner.submit(
            JobOperation.GENERATE_OPTIONS,
            "session-cancel",
            worker,
        )
    await started.wait()
    runner._tasks[job.id].cancel()
    await asyncio.gather(runner.wait(job.id), return_exceptions=True)

    completed = next(
        record for record in _records(capsys) if record["event"] == "job_completed"
    )
    assert completed["request_id"] == "request-cancel"
    assert completed["job_id"] == job.id
    assert completed["final_status"] == "cancelled"
    assert completed["error_code"] == "cancelled"


@pytest.mark.asyncio
async def test_job_cancellation_while_recording_failure_is_logged_as_cancelled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BlockingFailureStore(JobStore):
        def __init__(self) -> None:
            super().__init__(ttl_seconds=21_600)
            self.failure_started = asyncio.Event()

        async def mark_failed(self, job_id, error):
            self.failure_started.set()
            await asyncio.Event().wait()

    configure_logging("INFO")
    store = BlockingFailureStore()
    runner = JobRunner(store, max_concurrent_jobs=1)

    async def worker(progress):
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Ollama is temporarily unavailable.",
            status_code=503,
            retryable=True,
        )

    job = await runner.submit(
        JobOperation.EXTRACT_INGREDIENTS,
        "session-cancel-failure",
        worker,
    )
    await store.failure_started.wait()
    runner._tasks[job.id].cancel()
    await asyncio.gather(runner.wait(job.id), return_exceptions=True)

    completed = next(
        record for record in _records(capsys) if record["event"] == "job_completed"
    )
    assert completed["final_status"] == "cancelled"
    assert completed["error_code"] == "cancelled"


@pytest.mark.asyncio
async def test_job_overload_rejection_is_logged_without_creating_a_job(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    store = JobStore(ttl_seconds=21_600)
    runner = JobRunner(store, max_concurrent_jobs=1, max_queued_jobs=0)
    started = asyncio.Event()
    release = asyncio.Event()

    async def worker(progress):
        started.set()
        await release.wait()
        return {}

    first = await runner.submit(
        JobOperation.EXTRACT_INGREDIENTS,
        "session-active",
        worker,
    )
    await started.wait()
    with (
        log_context(request_id="request-busy"),
        pytest.raises(AppError, match="service is busy"),
    ):
        await runner.submit(
            JobOperation.GENERATE_OPTIONS,
            "session-rejected",
            worker,
        )

    rejected = next(
        record for record in _records(capsys) if record["event"] == "job_rejected"
    )
    assert rejected["request_id"] == "request-busy"
    assert rejected["session_id"] == "session-rejected"
    assert rejected["operation"] == "generate_options"
    assert rejected["final_status"] == "rejected"
    assert rejected["error_code"] == "service_busy"
    assert rejected["duration_ms"] >= 0
    assert "job_id" not in rejected

    release.set()
    await runner.wait(first.id)
