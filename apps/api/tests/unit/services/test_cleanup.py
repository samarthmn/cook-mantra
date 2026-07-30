import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domain.artifacts import ArtifactKind
from domain.jobs import JobOperation
from repositories.job_store import JobStore
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.cleanup import CleanupSupervisor, RuntimeCleaner


@pytest.mark.asyncio
async def test_run_once_removes_expired_state_and_expired_session_artifacts(
    project_tmp_path: Path,
) -> None:
    session_store = SessionStore(ttl_seconds=10)
    job_store = JobStore(ttl_seconds=10)
    artifact_root = project_tmp_path / "owned-runtime"
    sibling = project_tmp_path / "must-survive.txt"
    sibling.write_text("unrelated")
    artifact_store = ArtifactStore(artifact_root, ttl_seconds=10)
    await artifact_store.startup()
    session = await session_store.create()
    job = await job_store.create(JobOperation.EXTRACT_INGREDIENTS, session.id)
    session_artifact = await artifact_store.write(
        b"image-bytes",
        "image/png",
        ".png",
        owner_session_id=session.id,
        kind=ArtifactKind.INGREDIENT_UPLOAD,
    )
    independent_artifact = await artifact_store.write(
        b"independent-image-bytes",
        "image/png",
        ".png",
        owner_session_id="nonexistent-session",
        kind=ArtifactKind.DISH_PREVIEW,
    )
    cleanup = RuntimeCleaner(
        session_store,
        job_store,
        artifact_store,
        interval_seconds=60,
    )

    result = await cleanup.run_once(now=datetime.now(UTC) + timedelta(seconds=11))

    assert result.sessions_removed == 1
    assert result.jobs_removed == 1
    assert result.artifacts_removed == 2
    assert await session_store.get(session.id) is None
    assert await job_store.get(job.id) is None
    assert not session_artifact.path.exists()
    assert not independent_artifact.path.exists()
    assert sibling.read_text() == "unrelated"
    with pytest.raises(FrozenInstanceError):
        result.sessions_removed = 99
    await artifact_store.shutdown()


class TimestampRecordingSessionStore:
    def __init__(self, timestamps: list[datetime]) -> None:
        self._timestamps = timestamps

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        assert now is not None
        self._timestamps.append(now)
        return []


class TimestampRecordingJobStore:
    def __init__(self, timestamps: list[datetime]) -> None:
        self._timestamps = timestamps

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        assert now is not None
        self._timestamps.append(now)
        return []


class TimestampRecordingArtifactStore:
    def __init__(self, timestamps: list[datetime]) -> None:
        self._timestamps = timestamps

    async def delete_for_session(self, session_id: str) -> list[str]:
        raise AssertionError(f"Unexpected expired session: {session_id}")

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        assert now is not None
        self._timestamps.append(now)
        return []


@pytest.mark.asyncio
async def test_run_once_uses_one_utc_timestamp_for_every_repository() -> None:
    timestamps: list[datetime] = []
    cleaner = RuntimeCleaner(
        TimestampRecordingSessionStore(timestamps),
        TimestampRecordingJobStore(timestamps),
        TimestampRecordingArtifactStore(timestamps),
        interval_seconds=60,
    )
    now = datetime(2026, 7, 31, 10, 30, tzinfo=UTC)

    await cleaner.run_once(now)

    assert timestamps == [now, now, now]


class RecordingSessionStore:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        self._events.append("sessions.delete_expired")
        return ["expired-session"]


class RecordingJobStore:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        self._events.append("jobs.delete_expired")
        return []


class RecordingArtifactStore:
    def __init__(
        self,
        events: list[str],
        completed_runs: asyncio.Queue[None],
    ) -> None:
        self._events = events
        self._completed_runs = completed_runs

    async def delete_for_session(self, session_id: str) -> list[str]:
        self._events.append(f"artifacts.delete_for_session:{session_id}")
        return []

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        self._events.append("artifacts.delete_expired")
        self._completed_runs.put_nowait(None)
        return []


@pytest.mark.asyncio
async def test_supervisor_runs_can_be_woken_and_stops_deterministically() -> None:
    events: list[str] = []
    completed_runs: asyncio.Queue[None] = asyncio.Queue()
    cleanup = CleanupSupervisor(
        RecordingSessionStore(events),
        RecordingJobStore(events),
        RecordingArtifactStore(events, completed_runs),
        interval_seconds=3_600,
    )

    await cleanup.startup()
    owned_task = cleanup.task
    await cleanup.startup()
    assert cleanup.task is owned_task
    assert owned_task is not None
    await asyncio.wait_for(completed_runs.get(), timeout=1)
    cleanup.request_cleanup()
    await asyncio.wait_for(completed_runs.get(), timeout=1)
    await cleanup.shutdown()
    events_after_shutdown = list(events)
    cleanup.request_cleanup()
    await asyncio.sleep(0)

    assert events == events_after_shutdown
    assert owned_task.done()
    assert (
        events
        == [
            "sessions.delete_expired",
            "artifacts.delete_for_session:expired-session",
            "jobs.delete_expired",
            "artifacts.delete_expired",
        ]
        * 2
    )
