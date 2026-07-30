import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from agents.ingredient_extraction import IngredientExtractor
from api.app import create_app
from api.dependencies import get_artifact_store, get_job_runner, get_session_store
from api.routes.sessions import create_session
from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.artifacts import Artifact, ArtifactKind
from domain.ingredients import ExtractionResult
from domain.jobs import JobOperation
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.uploads import ImageUploadValidator, ValidatedImage


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": [],
            "missing": [],
        }


class FakeIngredientExtractor:
    def __init__(
        self,
        result: ExtractionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error

    async def extract(self, image: bytes, media_type: str) -> ExtractionResult:
        if self._error is not None:
            raise self._error
        assert image == png_bytes()
        assert media_type == "image/png"
        assert self._result is not None
        return self._result


class TrackingUploadValidator:
    def __init__(self, max_bytes: int) -> None:
        self._delegate = ImageUploadValidator(max_bytes)
        self.upload: UploadFile | None = None

    async def read(self, upload: UploadFile) -> ValidatedImage:
        self.upload = upload
        return await self._delegate.read(upload)


class ImmediateUploadValidator:
    async def read(self, upload: UploadFile) -> ValidatedImage:
        return ValidatedImage(
            data=png_bytes(),
            media_type="image/png",
            suffix=".png",
        )


class PausingCloseUpload:
    def __init__(self) -> None:
        self.file = BytesIO(png_bytes())
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        self.file.close()


class ClosureObservingArtifactStore:
    def __init__(
        self,
        delegate: ArtifactStore,
        validator: TrackingUploadValidator,
    ) -> None:
        self._delegate = delegate
        self._validator = validator
        self.upload_closed_when_written: bool | None = None

    async def write(
        self,
        data: bytes,
        media_type: str,
        suffix: str,
        owner_session_id: str,
        *,
        kind: ArtifactKind,
    ) -> Artifact:
        assert self._validator.upload is not None
        assert kind is ArtifactKind.INGREDIENT_UPLOAD
        self.upload_closed_when_written = self._validator.upload.file.closed
        return await self._delegate.write(
            data,
            media_type,
            suffix,
            owner_session_id,
            kind=kind,
        )

    async def delete(self, artifact_id: str) -> None:
        await self._delegate.delete(artifact_id)


class FailingArtifactStore:
    def __init__(self) -> None:
        self.owner_session_id: str | None = None

    async def write(
        self,
        data: bytes,
        media_type: str,
        suffix: str,
        owner_session_id: str,
        *,
        kind: ArtifactKind,
    ) -> Artifact:
        assert kind is ArtifactKind.INGREDIENT_UPLOAD
        self.owner_session_id = owner_session_id
        raise AppError(
            code=ErrorCode.ARTIFACT_FAILURE,
            message="Ingredient image storage is unavailable.",
            status_code=500,
            retryable=False,
        )

    async def delete(self, artifact_id: str) -> None:
        raise AssertionError("No artifact was returned to the route.")


class CapturingArtifactStore:
    def __init__(self, delegate: ArtifactStore) -> None:
        self._delegate = delegate
        self.written: Artifact | None = None

    async def write(
        self,
        data: bytes,
        media_type: str,
        suffix: str,
        owner_session_id: str,
        *,
        kind: ArtifactKind,
    ) -> Artifact:
        assert kind is ArtifactKind.INGREDIENT_UPLOAD
        self.written = await self._delegate.write(
            data,
            media_type,
            suffix,
            owner_session_id,
            kind=kind,
        )
        return self.written

    async def delete(self, artifact_id: str) -> None:
        await self._delegate.delete(artifact_id)


class FailingJobRunner:
    def __init__(self) -> None:
        self.session_id: str | None = None

    async def submit(self, operation, session_id, worker):
        assert operation is JobOperation.EXTRACT_INGREDIENTS
        self.session_id = session_id
        raise AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Extraction could not be queued.",
            status_code=503,
            retryable=True,
        )


class BlockingJobRunner:
    def __init__(self) -> None:
        self.submit_started = asyncio.Event()

    async def submit(self, operation, session_id, worker):
        assert operation is JobOperation.EXTRACT_INGREDIENTS
        self.submit_started.set()
        await asyncio.Event().wait()


class PausingDeleteArtifactStore(CapturingArtifactStore):
    def __init__(self, delegate: ArtifactStore) -> None:
        super().__init__(delegate)
        self.delete_started = asyncio.Event()
        self.allow_delete = asyncio.Event()

    async def delete(self, artifact_id: str) -> None:
        self.delete_started.set()
        await self.allow_delete.wait()
        await super().delete(artifact_id)


class CleanupFailingArtifactStore(CapturingArtifactStore):
    def __init__(self, delegate: ArtifactStore) -> None:
        super().__init__(delegate)
        self.delete_attempts: list[str] = []

    async def delete(self, artifact_id: str) -> None:
        self.delete_attempts.append(artifact_id)
        raise RuntimeError("artifact cleanup failed")


class CleanupFailingSessionStore:
    def __init__(self, delegate: SessionStore) -> None:
        self._delegate = delegate
        self.delete_attempts: list[str] = []

    async def create(self, stage):
        return await self._delegate.create(stage)

    async def delete(self, session_id: str) -> None:
        self.delete_attempts.append(session_id)
        raise RuntimeError("session cleanup failed")


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def settings_for(
    artifact_root: Path,
    *,
    max_upload_bytes: int = 10 * 1024 * 1024,
) -> Settings:
    return Settings(
        _env_file=None,
        artifact_root=artifact_root,
        max_upload_bytes=max_upload_bytes,
    )


def make_app(
    artifact_root: Path,
    extractor: IngredientExtractor,
    *,
    max_upload_bytes: int = 10 * 1024 * 1024,
):
    return create_app(
        settings=settings_for(
            artifact_root,
            max_upload_bytes=max_upload_bytes,
        ),
        ollama_health=ReadyOllama(),
        ingredient_extractor=extractor,
    )


def successful_extraction() -> ExtractionResult:
    return ExtractionResult(
        detected=[
            {"name": "Tomato", "confidence": 0.94},
            {"name": "Onion", "confidence": 0.87},
        ]
    )


def test_upload_creates_session_and_job(project_tmp_path: Path) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
        )

    assert response.status_code == 202
    assert set(response.json()) == {"session_id", "job_id"}


def test_completed_extraction_is_visible_on_session(project_tmp_path: Path) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
        ).json()
        client.portal.call(app.state.job_runner.wait, created["job_id"])

        job_response = client.get(f"/api/v1/jobs/{created['job_id']}")
        session_response = client.get(f"/api/v1/sessions/{created['session_id']}")

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "succeeded"
    assert session_response.status_code == 200
    body = session_response.json()
    assert body["stage"] == "reviewing_ingredients"
    assert body["image_artifact_id"] is not None
    assert body["warnings"] == []
    detected = [item for item in body["ingredients"] if item["source"] == "detected"]
    assert [(item["name"], item["confidence"]) for item in detected] == [
        ("Tomato", 0.94),
        ("Onion", 0.87),
    ]


def test_byte_invalid_image_uses_the_standard_error(project_tmp_path: Path) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", b"not-an-image", "image/png")},
            headers={"X-Request-ID": "req-invalid-image"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "Upload must be a valid image.",
            "details": {},
            "retryable": False,
            "request_id": "req-invalid-image",
            "session_id": None,
            "job_id": None,
        }
    }


def test_oversized_image_uses_the_standard_error(project_tmp_path: Path) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
        max_upload_bytes=4,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
            headers={"X-Request-ID": "req-oversized-image"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "Image uploads must be no larger than 10 MiB.",
            "details": {},
            "retryable": False,
            "request_id": "req-oversized-image",
            "session_id": None,
            "job_id": None,
        }
    }


def test_missing_session_uses_the_standard_not_found_error(
    project_tmp_path: Path,
) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/sessions/missing-session",
            headers={"X-Request-ID": "req-missing-session"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "Session was not found.",
            "details": {},
            "retryable": False,
            "request_id": "req-missing-session",
            "session_id": "missing-session",
            "job_id": None,
        }
    }


def test_weak_recognition_keeps_pantry_items_separate(
    project_tmp_path: Path,
) -> None:
    warning = "No ingredients were confidently detected. Add them manually."
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(
            ExtractionResult(
                detected=[],
                warnings=[warning],
            )
        ),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
        ).json()
        client.portal.call(app.state.job_runner.wait, created["job_id"])
        response = client.get(f"/api/v1/sessions/{created['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "reviewing_ingredients"
    assert body["warnings"] == [warning]
    assert body["ingredients"]
    assert {item["source"] for item in body["ingredients"]} == {"pantry_suggestion"}
    assert all(item["confirmed"] is False for item in body["ingredients"])


def test_upload_is_closed_before_artifact_storage(project_tmp_path: Path) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )
    validator = TrackingUploadValidator(app.state.settings.max_upload_bytes)
    app.state.upload_validator = validator
    artifact_store = ClosureObservingArtifactStore(
        app.state.artifact_store,
        validator,
    )
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
        )

    assert response.status_code == 202
    assert artifact_store.upload_closed_when_written is True


def test_artifact_write_failure_rolls_back_only_the_new_session(
    project_tmp_path: Path,
) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )
    failing_store = FailingArtifactStore()
    app.dependency_overrides[get_artifact_store] = lambda: failing_store

    with TestClient(app) as client:
        retained_session = client.portal.call(app.state.session_store.create)
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
            headers={"X-Request-ID": "req-artifact-failure"},
        )
        assert failing_store.owner_session_id is not None
        removed_session = client.portal.call(
            app.state.session_store.get,
            failing_store.owner_session_id,
        )
        still_retained = client.portal.call(
            app.state.session_store.require,
            retained_session.id,
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "artifact_failure",
            "message": "Ingredient image storage is unavailable.",
            "details": {},
            "retryable": False,
            "request_id": "req-artifact-failure",
            "session_id": None,
            "job_id": None,
        }
    }
    assert removed_session is None
    assert still_retained == retained_session


def test_job_submit_failure_rolls_back_only_new_session_and_artifact(
    project_tmp_path: Path,
) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )
    artifact_store = CapturingArtifactStore(app.state.artifact_store)
    failing_runner = FailingJobRunner()
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store
    app.dependency_overrides[get_job_runner] = lambda: failing_runner

    with TestClient(app) as client:
        retained_session = client.portal.call(app.state.session_store.create)
        retained_artifact = client.portal.call(
            app.state.artifact_store.write,
            b"retained-image",
            "image/png",
            ".png",
            retained_session.id,
            ArtifactKind.INGREDIENT_UPLOAD,
        )
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
            headers={"X-Request-ID": "req-submit-failure"},
        )

        assert failing_runner.session_id is not None
        assert artifact_store.written is not None
        removed_session = client.portal.call(
            app.state.session_store.get,
            failing_runner.session_id,
        )
        with pytest.raises(AppError) as removed_artifact:
            client.portal.call(
                app.state.artifact_store.require,
                artifact_store.written.id,
            )
        still_retained_session = client.portal.call(
            app.state.session_store.require,
            retained_session.id,
        )
        still_retained_artifact = client.portal.call(
            app.state.artifact_store.require,
            retained_artifact.id,
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "ollama_unavailable",
            "message": "Extraction could not be queued.",
            "details": {},
            "retryable": True,
            "request_id": "req-submit-failure",
            "session_id": None,
            "job_id": None,
        }
    }
    assert removed_session is None
    assert removed_artifact.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert still_retained_session == retained_session
    assert still_retained_artifact.id == retained_artifact.id


@pytest.mark.asyncio
async def test_cancellation_waits_for_upload_close_despite_repeated_cancel() -> None:
    upload = PausingCloseUpload()
    session_store = SessionStore(ttl_seconds=21_600)
    retained_session = await session_store.create()

    async def unused_extraction_runner(session_id, artifact_id, progress):
        raise AssertionError("Cancellation during close cannot submit a job.")

    request_task = asyncio.create_task(
        create_session(
            upload,
            ImmediateUploadValidator(),
            session_store,
            FailingArtifactStore(),
            FailingJobRunner(),
            unused_extraction_runner,
        )
    )

    try:
        await upload.close_started.wait()
        request_task.cancel()
        await asyncio.sleep(0)
        assert not request_task.done()

        request_task.cancel()
        await asyncio.sleep(0)
        assert not request_task.done()

        upload.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        assert upload.file.closed
        assert await session_store.require(retained_session.id) == retained_session
    finally:
        upload.allow_close.set()
        if not request_task.done():
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task


@pytest.mark.asyncio
async def test_cancellation_waits_for_rollback_and_preserves_neighbors(
    project_tmp_path: Path,
) -> None:
    session_store = SessionStore(ttl_seconds=21_600)
    delegate_artifact_store = ArtifactStore(
        project_tmp_path / "cancelled-session-artifacts",
        ttl_seconds=21_600,
    )
    artifact_store = PausingDeleteArtifactStore(delegate_artifact_store)
    job_runner = BlockingJobRunner()

    async def unused_extraction_runner(session_id, artifact_id, progress):
        raise AssertionError("The blocked job was never submitted.")

    await delegate_artifact_store.startup()
    retained_session = await session_store.create()
    retained_artifact = await delegate_artifact_store.write(
        b"retained-image",
        "image/png",
        ".png",
        retained_session.id,
        kind=ArtifactKind.INGREDIENT_UPLOAD,
    )
    upload = UploadFile(filename="ingredients.png", file=BytesIO(png_bytes()))
    request_task = asyncio.create_task(
        create_session(
            upload,
            ImageUploadValidator(10 * 1024 * 1024),
            session_store,
            artifact_store,
            job_runner,
            unused_extraction_runner,
        )
    )
    cleanup_started: asyncio.Task[bool] | None = None

    try:
        await job_runner.submit_started.wait()
        assert artifact_store.written is not None
        created_session_id = artifact_store.written.owner_session_id
        cleanup_started = asyncio.create_task(artifact_store.delete_started.wait())

        request_task.cancel()
        completed, _ = await asyncio.wait(
            {request_task, cleanup_started},
            return_when=asyncio.FIRST_COMPLETED,
        )

        assert cleanup_started in completed
        assert not request_task.done()
        request_task.cancel()
        await asyncio.sleep(0)
        assert not request_task.done()

        artifact_store.allow_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        assert await session_store.get(created_session_id) is None
        with pytest.raises(AppError) as removed_artifact:
            await delegate_artifact_store.require(artifact_store.written.id)
        assert removed_artifact.value.code is ErrorCode.RESOURCE_NOT_FOUND
        assert await session_store.require(retained_session.id) == retained_session
        assert (
            await delegate_artifact_store.require(retained_artifact.id)
        ).id == retained_artifact.id
    finally:
        artifact_store.allow_delete.set()
        if cleanup_started is not None and not cleanup_started.done():
            cleanup_started.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cleanup_started
        if not request_task.done():
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
        await delegate_artifact_store.shutdown()


@pytest.mark.asyncio
async def test_cancellation_during_ordinary_error_rollback_finishes_cleanup(
    project_tmp_path: Path,
) -> None:
    session_store = SessionStore(ttl_seconds=21_600)
    delegate_artifact_store = ArtifactStore(
        project_tmp_path / "ordinary-error-cancellation-artifacts",
        ttl_seconds=21_600,
    )
    artifact_store = PausingDeleteArtifactStore(delegate_artifact_store)
    job_runner = FailingJobRunner()

    async def unused_extraction_runner(session_id, artifact_id, progress):
        raise AssertionError("The failing runner never starts extraction.")

    await delegate_artifact_store.startup()
    retained_session = await session_store.create()
    retained_artifact = await delegate_artifact_store.write(
        b"retained-image",
        "image/png",
        ".png",
        retained_session.id,
        kind=ArtifactKind.INGREDIENT_UPLOAD,
    )
    upload = UploadFile(filename="ingredients.png", file=BytesIO(png_bytes()))
    request_task = asyncio.create_task(
        create_session(
            upload,
            ImageUploadValidator(10 * 1024 * 1024),
            session_store,
            artifact_store,
            job_runner,
            unused_extraction_runner,
        )
    )

    try:
        await artifact_store.delete_started.wait()
        assert artifact_store.written is not None
        created_session_id = artifact_store.written.owner_session_id

        request_task.cancel()
        await asyncio.sleep(0)
        assert not request_task.done()

        request_task.cancel()
        await asyncio.sleep(0)
        assert not request_task.done()

        artifact_store.allow_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        assert await session_store.get(created_session_id) is None
        with pytest.raises(AppError) as removed_artifact:
            await delegate_artifact_store.require(artifact_store.written.id)
        assert removed_artifact.value.code is ErrorCode.RESOURCE_NOT_FOUND
        assert await session_store.require(retained_session.id) == retained_session
        assert (
            await delegate_artifact_store.require(retained_artifact.id)
        ).id == retained_artifact.id
    finally:
        artifact_store.allow_delete.set()
        if not request_task.done():
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
        if artifact_store.written is not None:
            await delegate_artifact_store.delete(artifact_store.written.id)
            await session_store.delete(artifact_store.written.owner_session_id)
        await delegate_artifact_store.shutdown()


def test_cleanup_failures_preserve_original_error_and_neighboring_resources(
    project_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(successful_extraction()),
    )
    session_store = CleanupFailingSessionStore(app.state.session_store)
    artifact_store = CleanupFailingArtifactStore(app.state.artifact_store)
    failing_runner = FailingJobRunner()
    app.dependency_overrides[get_session_store] = lambda: session_store
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store
    app.dependency_overrides[get_job_runner] = lambda: failing_runner

    with (
        TestClient(app) as client,
        caplog.at_level("ERROR", logger="api.routes.sessions"),
    ):
        retained_session = client.portal.call(app.state.session_store.create)
        retained_artifact = client.portal.call(
            app.state.artifact_store.write,
            b"retained-image",
            "image/png",
            ".png",
            retained_session.id,
            ArtifactKind.INGREDIENT_UPLOAD,
        )
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
            headers={"X-Request-ID": "req-cleanup-failure"},
        )

        assert failing_runner.session_id is not None
        assert artifact_store.written is not None
        still_retained_session = client.portal.call(
            app.state.session_store.require,
            retained_session.id,
        )
        still_retained_artifact = client.portal.call(
            app.state.artifact_store.require,
            retained_artifact.id,
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "ollama_unavailable",
            "message": "Extraction could not be queued.",
            "details": {},
            "retryable": True,
            "request_id": "req-cleanup-failure",
            "session_id": None,
            "job_id": None,
        }
    }
    assert artifact_store.delete_attempts == [artifact_store.written.id]
    assert session_store.delete_attempts == [failing_runner.session_id]
    assert still_retained_session == retained_session
    assert still_retained_artifact.id == retained_artifact.id
    rollback_records = [
        record
        for record in caplog.records
        if record.getMessage() == "Session upload rollback failed"
    ]
    assert {record.resource for record in rollback_records} == {
        "artifact",
        "session",
    }


def test_background_failure_retains_extracting_session_and_artifact(
    project_tmp_path: Path,
) -> None:
    app = make_app(
        project_tmp_path,
        FakeIngredientExtractor(error=RuntimeError("vision failed")),
    )
    artifact_store = CapturingArtifactStore(app.state.artifact_store)
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", png_bytes(), "image/png")},
        ).json()
        client.portal.call(app.state.job_runner.wait, created["job_id"])
        job_response = client.get(f"/api/v1/jobs/{created['job_id']}")
        session_response = client.get(f"/api/v1/sessions/{created['session_id']}")
        assert artifact_store.written is not None
        retained_artifact = client.portal.call(
            app.state.artifact_store.require,
            artifact_store.written.id,
        )

    assert job_response.json()["status"] == "failed"
    assert job_response.json()["error"]["code"] == "internal_error"
    assert session_response.status_code == 200
    assert session_response.json()["stage"] == "extracting"
    assert session_response.json()["ingredients"] == []
    assert session_response.json()["warnings"] == []
    assert retained_artifact.owner_session_id == created["session_id"]
