"""Safe, temporary filesystem storage for generated image artifacts."""

import asyncio
import errno
import os
import stat
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on non-POSIX platforms.
    fcntl = None

from core.errors import AppError, ErrorCode
from domain.artifacts import Artifact

_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_MISSING = "missing"
_SAFE = "safe"
_UNSAFE = "unsafe"
_UNSAFE_DIRECTORY = "unsafe_directory"
_PATH_MAX = 1024


class _CapabilityError(Exception):
    """Raised when secure descriptor operations are unavailable."""


class ArtifactStore:
    """Store runtime-owned artifacts beneath one dedicated directory."""

    def __init__(self, artifact_root: Path, ttl_seconds: int) -> None:
        self._root = artifact_root.absolute()
        self._root_fd: int | None = None
        self._ttl_seconds = ttl_seconds
        self._artifacts: dict[str, Artifact] = {}
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        """Prepare an empty artifact namespace for this API process."""
        async with self._lock:
            try:
                root_fd, root_path = await asyncio.to_thread(self._open_and_clear_root)
            except _storage_errors() as error:
                raise _artifact_failure(
                    "Temporary artifact storage is unavailable."
                ) from error

            old_root_fd = self._root_fd
            self._root_fd = root_fd
            self._root = root_path
            self._artifacts.clear()
            if old_root_fd is not None:
                try:
                    await asyncio.to_thread(os.close, old_root_fd)
                except OSError as error:
                    raise _artifact_failure(
                        "Temporary artifact storage is unavailable."
                    ) from error

    async def shutdown(self) -> None:
        """Release the owned directory descriptor when the store stops."""
        async with self._lock:
            root_fd = self._root_fd
            self._root_fd = None
            self._artifacts.clear()
            if root_fd is None:
                return
            try:
                await asyncio.to_thread(os.close, root_fd)
            except OSError as error:
                raise _artifact_failure(
                    "Temporary artifact storage is unavailable."
                ) from error

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
            root_fd = self._require_root_fd()
            artifact_id = str(uuid4())
            filename = f"{artifact_id}{suffix}"
            try:
                await asyncio.to_thread(self._write_new_file, root_fd, filename, data)
                root_path = await asyncio.to_thread(self._root_path_from_fd, root_fd)
            except _storage_errors() as error:
                await self._remove_after_write_failure(root_fd, filename)
                raise _artifact_failure("The artifact could not be stored.") from error

            now = datetime.now(UTC)
            try:
                artifact = Artifact(
                    id=artifact_id,
                    path=root_path / filename,
                    media_type=media_type,
                    owner_session_id=owner_session_id,
                    created_at=now,
                    last_accessed_at=now,
                )
            except Exception as error:
                await self._remove_after_write_failure(root_fd, filename)
                raise _artifact_failure("The artifact could not be stored.") from error

            self._artifacts[artifact.id] = artifact
            return artifact.model_copy(deep=True)

    async def require(self, artifact_id: str) -> Artifact:
        """Return detached artifact metadata or raise the standard not-found error."""
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise _artifact_not_found()

            root_fd = self._require_root_fd()
            try:
                file_state = await asyncio.to_thread(
                    self._inspect_file, root_fd, artifact.path.name
                )
            except _storage_errors() as error:
                raise _artifact_failure(
                    "The artifact could not be accessed."
                ) from error

            if file_state == _MISSING:
                del self._artifacts[artifact_id]
                raise _artifact_not_found()
            if file_state == _UNSAFE_DIRECTORY:
                del self._artifacts[artifact_id]
                raise _artifact_not_found()
            if file_state == _UNSAFE:
                try:
                    await asyncio.to_thread(
                        self._unlink_file, root_fd, artifact.path.name
                    )
                except _storage_errors() as error:
                    raise _artifact_failure(
                        "The artifact could not be accessed."
                    ) from error
                del self._artifacts[artifact_id]
                raise _artifact_not_found()

            try:
                artifact.path = (
                    await asyncio.to_thread(self._root_path_from_fd, root_fd)
                ) / artifact.path.name
            except _storage_errors() as error:
                raise _artifact_failure(
                    "The artifact could not be accessed."
                ) from error
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

    def _open_and_clear_root(self) -> tuple[int, Path]:
        parent_fd, root_name = self._open_parent_directory()
        root_fd: int | None = None
        try:
            with suppress(FileExistsError):
                os.mkdir(root_name, mode=0o700, dir_fd=parent_fd)
            root_fd = self._open_directory(root_name, parent_fd)
            root_path = self._root_path_from_fd(root_fd)
            self._clear_runtime_files(root_fd)
            return root_fd, root_path
        except BaseException:
            if root_fd is not None:
                os.close(root_fd)
            raise
        finally:
            os.close(parent_fd)

    def _open_parent_directory(self) -> tuple[int, str]:
        parts = self._root.parts
        if not self._root.is_absolute() or len(parts) < 2:
            raise _CapabilityError("Artifact root must be an absolute directory.")

        directory_fd = self._open_directory(parts[0])
        try:
            for component in parts[1:-1]:
                try:
                    next_fd = self._open_directory(component, directory_fd)
                except FileNotFoundError:
                    os.mkdir(component, mode=0o700, dir_fd=directory_fd)
                    next_fd = self._open_directory(component, directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            return directory_fd, parts[-1]
        except BaseException:
            os.close(directory_fd)
            raise

    @staticmethod
    def _open_directory(name: str, directory_fd: int | None = None) -> int:
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise _CapabilityError("Secure descriptor operations are unavailable.")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if directory_fd is None:
            return os.open(name, flags)
        return os.open(name, flags, dir_fd=directory_fd)

    @staticmethod
    def _root_path_from_fd(root_fd: int) -> Path:
        if fcntl is None or not hasattr(fcntl, "F_GETPATH"):
            raise _CapabilityError("Descriptor paths are unavailable.")
        raw_path = fcntl.fcntl(root_fd, fcntl.F_GETPATH, b"\0" * _PATH_MAX)
        path = Path(os.fsdecode(raw_path).split("\0", maxsplit=1)[0])
        if not path.is_absolute():
            raise _CapabilityError("Descriptor path is invalid.")
        return path

    def _clear_runtime_files(self, root_fd: int) -> None:
        for filename in os.listdir(root_fd):
            if Path(filename).suffix not in _ALLOWED_SUFFIXES:
                continue
            mode = os.stat(filename, dir_fd=root_fd, follow_symlinks=False).st_mode
            if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                os.unlink(filename, dir_fd=root_fd)

    @staticmethod
    def _write_new_file(root_fd: int, filename: str, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(filename, flags, 0o600, dir_fd=root_fd)
        try:
            with os.fdopen(descriptor, "wb") as artifact_file:
                artifact_file.write(data)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(filename, dir_fd=root_fd)
            raise

    @staticmethod
    def _inspect_file(root_fd: int, filename: str) -> str:
        try:
            mode = os.stat(filename, dir_fd=root_fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return _MISSING
        if stat.S_ISDIR(mode):
            return _UNSAFE_DIRECTORY
        if not stat.S_ISREG(mode):
            return _UNSAFE

        try:
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        except FileNotFoundError:
            return _MISSING
        except OSError as error:
            if error.errno == errno.ELOOP:
                return _UNSAFE
            raise
        try:
            return _SAFE if stat.S_ISREG(os.fstat(descriptor).st_mode) else _UNSAFE
        finally:
            os.close(descriptor)

    async def _delete_artifact(self, artifact: Artifact) -> None:
        try:
            await asyncio.to_thread(
                self._unlink_file, self._require_root_fd(), artifact.path.name
            )
        except _storage_errors() as error:
            raise _artifact_failure("The artifact could not be deleted.") from error

    async def _remove_after_write_failure(self, root_fd: int, filename: str) -> None:
        try:
            await asyncio.to_thread(self._unlink_file, root_fd, filename)
        except _storage_errors() as error:
            raise _artifact_failure("The artifact could not be stored.") from error

    @staticmethod
    def _unlink_file(root_fd: int, filename: str) -> None:
        with suppress(FileNotFoundError):
            os.unlink(filename, dir_fd=root_fd)

    def _require_root_fd(self) -> int:
        if self._root_fd is None:
            raise _artifact_failure("Temporary artifact storage is unavailable.")
        return self._root_fd


def _storage_errors() -> tuple[type[BaseException], ...]:
    return (OSError, AttributeError, NotImplementedError, TypeError, _CapabilityError)


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
