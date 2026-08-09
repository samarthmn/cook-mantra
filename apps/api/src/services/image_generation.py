"""Provider-neutral generated-image validation and invocation boundaries."""

import base64
import math
import warnings
from collections.abc import Awaitable, Callable, Mapping
from io import BytesIO
from typing import Protocol

from PIL import Image

from core.errors import AppError, ErrorCode
from core.logging import current_log_context
from core.runtime_config import RoleConfiguration, RuntimeConfigurationSnapshot
from domain.images import ImageGenerationRequest, VerifiedRaster
from domain.model_runtime import (
    IMAGE_PROVIDER_NAMES,
    AgentRole,
    Capability,
    ImageOutputFormat,
    ImageOutputProvider,
    ImageTuning,
    ModelResult,
    ModelUsage,
    ModelUsageEvent,
    ProviderDiscoveryResult,
    ProviderError,
    ProviderErrorKind,
    ProviderImageOutput,
    ProviderName,
)
from services.provider_errors import ProviderInvocationError

ProgressCallback = Callable[[int], Awaitable[None]]

MAX_GENERATED_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ENCODED_IMAGE_CHARS = 4 * math.ceil(MAX_GENERATED_IMAGE_BYTES / 3)

_RASTER_TYPES: dict[str, tuple[ImageOutputFormat, str]] = {
    "JPEG": (ImageOutputFormat.JPEG, "image/jpeg"),
    "PNG": (ImageOutputFormat.PNG, "image/png"),
    "WEBP": (ImageOutputFormat.WEBP, "image/webp"),
}
_ALLOWED_MEDIA_TYPES = frozenset(media_type for _, media_type in _RASTER_TYPES.values())
_RETRYABLE_PROVIDER_KINDS = frozenset(
    {
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.TIMED_OUT,
        ProviderErrorKind.UNAVAILABLE,
    }
)


class ImageGenerator(Protocol):
    """Generate one centrally verified raster and report bounded progress."""

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> VerifiedRaster: ...


class ImageProviderRuntime(ImageOutputProvider, Protocol):
    """Discover and invoke one immutable configured image-provider selection."""

    async def inspect(self) -> ProviderDiscoveryResult: ...


class ModelUsageEventSink(Protocol):
    """Receive content-free image usage events."""

    async def record(self, event: ModelUsageEvent) -> None: ...


class RuntimeImageGenerator:
    """Invoke the one configured image runtime and return a verified raster."""

    def __init__(
        self,
        snapshot: RuntimeConfigurationSnapshot,
        *,
        image_runtimes: Mapping[ProviderName, ImageProviderRuntime],
        usage_journal: ModelUsageEventSink | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._image_runtimes = dict(image_runtimes)
        self._usage_journal = usage_journal

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> VerifiedRaster:
        selection = self._selection(request)
        runtime = self._image_runtimes.get(selection.provider)
        if runtime is None:
            raise _public_image_error(
                _runtime_failure(
                    ProviderErrorKind.CAPABILITY_MISSING,
                    provider=selection.provider,
                    retryable=False,
                )
            ) from None

        try:
            inspection = await runtime.inspect()
            if not isinstance(inspection, ProviderDiscoveryResult):
                raise _runtime_failure(
                    ProviderErrorKind.PROTOCOL_ERROR,
                    provider=selection.provider,
                    retryable=False,
                )
            self._admit(inspection)
            result = await runtime.generate_image(
                request.prompt,
                tuning=request.tuning,
            )
            if not isinstance(result, ModelResult) or not isinstance(
                result.output, ProviderImageOutput
            ):
                raise _runtime_failure(
                    ProviderErrorKind.PROTOCOL_ERROR,
                    provider=selection.provider,
                    retryable=False,
                )
            if result.usage is not None and not isinstance(result.usage, ModelUsage):
                raise _runtime_failure(
                    ProviderErrorKind.PROTOCOL_ERROR,
                    provider=selection.provider,
                    retryable=False,
                )
            if result.usage is not None and self._usage_journal is not None:
                await self._usage_journal.record(
                    ModelUsageEvent(
                        role=AgentRole.IMAGE_GENERATOR,
                        provider=selection.provider,
                        model=selection.model,
                        usage=ModelUsage(
                            input_tokens=result.usage.input_tokens,
                            output_tokens=result.usage.output_tokens,
                        ),
                        **_usage_correlation(),
                    )
                )
            raster = validate_generated_raster(result.output, request.tuning)
        except ProviderInvocationError as error:
            raise _public_image_error(error) from None

        await progress(100)
        return raster

    def _selection(self, request: ImageGenerationRequest) -> RoleConfiguration:
        config = self._snapshot.config
        if config is None:
            raise _configuration_error()
        selection = config.roles[AgentRole.IMAGE_GENERATOR]
        if not selection.enabled:
            raise _public_image_error(
                _runtime_failure(
                    ProviderErrorKind.CAPABILITY_MISSING,
                    provider=selection.provider,
                    retryable=False,
                )
            ) from None
        if selection.provider not in IMAGE_PROVIDER_NAMES:
            raise _public_image_error(
                _runtime_failure(
                    ProviderErrorKind.CAPABILITY_MISSING,
                    provider=selection.provider,
                    retryable=False,
                )
            ) from None
        if selection.image_tuning is None or request.tuning != selection.image_tuning:
            raise _configuration_error()
        return selection

    def _admit(self, inspection: ProviderDiscoveryResult) -> None:
        config = self._snapshot.config
        if config is None:
            raise _runtime_failure(
                ProviderErrorKind.PROTOCOL_ERROR,
                retryable=False,
            )
        selection = config.roles[AgentRole.IMAGE_GENERATOR]
        if inspection.provider is not selection.provider:
            raise _runtime_failure(
                ProviderErrorKind.PROTOCOL_ERROR,
                provider=selection.provider,
                retryable=False,
            )
        discovered = inspection.models.get(selection.model)
        if discovered is None or discovered.model != selection.model:
            raise _runtime_failure(
                ProviderErrorKind.CAPABILITY_MISSING,
                provider=selection.provider,
                retryable=False,
            )
        if not discovered.available or discovered.error is not None:
            kind = discovered.error or ProviderErrorKind.CAPABILITY_MISSING
            raise _runtime_failure(
                kind,
                provider=selection.provider,
                retryable=kind in _RETRYABLE_PROVIDER_KINDS,
            )
        configured_capabilities = selection.capabilities or frozenset()
        if Capability.IMAGE_OUTPUT not in configured_capabilities or (
            Capability.IMAGE_OUTPUT not in discovered.capabilities
        ):
            raise _runtime_failure(
                ProviderErrorKind.CAPABILITY_MISSING,
                provider=selection.provider,
                retryable=False,
            )


def validate_generated_raster(
    output: ProviderImageOutput,
    tuning: ImageTuning,
) -> VerifiedRaster:
    """Decode and fully verify one provider raster without fetching or rewriting it."""
    try:
        return _validate_generated_raster(output, tuning)
    except Exception:
        raise _invalid_output() from None


def _validate_generated_raster(
    output: ProviderImageOutput,
    tuning: ImageTuning,
) -> VerifiedRaster:
    raw_base64 = output.base64_data
    if (
        not isinstance(raw_base64, str)
        or not raw_base64
        or not raw_base64.isascii()
        or len(raw_base64) > MAX_ENCODED_IMAGE_CHARS
    ):
        raise ValueError("invalid encoded image")

    encoded = raw_base64.encode("ascii")
    data = base64.b64decode(encoded, validate=True)
    if not data or len(data) > MAX_GENERATED_IMAGE_BYTES:
        raise ValueError("invalid decoded image")

    declared_media_type = _normalize_media_type(output.media_type)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            image_format, width, height, media_type = _inspect_raster(
                image,
                tuning,
                declared_media_type,
            )
            image.verify()

        with Image.open(BytesIO(data)) as image:
            loaded_format, loaded_width, loaded_height, loaded_media_type = (
                _inspect_raster(image, tuning, declared_media_type)
            )
            image.load()
            if (
                image.format != loaded_format
                or image.size != (loaded_width, loaded_height)
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("loaded image metadata changed")

    if (
        loaded_format != image_format
        or loaded_width != width
        or loaded_height != height
        or loaded_media_type != media_type
    ):
        raise ValueError("verified image metadata changed")
    return VerifiedRaster(
        data=data,
        media_type=media_type,
        width=width,
        height=height,
    )


def _normalize_media_type(media_type: object) -> str | None:
    if media_type is None:
        return None
    if not isinstance(media_type, str):
        raise ValueError("invalid declared media type")
    normalized = media_type.strip().lower()
    if normalized not in _ALLOWED_MEDIA_TYPES:
        raise ValueError("unsupported declared media type")
    return normalized


def _inspect_raster(
    image: Image.Image,
    tuning: ImageTuning,
    declared_media_type: str | None,
) -> tuple[str, int, int, str]:
    image_format = image.format
    raster_type = _RASTER_TYPES.get(image_format or "")
    if raster_type is None:
        raise ValueError("unsupported detected image format")
    output_format, media_type = raster_type
    if output_format is not tuning.output_format:
        raise ValueError("detected image format differs from configured format")
    if declared_media_type is not None and declared_media_type != media_type:
        raise ValueError("declared media type differs from detected format")

    width, height = image.size
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width != tuning.width
        or height != tuning.height
    ):
        raise ValueError("detected dimensions differ from configured dimensions")
    frame_count = getattr(image, "n_frames", 1)
    if isinstance(frame_count, bool) or not isinstance(frame_count, int):
        raise ValueError("invalid raster frame count")
    if frame_count != 1:
        raise ValueError("animated raster is not supported")
    return image_format or "", width, height, media_type


def _invalid_output() -> ProviderInvocationError:
    return _runtime_failure(
        ProviderErrorKind.INVALID_OUTPUT,
        retryable=False,
    )


def _runtime_failure(
    kind: ProviderErrorKind,
    *,
    retryable: bool,
    provider: ProviderName | None = None,
) -> ProviderInvocationError:
    return ProviderInvocationError(
        ProviderError(
            kind=kind,
            message="The selected image provider could not complete the operation.",
            retryable=retryable,
            provider=provider,
            role=AgentRole.IMAGE_GENERATOR,
        )
    )


def _usage_correlation() -> dict[str, object]:
    context = current_log_context()
    return {
        field: value
        for field in ("session_id", "job_id")
        if isinstance((value := context.get(field)), str) and 0 < len(value) <= 128
    }


def _configuration_error() -> AppError:
    return AppError(
        code=ErrorCode.MODEL_CONFIGURATION_INVALID,
        message="The model runtime configuration is invalid.",
        status_code=503,
        retryable=False,
        details={"role": AgentRole.IMAGE_GENERATOR.value},
    )


def _public_image_error(error: ProviderInvocationError) -> AppError:
    mappings: dict[ProviderErrorKind, tuple[ErrorCode, int, str]] = {
        ProviderErrorKind.UNAVAILABLE: (
            ErrorCode.PROVIDER_UNAVAILABLE,
            503,
            "The selected model provider is unavailable.",
        ),
        ProviderErrorKind.AUTHENTICATION_FAILED: (
            ErrorCode.PROVIDER_AUTHENTICATION_FAILED,
            401,
            "Model provider authentication failed.",
        ),
        ProviderErrorKind.RATE_LIMITED: (
            ErrorCode.PROVIDER_RATE_LIMITED,
            429,
            "The selected model provider is rate limited.",
        ),
        ProviderErrorKind.PAYMENT_REQUIRED: (
            ErrorCode.PROVIDER_PAYMENT_REQUIRED,
            402,
            "The model provider requires payment.",
        ),
        ProviderErrorKind.CAPABILITY_MISSING: (
            ErrorCode.MODEL_CAPABILITY_MISSING,
            503,
            "The selected model lacks a required capability.",
        ),
        ProviderErrorKind.PROTOCOL_ERROR: (
            ErrorCode.PROVIDER_PROTOCOL_ERROR,
            502,
            "The model provider returned an invalid response.",
        ),
        ProviderErrorKind.TIMED_OUT: (
            ErrorCode.OPERATION_TIMED_OUT,
            504,
            "The model operation timed out.",
        ),
        ProviderErrorKind.INVALID_OUTPUT: (
            ErrorCode.ARTIFACT_FAILURE,
            502,
            "The generated image artifact is invalid.",
        ),
    }
    code, status_code, message = mappings[error.error.kind]
    return AppError(
        code=code,
        message=message,
        status_code=status_code,
        retryable=error.error.retryable,
        details={"role": AgentRole.IMAGE_GENERATOR.value},
    )
