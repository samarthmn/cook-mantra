import asyncio
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from PIL import Image

from api.app import create_app
from core.config import Model, Settings
from domain.artifacts import Artifact
from domain.images import DishPreview, GeneratedImage, ImageGenerationRequest


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": [],
            "missing": [],
        }


class UnusedDishPreviewService:
    async def generate(
        self,
        session_id: str,
        option: object,
        progress: Callable[[int], Awaitable[None]],
    ) -> DishPreview:
        raise AssertionError("Artifact requests must not generate a dish preview.")


class CallerOwnedImageGenerator:
    def __init__(self) -> None:
        self.close_calls = 0

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> GeneratedImage:
        raise AssertionError("Application startup must not generate an image.")

    async def aclose(self) -> None:
        self.close_calls += 1


class FailingStartupCleanup:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def startup(self) -> None:
        raise RuntimeError("cleanup startup failed")

    async def shutdown(self) -> None:
        self.shutdown_called = True


class PausingCloseClient:
    def __init__(self, delegate: httpx.AsyncClient) -> None:
        self._delegate = delegate
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    @property
    def is_closed(self) -> bool:
        return self._delegate.is_closed

    async def aclose(self) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        await self._delegate.aclose()


def settings_for(artifact_root: Path) -> Settings:
    return Settings(_env_file=None, artifact_root=artifact_root)


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def client(project_tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        settings=settings_for(project_tmp_path / "artifact-route"),
        ollama_health=ReadyOllama(),
        dish_previews=UnusedDishPreviewService(),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def png_artifact(client: TestClient) -> Artifact:
    return client.portal.call(
        client.app.state.artifact_store.write,
        png_bytes(),
        "image/png",
        ".png",
        "session-1",
    )


def test_image_artifact_is_served_with_its_media_type(
    client: TestClient,
    png_artifact: Artifact,
) -> None:
    response = client.get(f"/api/v1/artifacts/{png_artifact.id}")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == png_artifact.path.read_bytes()


def test_serving_an_artifact_refreshes_its_last_access_time(
    client: TestClient,
    png_artifact: Artifact,
) -> None:
    response = client.get(f"/api/v1/artifacts/{png_artifact.id}")

    assert response.status_code == status.HTTP_200_OK
    stored = client.app.state.artifact_store._artifacts[png_artifact.id]
    assert stored.last_accessed_at > png_artifact.last_accessed_at


def test_missing_artifact_uses_standard_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/artifacts/missing",
        headers={"X-Request-ID": "req-missing-artifact"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "Artifact was not found.",
            "details": {},
            "retryable": False,
            "request_id": "req-missing-artifact",
            "session_id": None,
            "job_id": None,
        }
    }


def test_expired_artifact_is_not_served(
    client: TestClient,
    png_artifact: Artifact,
) -> None:
    removed = client.portal.call(
        client.app.state.artifact_store.delete_expired,
        datetime.now(UTC) + timedelta(days=1),
    )

    response = client.get(f"/api/v1/artifacts/{png_artifact.id}")

    assert removed == [png_artifact.id]
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "resource_not_found"
    assert response.json()["error"]["message"] == "Artifact was not found."


def test_unregistered_file_is_not_exposed_as_an_artifact(client: TestClient) -> None:
    foreign_path = client.app.state.settings.artifact_root / "unregistered.png"
    foreign_path.write_bytes(b"private")

    response = client.get("/api/v1/artifacts/unregistered.png")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["message"] == "Artifact was not found."
    assert response.content != b"private"
    assert foreign_path.read_bytes() == b"private"


def test_unsafe_replaced_artifact_is_not_served(
    client: TestClient,
    png_artifact: Artifact,
    project_tmp_path: Path,
) -> None:
    outside_path = project_tmp_path / "outside.png"
    outside_path.write_bytes(b"outside")
    png_artifact.path.unlink()
    png_artifact.path.symlink_to(outside_path)

    response = client.get(f"/api/v1/artifacts/{png_artifact.id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["message"] == "Artifact was not found."
    assert response.content != b"outside"
    assert outside_path.read_bytes() == b"outside"


def test_unsupported_artifact_media_type_uses_artifact_failure(
    client: TestClient,
) -> None:
    artifact = client.portal.call(
        client.app.state.artifact_store.write,
        b"<svg/>",
        "image/svg+xml",
        ".png",
        "session-1",
    )

    response = client.get(f"/api/v1/artifacts/{artifact.id}")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["error"]["code"] == "artifact_failure"
    assert "image/svg+xml" not in response.text


def test_artifact_route_openapi_documents_binary_media_and_public_errors(
    client: TestClient,
) -> None:
    responses = client.app.openapi()["paths"]["/api/v1/artifacts/{artifact_id}"]["get"][
        "responses"
    ]
    success_content = responses["200"]["content"]
    error_schemas = {
        status_code: responses[status_code]["content"]["application/json"]["schema"]
        for status_code in ("404", "500")
    }

    assert set(success_content) == {"image/png", "image/jpeg", "image/webp"}
    assert error_schemas == {
        "404": {"$ref": "#/components/schemas/ErrorResponse"},
        "500": {"$ref": "#/components/schemas/ErrorResponse"},
    }


def test_default_image_generator_is_closed_by_application_lifespan(
    project_tmp_path: Path,
) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path / "default-image-lifecycle"),
        ollama_health=ReadyOllama(),
    )
    generator = app.state.image_generator
    image_client = generator._client

    with TestClient(app):
        assert image_client.is_closed is False

    assert image_client.is_closed is True


@pytest.mark.asyncio
async def test_caller_injected_image_generator_remains_caller_owned(
    project_tmp_path: Path,
) -> None:
    image_generator = CallerOwnedImageGenerator()
    app = create_app(
        settings=settings_for(project_tmp_path / "injected-image-generator"),
        ollama_health=ReadyOllama(),
        image_generator=image_generator,
    )

    async with app.router.lifespan_context(app):
        pass

    assert image_generator.close_calls == 0


@pytest.mark.asyncio
async def test_caller_injected_image_client_remains_caller_owned(
    project_tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    async def reject_network(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError(
            "Application construction and startup must not use Ollama."
        )

    image_client = httpx.AsyncClient(transport=httpx.MockTransport(reject_network))
    settings = settings_for(project_tmp_path / "injected-image-client")
    app = create_app(
        settings=settings,
        ollama_health=ReadyOllama(),
        image_client=image_client,
    )

    try:
        async with app.router.lifespan_context(app):
            assert app.state.settings is settings
            assert app.state.dish_preview_service._settings is settings
            assert app.state.image_generator._model == Model.Z_IMAGE

        assert image_client.is_closed is False
        assert requests == []
    finally:
        await image_client.aclose()


def test_startup_failure_releases_every_application_owned_resource(
    project_tmp_path: Path,
) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path / "failed-startup"),
        ollama_health=ReadyOllama(),
    )
    cleanup = FailingStartupCleanup()
    app.state.cleanup_supervisor = cleanup
    generator = app.state.image_generator

    with pytest.raises(RuntimeError, match="cleanup startup failed"), TestClient(app):
        pass

    assert cleanup.shutdown_called is True
    assert generator._client.is_closed is True
    assert app.state.job_runner._closed is True
    assert app.state.artifact_store._root_fd is None


@pytest.mark.asyncio
async def test_shutdown_drains_owned_resources_after_repeated_cancellation(
    project_tmp_path: Path,
) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path / "cancelled-shutdown"),
        ollama_health=ReadyOllama(),
    )
    generator = app.state.image_generator
    delegate_client = generator._client
    pausing_client = PausingCloseClient(delegate_client)
    generator._client = pausing_client
    lifespan_context = app.router.lifespan_context(app)
    await lifespan_context.__aenter__()
    shutdown_task = asyncio.create_task(lifespan_context.__aexit__(None, None, None))

    try:
        await asyncio.wait_for(pausing_client.close_started.wait(), timeout=1)
        shutdown_task.cancel()
        await asyncio.sleep(0)
        assert shutdown_task.done() is False

        shutdown_task.cancel()
        await asyncio.sleep(0)
        assert shutdown_task.done() is False

        pausing_client.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown_task
    finally:
        pausing_client.allow_close.set()
        if not shutdown_task.done():
            shutdown_task.cancel()
        await asyncio.gather(shutdown_task, return_exceptions=True)
        if not delegate_client.is_closed:
            await delegate_client.aclose()

    assert pausing_client.is_closed is True
    assert app.state.cleanup_supervisor._task is None
    assert app.state.job_runner._closed is True
    assert app.state.artifact_store._root_fd is None
