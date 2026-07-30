"""Generate and persist labeled dish preview images."""

from collections.abc import Awaitable, Callable

from core.config import Settings
from core.errors import AppError, ErrorCode
from domain.artifacts import ArtifactKind
from domain.image_prompts import build_dish_prompt
from domain.images import DishPreview, ImageGenerationRequest
from domain.recipe_options import RecipeOptionDraft
from services.artifacts import ArtifactStore
from services.image_generation import ImageGenerator

ProgressCallback = Callable[[int], Awaitable[None]]

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
        settings: Settings | None = None,
    ) -> None:
        self._image_generator = image_generator
        self._artifact_store = artifact_store
        self._settings = settings if settings is not None else Settings(_env_file=None)

    async def generate(
        self,
        session_id: str,
        option: RecipeOptionDraft,
        progress: ProgressCallback,
    ) -> DishPreview:
        """Generate one preview and return it only after artifact storage succeeds."""
        image = await self._image_generator.generate(
            ImageGenerationRequest(
                prompt=build_dish_prompt(option),
                width=self._settings.image_width,
                height=self._settings.image_height,
                steps=self._settings.image_steps,
            ),
            progress,
        )
        media_type, suffix = _normalized_media_type_and_suffix(image.media_type)
        artifact = await self._artifact_store.write(
            image.data,
            media_type,
            suffix,
            owner_session_id=session_id,
            kind=ArtifactKind.DISH_PREVIEW,
        )
        return DishPreview(artifact_id=artifact.id)

    async def delete(self, artifact_id: str) -> None:
        """Delete one preview created by an uncommitted workflow attempt."""
        await self._artifact_store.delete(artifact_id)


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
