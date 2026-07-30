"""Application-owned cleanup for temporary in-memory and filesystem state."""

import asyncio
import logging
from datetime import UTC, datetime

from repositories.job_store import JobStore
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 60.0


class CleanupSupervisor:
    """Periodically expire runtime records and their owned artifacts."""

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
        self._stop_event = asyncio.Event()
        self._cleanup_requested = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def startup(self) -> None:
        """Start the owned periodic task once."""
        async with self._lifecycle_lock:
            if self._task is not None:
                return
            self._stop_event.clear()
            self._cleanup_requested.clear()
            self._task = asyncio.create_task(self._run())

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

    async def run_once(self, now: datetime | None = None) -> None:
        """Perform one deterministic expiry pass."""
        cleanup_time = now or datetime.now(UTC)
        expired_session_ids = await self._session_store.delete_expired(now=cleanup_time)
        for session_id in expired_session_ids:
            await self._artifact_store.delete_for_session(session_id)
        await self._job_store.delete_expired(now=cleanup_time)
        await self._artifact_store.delete_expired(now=cleanup_time)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Periodic runtime cleanup failed")

            if self._stop_event.is_set():
                return
            await self._wait_for_interval_or_request()

    async def _wait_for_interval_or_request(self) -> None:
        stop_wait = asyncio.create_task(self._stop_event.wait())
        request_wait = asyncio.create_task(self._cleanup_requested.wait())
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
            self._cleanup_requested.clear()
