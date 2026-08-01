import asyncio
from pathlib import Path

import pytest

from api.app import create_app
from core.config import Settings
from domain.jobs import JobOperation


@pytest.mark.asyncio
async def test_lifespan_uses_configured_cleanup_and_drains_blocked_work(
    project_tmp_path: Path,
) -> None:
    artifact_root = project_tmp_path / "owned-artifacts"
    sibling = project_tmp_path / "sibling.txt"
    sibling.write_text("keep me")
    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=artifact_root,
            cleanup_interval_seconds=17,
            max_concurrent_jobs=1,
            max_queued_jobs=0,
            beast_base_url="http://beast.test:4900",
            beast_api_key="test-key",
        )
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker(progress):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async with app.router.lifespan_context(app):
        job = await app.state.job_runner.submit(
            JobOperation.EXTRACT_INGREDIENTS,
            "session-1",
            worker,
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        assert app.state.cleanup_supervisor.interval_seconds == 17
        assert app.state.cleanup_task is app.state.cleanup_supervisor.task
        assert app.state.cleanup_task.done() is False
        assert app.state.job_runner.active_count == 1
        assert job.id

    assert app.state.cleanup_task.done()
    assert app.state.job_runner.active_count == 0
    assert cancelled.is_set()
    assert sibling.read_text() == "keep me"
    assert app.state.artifact_store._root_fd is None
    assert app.state.owned_image_generator._client.is_closed


@pytest.mark.asyncio
async def test_client_close_failure_still_shuts_down_artifact_storage(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path / "close-failure",
            beast_base_url="http://beast.test:4900",
            beast_api_key="test-key",
        )
    )
    events: list[str] = []
    generator = app.state.owned_image_generator
    artifact_store = app.state.artifact_store
    original_artifact_shutdown = artifact_store.shutdown

    async def failing_close() -> None:
        events.append("client.close")
        await generator._client.aclose()
        raise RuntimeError("client close failed")

    async def recording_artifact_shutdown() -> None:
        events.append("artifact.shutdown")
        await original_artifact_shutdown()

    monkeypatch.setattr(generator, "aclose", failing_close)
    monkeypatch.setattr(artifact_store, "shutdown", recording_artifact_shutdown)

    with pytest.raises(RuntimeError, match="client close failed"):
        async with app.router.lifespan_context(app):
            pass

    assert events == ["client.close", "artifact.shutdown"]
    assert app.state.cleanup_task.done()
    assert app.state.job_runner.active_count == 0
    assert artifact_store._root_fd is None


@pytest.mark.asyncio
async def test_artifact_shutdown_failure_propagates_after_earlier_resources_drain(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path / "artifact-close-failure",
            beast_base_url="http://beast.test:4900",
            beast_api_key="test-key",
        )
    )
    artifact_store = app.state.artifact_store
    original_artifact_shutdown = artifact_store.shutdown

    async def failing_artifact_shutdown() -> None:
        await original_artifact_shutdown()
        raise RuntimeError("artifact shutdown failed")

    monkeypatch.setattr(artifact_store, "shutdown", failing_artifact_shutdown)

    with pytest.raises(RuntimeError, match="artifact shutdown failed"):
        async with app.router.lifespan_context(app):
            pass

    assert app.state.cleanup_task.done()
    assert app.state.job_runner.active_count == 0
    assert app.state.owned_image_generator._client.is_closed
    assert artifact_store._root_fd is None
