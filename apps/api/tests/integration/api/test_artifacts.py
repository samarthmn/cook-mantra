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
from core.config import Settings
from domain.artifacts import Artifact, ArtifactKind
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
    return Settings(
        _env_file=None,
        artifact_root=artifact_root,
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )


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
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def png_artifact(client: TestClient) -> Artifact:
    return client.portal.call(
        client.app.state.artifact_store.write,
        png_bytes(),
        "image/png",
        ".png",
        "session-1",
        ArtifactKind.DISH_PREVIEW,
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


def test_original_ingredient_upload_is_not_served_as_a_public_preview(
    client: TestClient,
) -> None:
    upload = client.portal.call(
        client.app.state.artifact_store.write,
        png_bytes(),
        "image/png",
        ".png",
        "session-upload",
        ArtifactKind.INGREDIENT_UPLOAD,
    )

    response = client.get(f"/api/v1/artifacts/{upload.id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "resource_not_found"
    assert response.content != png_bytes()


def test_detached_metadata_copy_cannot_authorize_an_ingredient_upload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_store = client.app.state.artifact_store
    upload = client.portal.call(
        artifact_store.write,
        png_bytes(),
        "image/png",
        ".png",
        "session-upload-copy",
        ArtifactKind.INGREDIENT_UPLOAD,
    )
    require_artifact = artifact_store.require

    async def forge_detached_preview_metadata(artifact_id: str) -> Artifact:
        artifact = await require_artifact(artifact_id)
        object.__setattr__(artifact, "kind", "dish_preview")
        return artifact

    monkeypatch.setattr(
        artifact_store,
        "require",
        forge_detached_preview_metadata,
    )

    response = client.get(f"/api/v1/artifacts/{upload.id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "resource_not_found"
    assert response.content != png_bytes()


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


def test_artifact_replaced_after_validation_never_serves_outside_bytes(
    client: TestClient,
    png_artifact: Artifact,
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside_path = project_tmp_path / "post-validation-outside.png"
    outside_path.write_bytes(b"outside-after-validation")
    artifact_store = client.app.state.artifact_store
    read_regular_file = artifact_store._read_regular_file

    def replace_before_descriptor_open(root_fd: int, filename: str) -> bytes:
        png_artifact.path.unlink()
        png_artifact.path.symlink_to(outside_path)
        return read_regular_file(root_fd, filename)

    monkeypatch.setattr(
        artifact_store,
        "_read_regular_file",
        replace_before_descriptor_open,
    )

    response = client.get(f"/api/v1/artifacts/{png_artifact.id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["message"] == "Artifact was not found."
    assert response.content != b"outside-after-validation"
    assert outside_path.read_bytes() == b"outside-after-validation"


def test_artifact_deleted_after_validation_uses_standard_404(
    client: TestClient,
    png_artifact: Artifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_store = client.app.state.artifact_store
    read_regular_file = artifact_store._read_regular_file

    def delete_before_descriptor_open(root_fd: int, filename: str) -> bytes:
        png_artifact.path.unlink()
        return read_regular_file(root_fd, filename)

    monkeypatch.setattr(
        artifact_store,
        "_read_regular_file",
        delete_before_descriptor_open,
    )

    response = client.get(
        f"/api/v1/artifacts/{png_artifact.id}",
        headers={"X-Request-ID": "req-deleted-artifact"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "Artifact was not found.",
            "details": {},
            "retryable": False,
            "request_id": "req-deleted-artifact",
            "session_id": None,
            "job_id": None,
        }
    }


@pytest.mark.parametrize(
    "range_header",
    [
        pytest.param("bytes=0-3", id="valid"),
        pytest.param("bytes=abc", id="malformed"),
        pytest.param("bytes=999-1000", id="unsatisfiable"),
    ],
)
def test_artifact_range_header_is_ignored_for_full_documented_response(
    client: TestClient,
    png_artifact: Artifact,
    range_header: str,
) -> None:
    full_content = png_artifact.path.read_bytes()

    response = client.get(
        f"/api/v1/artifacts/{png_artifact.id}",
        headers={"Range": range_header},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.content == full_content
    assert response.headers["content-length"] == str(len(full_content))
    assert "accept-ranges" not in response.headers
    assert "content-range" not in response.headers


def test_unsupported_artifact_media_type_uses_artifact_failure(
    client: TestClient,
) -> None:
    artifact = client.portal.call(
        client.app.state.artifact_store.write,
        b"<svg/>",
        "image/svg+xml",
        ".png",
        "session-1",
        ArtifactKind.DISH_PREVIEW,
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
    settings = Settings(
        _env_file=None,
        artifact_root=project_tmp_path / "injected-image-client",
        beast_base_url="http://beast.test:4900",
        beast_api_key="test-key",
    )
    app = create_app(
        settings=settings,
        ollama_health=ReadyOllama(),
        image_client=image_client,
    )

    try:
        async with app.router.lifespan_context(app):
            assert app.state.settings is settings
            assert app.state.dish_preview_service._settings is settings
            assert app.state.image_generator._model == settings.beast_image_model
            assert app.state.image_generator._base_url == "http://beast.test:4900"
            assert app.state.image_generator._timeout_seconds == (
                settings.image_timeout_seconds
            )
            assert app.state.image_generator._poll_interval_seconds == (
                settings.beast_poll_interval_seconds
            )
            assert app.state.image_generator._headers == {
                "Authorization": "Bearer test-key"
            }

        assert image_client.is_closed is False
        assert requests == []
    finally:
        await image_client.aclose()


def test_startup_failure_releases_every_application_owned_resource(
    project_tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path / "failed-startup",
            beast_base_url="http://beast.test:4900",
            beast_api_key="test-key",
        ),
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
