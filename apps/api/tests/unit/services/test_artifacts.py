from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.errors import AppError, ErrorCode
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
