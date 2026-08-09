import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from core.errors import AppError, ErrorCode
from domain.images import ImageGenerationRequest, VerifiedRaster
from domain.model_runtime import ImageOutputFormat, ImageTuning
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeIngredient,
    RecipeStep,
)
from services import dish_previews as dish_previews_module
from services.artifacts import ArtifactStore
from services.dish_previews import DishPreviewService


def complete_recipe() -> CompleteRecipe:
    return CompleteRecipe(
        option_id="option-1",
        name="Tomato masala",
        cuisine="Indian",
        servings=2,
        total_minutes=20,
        ingredients=(
            RecipeIngredient(
                name="Tomato",
                quantity="3 medium",
                availability=IngredientAvailability.AVAILABLE,
            ),
            RecipeIngredient(
                name="Onion",
                quantity="1 large",
                availability=IngredientAvailability.AVAILABLE,
            ),
        ),
        steps=(RecipeStep(number=1, instruction="Cook until tender."),),
    )


class RecordingImageGenerator:
    def __init__(self, image: VerifiedRaster, progress_values: list[int]) -> None:
        self._image = image
        self._progress_values = progress_values
        self.request: ImageGenerationRequest | None = None

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> VerifiedRaster:
        self.request = request
        for value in self._progress_values:
            await progress(value)
        return self._image


class CancellingImageGenerator:
    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> VerifiedRaster:
        raise asyncio.CancelledError


class RasterImposterGenerator:
    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> object:
        class RasterImposter:
            data = b"unverified-image-bytes"
            media_type = "image/png"
            width = 512
            height = 640

        return RasterImposter()


def generated_image(media_type: str = "image/png") -> VerifiedRaster:
    return VerifiedRaster(
        data=b"generated-image-bytes",
        media_type=media_type,
        width=512,
        height=640,
    )


async def ignore_progress(_: int) -> None:
    return None


@pytest.mark.asyncio
async def test_preview_service_uses_completed_recipe_instruction_and_exact_tuning(
    project_tmp_path: Path,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-settings", ttl_seconds=60)
    await store.startup()
    generator = RecordingImageGenerator(generated_image(), [12, 84])
    tuning = ImageTuning(
        width=512,
        height=640,
        output_format=ImageOutputFormat.PNG,
    )
    progress_values: list[int] = []

    async def record_progress(value: int) -> None:
        progress_values.append(value)

    try:
        await DishPreviewService(
            generator,
            store,
            tuning,
            editable_instruction="Use a rustic stoneware plate.",
        ).generate("session-1", complete_recipe(), record_progress)
    finally:
        await store.shutdown()

    assert generator.request is not None
    assert generator.request.tuning == tuning
    assert "Use a rustic stoneware plate." in generator.request.prompt
    assert '"name":"Tomato masala"' in generator.request.prompt
    assert '"quantity":"3 medium"' in generator.request.prompt
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
        tuning = ImageTuning(
            width=512,
            height=640,
            output_format={
                "image/png": ImageOutputFormat.PNG,
                "image/jpeg": ImageOutputFormat.JPEG,
                "image/webp": ImageOutputFormat.WEBP,
            }[media_type],
        )
        preview = await DishPreviewService(generator, store, tuning).generate(
            "session-1", complete_recipe(), ignore_progress
        )
        artifact = await store.require(preview.artifact_id)
        stored_data, stored_media_type = await store.read(
            preview.artifact_id, "session-1"
        )
    finally:
        await store.shutdown()

    assert preview.label == "AI-generated image"
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
        RecordingImageGenerator(generated_image("image/svg+xml"), []),
        store,
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )

    try:
        with pytest.raises(AppError) as raised:
            await service.generate("session-1", complete_recipe(), ignore_progress)

        assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
        assert list((project_tmp_path / "preview-invalid-media").iterdir()) == []
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_persists_only_a_verified_raster(
    project_tmp_path: Path,
) -> None:
    root = project_tmp_path / "preview-unverified-raster"
    store = ArtifactStore(root, ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(
        RasterImposterGenerator(),  # type: ignore[arg-type]
        store,
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )

    try:
        with pytest.raises(AppError) as raised:
            await service.generate("session-1", complete_recipe(), ignore_progress)

        assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
        assert list(root.iterdir()) == []
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_propagates_artifact_write_failures_without_a_preview(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-write-failure", ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(
        RecordingImageGenerator(generated_image(), []),
        store,
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )

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
            await service.generate("session-1", complete_recipe(), ignore_progress)

        assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_propagates_generator_cancellation_unchanged(
    project_tmp_path: Path,
) -> None:
    store = ArtifactStore(project_tmp_path / "preview-cancellation", ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(
        CancellingImageGenerator(),
        store,
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )

    try:
        with pytest.raises(asyncio.CancelledError):
            await service.generate("session-1", complete_recipe(), ignore_progress)
        assert list((project_tmp_path / "preview-cancellation").iterdir()) == []
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_deletes_a_written_artifact_when_preview_creation_fails(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project_tmp_path / "preview-construction-failure"
    store = ArtifactStore(root, ttl_seconds=60)
    await store.startup()
    service = DishPreviewService(
        RecordingImageGenerator(generated_image(), []),
        store,
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )

    class PreviewConstructionFailure(RuntimeError):
        pass

    def fail_preview_construction(*_: object, **__: object) -> None:
        raise PreviewConstructionFailure("preview could not be returned")

    monkeypatch.setattr(
        dish_previews_module,
        "DishPreview",
        fail_preview_construction,
    )
    try:
        with pytest.raises(
            PreviewConstructionFailure,
            match="preview could not be returned",
        ):
            await service.generate("session-1", complete_recipe(), ignore_progress)

        assert list(root.iterdir()) == []
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_preserves_post_write_failure_when_cleanup_also_fails(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = ArtifactStore(
        project_tmp_path / "preview-cleanup-failure",
        ttl_seconds=60,
    )
    await store.startup()
    service = DishPreviewService(
        RecordingImageGenerator(generated_image(), []),
        store,
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )
    delete_calls: list[str] = []
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()

    class PreviewConstructionFailure(RuntimeError):
        pass

    def fail_preview_construction(*_: object, **__: object) -> None:
        raise PreviewConstructionFailure("primary preview failure")

    async def fail_delete(artifact_id: str) -> None:
        delete_calls.append(artifact_id)
        delete_started.set()
        await release_delete.wait()
        raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(
        dish_previews_module,
        "DishPreview",
        fail_preview_construction,
    )
    monkeypatch.setattr(store, "delete", fail_delete)
    try:
        with caplog.at_level(logging.ERROR, logger="services.dish_previews"):
            generation = asyncio.create_task(
                service.generate(
                    "session-1",
                    complete_recipe(),
                    ignore_progress,
                )
            )
            await asyncio.wait_for(delete_started.wait(), timeout=2)
            generation.cancel()
            await asyncio.sleep(0)
            generation.cancel()
            release_delete.set()

            with pytest.raises(
                PreviewConstructionFailure,
                match="primary preview failure",
            ):
                await generation

        assert len(delete_calls) == 1
        cleanup_records = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == "dish_preview_cleanup_failed"
        ]
        assert len(cleanup_records) == 1
        assert cleanup_records[0].artifact_id == delete_calls[0]
        assert cleanup_records[0].error_type == "RuntimeError"
        assert "secondary cleanup failure" not in cleanup_records[0].getMessage()
    finally:
        await store.shutdown()


@pytest.mark.asyncio
async def test_preview_service_drains_delete_through_repeated_gap_cancellation() -> (
    None
):
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()
    delete_finished = asyncio.Event()
    delete_calls: list[str] = []

    class CancellingWriteStore:
        async def write(self, *_: object, **__: object) -> object:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()

            class WrittenArtifact:
                id = "preview-artifact-1"

            return WrittenArtifact()

        async def delete(self, artifact_id: str) -> None:
            delete_calls.append(artifact_id)
            delete_started.set()
            await release_delete.wait()
            delete_finished.set()

    service = DishPreviewService(
        RecordingImageGenerator(generated_image(), []),
        CancellingWriteStore(),  # type: ignore[arg-type]
        ImageTuning(
            width=512,
            height=640,
            output_format=ImageOutputFormat.PNG,
        ),
    )

    generation = asyncio.create_task(
        service.generate("session-1", complete_recipe(), ignore_progress)
    )
    for _ in range(8):
        await asyncio.sleep(0)
        if delete_started.is_set():
            break
    cleanup_started = delete_started.is_set()
    if cleanup_started:
        generation.cancel()
        await asyncio.sleep(0)
        generation.cancel()
    release_delete.set()

    with pytest.raises(asyncio.CancelledError):
        await generation

    assert cleanup_started is True
    assert delete_calls == ["preview-artifact-1"]
    assert delete_finished.is_set()
