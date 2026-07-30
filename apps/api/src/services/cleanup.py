"""Application-owned cleanup for temporary in-memory and filesystem state."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from repositories.job_store import JobStore
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Immutable counts from one runtime expiry pass."""

    sessions_removed: int
    jobs_removed: int
    artifacts_removed: int


class RuntimeCleaner:
    """Expire in-memory records and application-owned artifacts."""

    def __init__(
        self,
        session_store: SessionStore,
        job_store: JobStore,
        artifact_store: ArtifactStore,
        *,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._session_store = session_store
        self._job_store = job_store
        self._artifact_store = artifact_store
        self._interval_seconds = interval_seconds

    @property
    def interval_seconds(self) -> float:
        """Return the configured pause between periodic cleanup passes."""
        return self._interval_seconds

    async def run_once(self, now: datetime | None = None) -> CleanupResult:
        """Perform one deterministic expiry pass with a shared UTC timestamp."""
        cleanup_time = now or datetime.now(UTC)
        if cleanup_time.tzinfo is None:
            cleanup_time = cleanup_time.replace(tzinfo=UTC)
        else:
            cleanup_time = cleanup_time.astimezone(UTC)

        expired_session_ids = await self._session_store.delete_expired(now=cleanup_time)
        removed_artifact_ids: list[str] = []
        for session_id in expired_session_ids:
            removed_artifact_ids.extend(
                await self._artifact_store.delete_for_session(session_id)
            )
        expired_job_ids = await self._job_store.delete_expired(now=cleanup_time)
        removed_artifact_ids.extend(
            await self._artifact_store.delete_expired(now=cleanup_time)
        )
        return CleanupResult(
            sessions_removed=len(expired_session_ids),
            jobs_removed=len(expired_job_ids),
            artifacts_removed=len(removed_artifact_ids),
        )

    async def run(
        self,
        stop_event: asyncio.Event,
        *,
        cleanup_requested: asyncio.Event | None = None,
    ) -> None:
        """Run periodically until a stop event wakes the wait immediately."""
        while not stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Periodic runtime cleanup failed")

            if stop_event.is_set():
                return
            await self._wait_for_interval(
                stop_event,
                cleanup_requested=cleanup_requested,
            )

    async def _wait_for_interval(
        self,
        stop_event: asyncio.Event,
        *,
        cleanup_requested: asyncio.Event | None,
    ) -> None:
        if cleanup_requested is None:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                return
            return

        stop_wait = asyncio.create_task(stop_event.wait())
        request_wait = asyncio.create_task(cleanup_requested.wait())
        try:
            await asyncio.wait(
                {stop_wait, request_wait},
                timeout=self._interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (stop_wait, request_wait):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stop_wait, request_wait, return_exceptions=True)
            cleanup_requested.clear()


class CleanupSupervisor:
    """Own exactly one periodic RuntimeCleaner task for the application."""

    def __init__(
        self,
        session_store: SessionStore,
        job_store: JobStore,
        artifact_store: ArtifactStore,
        *,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._cleaner = RuntimeCleaner(
            session_store,
            job_store,
            artifact_store,
            interval_seconds=interval_seconds,
        )
        self._stop_event = asyncio.Event()
        self._cleanup_requested = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def interval_seconds(self) -> float:
        """Return the cleaner's configured periodic interval."""
        return self._cleaner.interval_seconds

    @property
    def task(self) -> asyncio.Task[None] | None:
        """Expose the current or most recently drained owned task."""
        return self._task or self._last_task

    async def startup(self) -> None:
        """Start the owned periodic task once."""
        async with self._lifecycle_lock:
            if self._task is not None:
                return
            self._stop_event.clear()
            self._cleanup_requested.clear()
            self._task = asyncio.create_task(self._run())
            self._last_task = self._task

    async def shutdown(self) -> None:
        """Wake and await the periodic task so no cleanup outlives storage."""
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            self._stop_event.set()
            self._cleanup_requested.set()

        await task

        async with self._lifecycle_lock:
            if self._task is task:
                self._task = None

    def request_cleanup(self) -> None:
        """Wake the supervisor without waiting for the periodic interval."""
        self._cleanup_requested.set()

    async def run_once(self, now: datetime | None = None) -> CleanupResult:
        """Perform one deterministic expiry pass."""
        return await self._cleaner.run_once(now)

    async def _run(self) -> None:
        await self._cleaner.run(
            self._stop_event,
            cleanup_requested=self._cleanup_requested,
        )
