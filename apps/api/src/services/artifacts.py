"""Safe, temporary filesystem storage for generated image artifacts."""

import asyncio
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core.errors import AppError, ErrorCode
from domain.artifacts import Artifact

_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ArtifactStore:
    """Store runtime-owned artifacts beneath one dedicated directory."""

    def __init__(self, artifact_root: Path, ttl_seconds: int) -> None:
        self._root = artifact_root.resolve()
        self._ttl_seconds = ttl_seconds
        self._artifacts: dict[str, Artifact] = {}
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        """Prepare an empty artifact namespace for this API process."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._clear_runtime_files)
            except OSError as error:
                raise _artifact_failure(
                    "Temporary artifact storage is unavailable."
                ) from error
            self._artifacts.clear()

    async def write(
        self,
        data: bytes,
        media_type: str,
        suffix: str,
        owner_session_id: str,
    ) -> Artifact:
        """Write a generated image using a server-created name."""
        if suffix not in _ALLOWED_SUFFIXES:
            raise _artifact_failure("The artifact format is not supported.")

        async with self._lock:
            artifact_id = str(uuid4())
            path = await asyncio.to_thread(self._path_for, artifact_id, suffix)
            try:
                await asyncio.to_thread(self._write_new_file, path, data)
            except OSError as error:
                raise _artifact_failure("The artifact could not be stored.") from error

            now = datetime.now(UTC)
            artifact = Artifact(
                id=artifact_id,
                path=path,
                media_type=media_type,
                owner_session_id=owner_session_id,
                created_at=now,
                last_accessed_at=now,
            )
            self._artifacts[artifact.id] = artifact
            return artifact.model_copy(deep=True)

    async def require(self, artifact_id: str) -> Artifact:
        """Return detached artifact metadata or raise the standard not-found error."""
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise _artifact_not_found()

            if not await asyncio.to_thread(self._is_safe_existing_file, artifact.path):
                del self._artifacts[artifact_id]
                raise _artifact_not_found()

            artifact.last_accessed_at = datetime.now(UTC)
            return artifact.model_copy(deep=True)

    async def delete(self, artifact_id: str) -> None:
        """Delete one artifact when it belongs to this runtime namespace."""
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                return
            await self._delete_artifact(artifact)
            del self._artifacts[artifact_id]

    async def delete_for_session(self, session_id: str) -> list[str]:
        """Delete only artifacts that were created for the given session."""
        async with self._lock:
            artifacts = [
                artifact
                for artifact in self._artifacts.values()
                if artifact.owner_session_id == session_id
            ]
            for artifact in artifacts:
                await self._delete_artifact(artifact)
                del self._artifacts[artifact.id]
            return [artifact.id for artifact in artifacts]

    async def delete_expired(self, now: datetime | None = None) -> list[str]:
        """Delete artifacts that have not been accessed within the configured TTL."""
        expiry_time = now or datetime.now(UTC)
        async with self._lock:
            artifacts = [
                artifact
                for artifact in self._artifacts.values()
                if (expiry_time - artifact.last_accessed_at).total_seconds()
                > self._ttl_seconds
            ]
            for artifact in artifacts:
                await self._delete_artifact(artifact)
                del self._artifacts[artifact.id]
            return [artifact.id for artifact in artifacts]

    def _clear_runtime_files(self) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in self._root.iterdir():
            mode = path.lstat().st_mode
            if path.suffix in _ALLOWED_SUFFIXES and (
                stat.S_ISREG(mode) or stat.S_ISLNK(mode)
            ):
                path.unlink()

    def _path_for(self, artifact_id: str, suffix: str) -> Path:
        path = (self._root / f"{artifact_id}{suffix}").resolve(strict=False)
        if path.parent != self._root:
            raise _artifact_failure("The artifact path is invalid.")
        return path

    @staticmethod
    def _write_new_file(path: Path, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as artifact_file:
            artifact_file.write(data)

    async def _delete_artifact(self, artifact: Artifact) -> None:
        try:
            await asyncio.to_thread(self._unlink_owned_path, artifact.path)
        except OSError as error:
            raise _artifact_failure("The artifact could not be deleted.") from error

    def _unlink_owned_path(self, path: Path) -> None:
        if path.parent.resolve() != self._root:
            raise _artifact_failure("The artifact path is invalid.")
        path.unlink(missing_ok=True)

    def _is_safe_existing_file(self, path: Path) -> bool:
        try:
            return path.resolve(strict=True).parent == self._root and path.is_file()
        except OSError:
            return False


def _artifact_failure(message: str) -> AppError:
    return AppError(
        code=ErrorCode.ARTIFACT_FAILURE,
        message=message,
        status_code=500,
        retryable=False,
    )


def _artifact_not_found() -> AppError:
    return AppError(
        code=ErrorCode.RESOURCE_NOT_FOUND,
        message="Artifact was not found.",
        status_code=404,
        retryable=False,
    )
