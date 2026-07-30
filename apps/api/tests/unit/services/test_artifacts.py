import errno
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.errors import AppError, ErrorCode
from services import artifacts as artifacts_service
from services.artifacts import ArtifactStore


@pytest.mark.asyncio
async def test_artifact_names_ignore_client_filenames(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()

    artifact = await store.write(
        b"image-bytes",
        "image/png",
        ".png",
        owner_session_id="session-1",
    )

    assert artifact.path.parent == tmp_path
    assert artifact.path.name == f"{artifact.id}.png"
    assert artifact.path.read_bytes() == b"image-bytes"


@pytest.mark.asyncio
async def test_startup_removes_previous_runtime_files(tmp_path: Path) -> None:
    stale_path = tmp_path / "stale.png"
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_bytes(b"stale")

    await ArtifactStore(tmp_path, ttl_seconds=60).startup()

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_write_rejects_a_suffix_outside_the_image_allowlist(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()

    with pytest.raises(AppError) as raised:
        await store.write(
            b"not-an-image",
            "image/svg+xml",
            ".svg",
            owner_session_id="session-1",
        )

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_require_returns_isolated_metadata_and_missing_artifacts_are_not_found(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )

    required = await store.require(artifact.id)
    required.owner_session_id = "other-session"

    assert (await store.require(artifact.id)).owner_session_id == "session-1"

    with pytest.raises(AppError) as raised:
        await store.require("missing-artifact")

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert raised.value.status_code == 404
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_delete_for_session_only_removes_owned_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    first = await store.write(
        b"first", "image/png", ".png", owner_session_id="session-1"
    )
    second = await store.write(
        b"second", "image/jpeg", ".jpg", owner_session_id="session-2"
    )
    foreign_path = tmp_path / "foreign.png"
    foreign_path.write_bytes(b"foreign")

    removed = await store.delete_for_session("session-1")

    assert removed == [first.id]
    assert not first.path.exists()
    assert second.path.read_bytes() == b"second"
    assert foreign_path.read_bytes() == b"foreign"


@pytest.mark.asyncio
async def test_delete_removes_only_the_requested_owned_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    first = await store.write(
        b"first", "image/png", ".png", owner_session_id="session-1"
    )
    second = await store.write(
        b"second", "image/png", ".png", owner_session_id="session-1"
    )

    await store.delete(first.id)

    assert not first.path.exists()
    assert (await store.require(second.id)).path == second.path


@pytest.mark.asyncio
async def test_expired_artifacts_are_removed_from_metadata_and_disk(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=10)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )

    removed = await store.delete_expired(now=datetime.now(UTC) + timedelta(seconds=11))

    assert removed == [artifact.id]
    assert not artifact.path.exists()
    with pytest.raises(AppError, match="Artifact was not found"):
        await store.require(artifact.id)


@pytest.mark.asyncio
async def test_require_rejects_an_artifact_path_replaced_with_an_escape_symlink(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )
    outside_path = tmp_path.parent / "outside-image.png"
    outside_path.write_bytes(b"outside")
    artifact.path.unlink()
    artifact.path.symlink_to(outside_path)

    with pytest.raises(AppError) as raised:
        await store.require(artifact.id)

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert outside_path.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_require_treats_a_replaced_directory_as_an_unsafe_missing_artifact(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )
    artifact.path.unlink()
    artifact.path.mkdir()

    with pytest.raises(AppError) as raised:
        await store.require(artifact.id)

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert artifact.path.is_dir()


@pytest.mark.asyncio
async def test_root_relative_operations_resist_root_and_leaf_symlink_replacement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.png"
    sentinel.write_bytes(b"outside")
    store = ArtifactStore(root, ttl_seconds=60)
    await store.startup()

    retained_root = tmp_path / "retained-artifacts"
    root.rename(retained_root)
    root.symlink_to(outside, target_is_directory=True)

    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )
    retained_path = retained_root / artifact.path.name
    retained_path.unlink()
    retained_path.symlink_to(sentinel)

    await store.delete(artifact.id)

    assert not retained_path.is_symlink()
    assert sentinel.read_bytes() == b"outside"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inspection_error",
    [PermissionError("inspection blocked"), OSError(errno.EIO, "transient I/O")],
)
async def test_require_preserves_owned_metadata_on_operational_inspection_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspection_error: OSError,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )

    def raise_inspection_error(*_: object, **__: object) -> int:
        raise inspection_error

    with monkeypatch.context() as patch:
        patch.setattr(artifacts_service.os, "open", raise_inspection_error)
        with pytest.raises(AppError) as raised:
            await store.require(artifact.id)

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert (await store.require(artifact.id)).id == artifact.id


@pytest.mark.asyncio
async def test_write_cleans_up_a_partially_created_file_after_a_write_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()

    def create_then_fail(root_fd: int, filename: str, _: bytes) -> None:
        descriptor = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=root_fd,
        )
        os.close(descriptor)
        raise OSError(errno.EIO, "write failed")

    monkeypatch.setattr(
        ArtifactStore, "_write_new_file", staticmethod(create_then_fail)
    )

    with pytest.raises(AppError) as raised:
        await store.write(
            b"image-bytes", "image/png", ".png", owner_session_id="session-1"
        )

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_write_cleans_up_file_when_metadata_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()

    def fail_metadata_construction(**_: object) -> None:
        raise ValueError("metadata failed")

    monkeypatch.setattr(artifacts_service, "Artifact", fail_metadata_construction)

    with pytest.raises(AppError) as raised:
        await store.write(
            b"image-bytes", "image/png", ".png", owner_session_id="session-1"
        )

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_startup_rejects_a_replaced_intermediate_parent_symlink(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "replaceable-parent"
    parent.mkdir()
    root = parent / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.png"
    sentinel.write_bytes(b"outside")
    store = ArtifactStore(root, ttl_seconds=60)

    retained_parent = tmp_path / "retained-parent"
    parent.rename(retained_parent)
    parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AppError) as raised:
        await store.startup()

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert sentinel.read_bytes() == b"outside"
    assert not (outside / "artifacts").exists()


@pytest.mark.asyncio
async def test_require_returns_the_anchored_path_after_root_and_leaf_replacement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ArtifactStore(root, ttl_seconds=60)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )

    retained_root = tmp_path / "retained-artifacts"
    root.rename(retained_root)
    sentinel = outside / artifact.path.name
    sentinel.write_bytes(b"outside")
    root.symlink_to(outside, target_is_directory=True)

    required = await store.require(artifact.id)

    assert required.path.parent == retained_root
    assert required.path.read_bytes() == b"image-bytes"

    required.path.unlink()
    required.path.symlink_to(sentinel)
    with pytest.raises(AppError) as raised:
        await store.require(artifact.id)

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert sentinel.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_shutdown_closes_descriptor_is_idempotent_and_disables_operations(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    root_fd = store._root_fd

    assert root_fd is not None
    await store.shutdown()
    await store.shutdown()

    with pytest.raises(OSError):
        os.fstat(root_fd)
    with pytest.raises(AppError) as raised:
        await store.write(
            b"image-bytes", "image/png", ".png", owner_session_id="session-1"
        )

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE


@pytest.mark.asyncio
async def test_repeated_startup_releases_the_previous_descriptor(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    first_fd = store._root_fd
    await store.startup()
    second_fd = store._root_fd

    assert first_fd is not None
    assert second_fd is not None
    with pytest.raises(OSError):
        os.fstat(first_fd)

    await store.shutdown()

    with pytest.raises(OSError):
        os.fstat(second_fd)


@pytest.mark.asyncio
async def test_startup_maps_missing_descriptor_capabilities_to_artifact_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)

    with monkeypatch.context() as patch:
        patch.delattr(artifacts_service.os, "O_DIRECTORY")
        with pytest.raises(AppError) as raised:
            await store.startup()

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE


@pytest.mark.asyncio
async def test_startup_maps_unimplemented_descriptor_operations_to_artifact_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)

    def raise_not_implemented(*_: object, **__: object) -> int:
        raise NotImplementedError

    with monkeypatch.context() as patch:
        patch.setattr(artifacts_service.os, "open", raise_not_implemented)
        with pytest.raises(AppError) as raised:
            await store.startup()

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE


@pytest.mark.asyncio
async def test_shutdown_records_a_close_failure_without_retrying_the_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    root_fd = store._root_fd

    assert root_fd is not None
    actual_close = os.close
    close_attempts: list[int] = []

    def fail_root_close(descriptor: int) -> None:
        close_attempts.append(descriptor)
        if descriptor == root_fd:
            raise OSError(errno.EIO, "close failed")
        actual_close(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(artifacts_service.os, "close", fail_root_close)
        with pytest.raises(AppError) as first:
            await store.shutdown()
        with pytest.raises(AppError) as second:
            await store.shutdown()

    assert first.value.code is ErrorCode.ARTIFACT_FAILURE
    assert second.value.code is ErrorCode.ARTIFACT_FAILURE
    assert close_attempts == [root_fd]
    assert root_fd in store._unresolved_close_fds
    actual_close(root_fd)


@pytest.mark.parametrize(
    "next_close_fails",
    [False, True],
    ids=["cleanup-succeeds", "cleanup-fails"],
)
def test_open_parent_directory_accounts_for_both_descriptors_when_handoff_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    next_close_fails: bool,
) -> None:
    store = ArtifactStore(Path("/intermediate/artifacts"), ttl_seconds=60)
    previous_fd = 101
    next_fd = 102
    descriptors = iter((previous_fd, next_fd))
    close_attempts: list[int] = []
    closed_descriptors: list[int] = []

    def open_directory(_: str, __: int | None = None) -> int:
        return next(descriptors)

    def fail_previous_close(descriptor: int) -> None:
        close_attempts.append(descriptor)
        if descriptor == previous_fd:
            raise OSError(errno.EIO, "previous close failed")
        if next_close_fails:
            raise OSError(errno.EIO, "next close failed")
        closed_descriptors.append(descriptor)

    monkeypatch.setattr(store, "_open_directory", open_directory)
    monkeypatch.setattr(artifacts_service.os, "close", fail_previous_close)

    with pytest.raises(OSError, match="previous close failed"):
        store._open_parent_directory()

    assert close_attempts == [previous_fd, next_fd]
    assert closed_descriptors == ([] if next_close_fails else [next_fd])
    expected_unresolved = {previous_fd, next_fd} if next_close_fails else {previous_fd}
    assert store._unresolved_close_fds == expected_unresolved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_name", "operation_args"),
    [
        ("require", ("missing-artifact",)),
        ("delete", ("missing-artifact",)),
        ("delete_for_session", ("missing-session",)),
        ("delete_expired", ()),
    ],
)
async def test_unresolved_close_state_precedes_public_resource_lookup(
    tmp_path: Path,
    operation_name: str,
    operation_args: tuple[object, ...],
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    root_fd = store._root_fd

    assert root_fd is not None
    store._unresolved_close_fds.add(root_fd + 10_000)

    try:
        operation = getattr(store, operation_name)
        with pytest.raises(AppError) as raised:
            await operation(*operation_args)
    finally:
        store._unresolved_close_fds.clear()
        await store.shutdown()

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert raised.value.message == "Temporary artifact storage is unavailable."
    assert raised.value.status_code == 500
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_startup_does_not_publish_state_when_closing_previous_descriptor_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=60)
    await store.startup()
    artifact = await store.write(
        b"image-bytes", "image/png", ".png", owner_session_id="session-1"
    )
    old_fd = store._root_fd
    new_fd: int | None = None
    actual_open_and_clear = store._open_and_clear_root
    actual_close = os.close

    assert old_fd is not None

    def capture_new_descriptor() -> tuple[int, Path]:
        nonlocal new_fd
        new_fd, root_path = actual_open_and_clear()
        return new_fd, root_path

    def fail_old_close(descriptor: int) -> None:
        if descriptor == old_fd:
            raise OSError(errno.EIO, "close failed")
        actual_close(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_open_and_clear_root", capture_new_descriptor)
        patch.setattr(artifacts_service.os, "close", fail_old_close)
        with pytest.raises(AppError) as raised:
            await store.startup()

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert store._root_fd is None
    assert artifact.id in store._artifacts
    assert old_fd in store._unresolved_close_fds
    assert new_fd is not None
    with pytest.raises(OSError):
        os.fstat(new_fd)
    actual_close(old_fd)


def test_open_and_clear_root_closes_root_when_parent_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    store = ArtifactStore(tmp_path / "artifacts", ttl_seconds=60)
    opened_root_fd: int | None = None
    actual_open = os.open
    actual_close = os.close

    def return_parent() -> tuple[int, str]:
        return parent_fd, "artifacts"

    def capture_root_open(*args: object, **kwargs: object) -> int:
        nonlocal opened_root_fd
        descriptor = actual_open(*args, **kwargs)
        if args[0] == "artifacts":
            opened_root_fd = descriptor
        return descriptor

    def fail_parent_close(descriptor: int) -> None:
        if descriptor == parent_fd:
            raise OSError(errno.EIO, "parent close failed")
        actual_close(descriptor)

    monkeypatch.setattr(store, "_open_parent_directory", return_parent)
    monkeypatch.setattr(artifacts_service.os, "open", capture_root_open)
    monkeypatch.setattr(artifacts_service.os, "close", fail_parent_close)

    with pytest.raises(OSError, match="parent close failed"):
        store._open_and_clear_root()

    assert opened_root_fd is not None
    with pytest.raises(OSError):
        os.fstat(opened_root_fd)
    actual_close(parent_fd)


@pytest.mark.asyncio
async def test_startup_tolerates_a_concurrent_safe_intermediate_directory_creator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intermediate = tmp_path / "concurrent-parent"
    store = ArtifactStore(intermediate / "artifacts", ttl_seconds=60)
    original_open_directory = ArtifactStore._open_directory
    injected = False

    def create_then_report_missing(
        name: str,
        directory_fd: int | None = None,
    ) -> int:
        nonlocal injected
        if name == intermediate.name and directory_fd is not None and not injected:
            injected = True
            intermediate.mkdir()
            raise FileNotFoundError
        return original_open_directory(name, directory_fd)

    monkeypatch.setattr(
        ArtifactStore, "_open_directory", staticmethod(create_then_report_missing)
    )

    await store.startup()

    assert intermediate.is_dir()
    await store.shutdown()


@pytest.mark.asyncio
async def test_startup_rejects_a_concurrent_intermediate_symlink_creator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intermediate = tmp_path / "concurrent-parent"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ArtifactStore(intermediate / "artifacts", ttl_seconds=60)
    original_open_directory = ArtifactStore._open_directory
    injected = False

    def symlink_then_report_missing(
        name: str,
        directory_fd: int | None = None,
    ) -> int:
        nonlocal injected
        if name == intermediate.name and directory_fd is not None and not injected:
            injected = True
            intermediate.symlink_to(outside, target_is_directory=True)
            raise FileNotFoundError
        return original_open_directory(name, directory_fd)

    monkeypatch.setattr(
        ArtifactStore, "_open_directory", staticmethod(symlink_then_report_missing)
    )

    with pytest.raises(AppError) as raised:
        await store.startup()

    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert not (outside / "artifacts").exists()
