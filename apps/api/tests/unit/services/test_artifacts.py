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
