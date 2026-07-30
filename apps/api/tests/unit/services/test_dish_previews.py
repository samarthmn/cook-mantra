import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.images import GeneratedImage, ImageGenerationRequest
from domain.recipe_options import Difficulty, RecipeOptionDraft
from services.artifacts import ArtifactStore
from services.dish_previews import DishPreviewService


def recipe_option_draft() -> RecipeOptionDraft:
    return RecipeOptionDraft(
        name="Tomato masala",
        summary="A quick tomato dish.",
        cuisine="Indian",
        total_minutes=20,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato", "Onion"],
    )


class RecordingImageGenerator:
    def __init__(self, image: GeneratedImage, progress_values: list[int]) -> None:
        self._image = image
        self._progress_values = progress_values
        self.request: ImageGenerationRequest | None = None

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> GeneratedImage:
        self.request = request
        for value in self._progress_values:
            await progress(value)
        return self._image


class CancellingImageGenerator:
    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> GeneratedImage:
        raise asyncio.CancelledError


def generated_image(media_type: str = "image/png") -> GeneratedImage:
    return GeneratedImage(
        data=b"generated-image-bytes",
        media_type=media_type,
        width=512,
        height=640,
    )


async def ignore_progress(_: int) -> None:
    return None


@pytest.mark.asyncio
async def test_preview_service_uses_exact_settings_for_request_and_forwards_progress(
    project_tmp_path: Path,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-settings", ttl_seconds=60)
    await store.startup()
    generator = RecordingImageGenerator(generated_image(), [12, 84])
    settings = Settings(
        _env_file=None,
        image_width=512,
        image_height=640,
        image_steps=8,
    )
    progress_values: list[int] = []

    async def record_progress(value: int) -> None:
        progress_values.append(value)

    try:
        await DishPreviewService(generator, store, settings).generate(
            "session-1", recipe_option_draft(), record_progress
        )
    finally:
        await store.shutdown()

    assert generator.request is not None
    assert generator.request.width == 512
    assert generator.request.height == 640
    assert generator.request.steps == 8
    assert progress_values == [12, 84]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "suffix"),
    [
        ("image/png", ".png"),
        ("image/jpeg", ".jpg"),
        ("image/webp", ".webp"),
    ],
)
async def test_preview_service_stores_each_supported_generated_image_with_its_owner(
    project_tmp_path: Path,
    media_type: str,
    suffix: str,
) -> None:
    store = ArtifactStore(project_tmp_path / f"preview-{suffix[1:]}", ttl_seconds=60)
    await store.startup()
    generator = RecordingImageGenerator(generated_image(media_type), [])

    try:
        preview = await DishPreviewService(generator, store).generate(
            "session-1", recipe_option_draft(), ignore_progress
        )
        artifact = await store.require(preview.artifact_id)
        stored_data, stored_media_type = await store.read(
            preview.artifact_id, "session-1"
        )
    finally:
        await store.shutdown()

    assert preview.label == "AI-generated illustration"
    assert artifact.owner_session_id == "session-1"
    assert artifact.path.suffix == suffix
    assert stored_data == b"generated-image-bytes"
    assert stored_media_type == media_type


@pytest.mark.asyncio
async def test_preview_service_rejects_unsupported_generator_media_before_storing(
    project_tmp_path: Path,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-invalid-media", ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(
        RecordingImageGenerator(generated_image("image/svg+xml"), []), store
    )

    try:
        with pytest.raises(AppError) as raised:
            await service.generate("session-1", recipe_option_draft(), ignore_progress)

        assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
        assert list((project_tmp_path / "preview-invalid-media").iterdir()) == []
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_propagates_artifact_write_failures_without_a_preview(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-write-failure", ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(RecordingImageGenerator(generated_image(), []), store)

    async def fail_write(*_: object, **__: object) -> None:
        raise AppError(
            code=ErrorCode.ARTIFACT_FAILURE,
            message="The artifact could not be stored.",
            status_code=500,
            retryable=False,
        )

    monkeypatch.setattr(store, "write", fail_write)
    try:
        with pytest.raises(AppError) as raised:
            await service.generate("session-1", recipe_option_draft(), ignore_progress)

        assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_propagates_generator_cancellation_unchanged(
    project_tmp_path: Path,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-cancellation", ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(CancellingImageGenerator(), store)

    try:
        with pytest.raises(asyncio.CancelledError):
            await service.generate("session-1", recipe_option_draft(), ignore_progress)
        assert list((project_tmp_path / "preview-cancellation").iterdir()) == []
    finally:
        await store.shutdown()
