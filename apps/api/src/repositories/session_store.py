"""Lock-protected in-memory storage for cooking sessions."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.errors import AppError, ErrorCode
from domain.sessions import Session, SessionStage

type PreCommitHook = Callable[[], Awaitable[None]]


class SessionStore:
    """Keep temporary session records safe across concurrent requests."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def create(self, stage: SessionStage = SessionStage.EXTRACTING) -> Session:
        """Create a new session at the requested workflow stage."""
        now = datetime.now(UTC)
        session = Session(
            id=str(uuid4()),
            stage=stage,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._sessions[session.id] = session.model_copy(deep=True)
        return session.model_copy(deep=True)

    async def get(self, session_id: str) -> Session | None:
        """Return a detached copy of a session when it exists."""
        async with self._lock:
            session = self._sessions.get(session_id)
            return session.model_copy(deep=True) if session is not None else None

    async def require(self, session_id: str) -> Session:
        """Return a detached session copy or raise the public not-found error."""
        async with self._lock:
            return self._require(session_id).model_copy(deep=True)

    async def replace(
        self,
        session: Session,
        *,
        before_commit: PreCommitHook | None = None,
    ) -> Session:
        """Atomically replace an existing session with updated state."""
        async with self._lock:
            current = self._require(session.id)
            if session.updated_at != current.updated_at:
                raise AppError(
                    code=ErrorCode.INVALID_SESSION_TRANSITION,
                    message="The session changed before this update could be applied.",
                    status_code=409,
                    retryable=True,
                    session_id=session.id,
                )
            payload = session.__dict__.copy()
            payload["created_at"] = current.created_at
            payload["updated_at"] = max(
                datetime.now(UTC),
                current.updated_at + timedelta(microseconds=1),
            )
            replacement = Session.model_validate(payload)
            detached_replacement = replacement.model_copy(deep=True)
            if before_commit is not None:
                await before_commit()
            self._sessions[session.id] = replacement
            return detached_replacement

    async def delete(self, session_id: str) -> None:
        """Delete one session when it exists."""
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        """Delete sessions that have been inactive longer than their TTL."""
        expiry_time = now or datetime.now(UTC)
        async with self._lock:
            expired_ids = [
                session_id
                for session_id, session in self._sessions.items()
                if (expiry_time - session.updated_at).total_seconds()
                > self._ttl_seconds
            ]
            for session_id in expired_ids:
                del self._sessions[session_id]
            return expired_ids

    def _require(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise AppError(
                code=ErrorCode.RESOURCE_NOT_FOUND,
                message="Session was not found.",
                status_code=404,
                retryable=False,
                session_id=session_id,
            )
        return session
