"""Generate and persist labeled dish preview images."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from core.errors import AppError, ErrorCode
from domain.artifacts import ArtifactKind
from domain.image_prompts import build_dish_prompt
from domain.images import DishPreview, ImageGenerationRequest, VerifiedRaster
from domain.model_runtime import ImageTuning
from domain.recipes import CompleteRecipe
from services.artifacts import ArtifactStore
from services.image_generation import ImageGenerator

ProgressCallback = Callable[[int], Awaitable[None]]
logger = logging.getLogger(__name__)

_SUFFIXES_BY_MEDIA_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class DishPreviewService:
    """Persist a generated dish image as an artifact owned by one session."""

    def __init__(
        self,
        image_generator: ImageGenerator,
        artifact_store: ArtifactStore,
        image_tuning: ImageTuning,
        *,
        editable_instruction: str = "",
    ) -> None:
        self._image_generator = image_generator
        self._artifact_store = artifact_store
        self._image_tuning = image_tuning
        self._editable_instruction = editable_instruction

    async def generate(
        self,
        session_id: str,
        recipe: CompleteRecipe,
        progress: ProgressCallback,
    ) -> DishPreview:
        """Generate one preview and return it only after artifact storage succeeds."""
        image = await self._image_generator.generate(
            ImageGenerationRequest(
                prompt=build_dish_prompt(
                    recipe,
                    editable_instruction=self._editable_instruction,
                ),
                tuning=self._image_tuning,
            ),
            progress,
        )
        if not isinstance(image, VerifiedRaster):
            raise _artifact_failure()
        media_type, suffix = _normalized_media_type_and_suffix(image.media_type)
        artifact = await self._artifact_store.write(
            image.data,
            media_type,
            suffix,
            owner_session_id=session_id,
            kind=ArtifactKind.DISH_PREVIEW,
        )
        try:
            preview = DishPreview(artifact_id=artifact.id)
            await asyncio.sleep(0)
            return preview
        except BaseException:
            await self._drain_owned_delete(artifact.id)
            raise

    async def delete(self, artifact_id: str) -> None:
        """Delete one preview created by an uncommitted workflow attempt."""
        await self._artifact_store.delete(artifact_id)

    async def _drain_owned_delete(self, artifact_id: str) -> None:
        """Settle one cleanup attempt without replacing the primary failure."""
        delete_task = asyncio.create_task(self._artifact_store.delete(artifact_id))
        while not delete_task.done():
            try:
                await asyncio.shield(delete_task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if delete_task.cancelled():
            _log_cleanup_failure(artifact_id, asyncio.CancelledError())
            return
        try:
            delete_task.result()
        except BaseException as error:
            _log_cleanup_failure(artifact_id, error)
            return


def _normalized_media_type_and_suffix(media_type: object) -> tuple[str, str]:
    """Return the canonical stored MIME type and an ArtifactStore-approved suffix."""
    if not isinstance(media_type, str):
        raise _artifact_failure()

    normalized_media_type = media_type.strip().lower()
    suffix = _SUFFIXES_BY_MEDIA_TYPE.get(normalized_media_type)
    if suffix is None:
        raise _artifact_failure()
    return normalized_media_type, suffix


def _artifact_failure() -> AppError:
    return AppError(
        code=ErrorCode.ARTIFACT_FAILURE,
        message="The generated image artifact is invalid.",
        status_code=502,
        retryable=False,
    )


def _log_cleanup_failure(artifact_id: str, error: BaseException) -> None:
    logger.error(
        "Dish preview cleanup failed",
        extra={
            "event": "dish_preview_cleanup_failed",
            "artifact_id": artifact_id,
            "error_type": type(error).__name__,
        },
    )
