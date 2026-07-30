"""Experimental Ollama HTTP adapter for generated dish preview images."""

import base64
import json
import math
from collections.abc import Awaitable, Callable
from io import BytesIO
from types import TracebackType
from typing import Protocol, Self

import httpx
from PIL import Image

from core.errors import AppError, ErrorCode
from domain.images import GeneratedImage, ImageGenerationRequest

ProgressCallback = Callable[[int], Awaitable[None]]

_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class ImageGenerator(Protocol):
    """Generate one verified image while reporting bounded progress."""

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> GeneratedImage:
        raise NotImplementedError


class OllamaImageGenerator:
    """Call Ollama's experimental streaming image-generation endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("transport cannot be supplied with an injected client")

        self._base_url = base_url.rstrip("/")
        self._model = model
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(
                timeout=timeout_seconds,
                transport=transport,
            )
        )
        self._closed = False

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> GeneratedImage:
        """Generate and verify one supported image from streamed NDJSON."""
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": request.prompt,
            "stream": True,
            "width": request.width,
            "height": request.height,
        }
        if request.steps is not None:
            payload["steps"] = request.steps

        final_image: str | None = None
        last_progress = -1
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/generate",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue

                    event = _parse_event(line)
                    if event is None:
                        raise _artifact_failure()

                    next_progress = _event_progress(event)
                    if next_progress is not None and next_progress > last_progress:
                        await progress(next_progress)
                        last_progress = next_progress

                    if event.get("done") is True:
                        image_value = event.get("image")
                        if not isinstance(image_value, str) or not image_value:
                            raise _artifact_failure()
                        final_image = image_value
                        break
        except (httpx.TimeoutException, TimeoutError) as error:
            raise AppError(
                code=ErrorCode.OPERATION_TIMED_OUT,
                message="Image generation timed out.",
                status_code=504,
                retryable=True,
            ) from error
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            raise AppError(
                code=ErrorCode.OLLAMA_UNAVAILABLE,
                message="Ollama is unavailable.",
                status_code=503,
                retryable=True,
            ) from error

        if final_image is None:
            raise _artifact_failure()

        generated_image = _decode_and_verify(final_image)
        if generated_image is None:
            raise _artifact_failure()

        await progress(100)
        return generated_image

    async def aclose(self) -> None:
        """Close only the HTTP client created by this adapter."""
        if self._owns_client and not self._closed:
            await self._client.aclose()
            self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


def _parse_event(line: str) -> dict[str, object] | None:
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    return event if isinstance(event, dict) else None


def _event_progress(event: dict[str, object]) -> int | None:
    completed = event.get("completed")
    total = event.get("total")
    if (
        isinstance(completed, bool)
        or isinstance(total, bool)
        or not isinstance(completed, (int, float))
        or not isinstance(total, (int, float))
    ):
        return None

    try:
        if not math.isfinite(completed) or not math.isfinite(total) or total <= 0:
            return None
        percent = completed / total * 100
        if not math.isfinite(percent):
            return None
        return int(max(0, min(percent, 99)))
    except OverflowError:
        raise _artifact_failure() from None


def _decode_and_verify(encoded_image: str) -> GeneratedImage | None:
    try:
        image_bytes = base64.b64decode(encoded_image, validate=True)
        with Image.open(BytesIO(image_bytes)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except Exception:
        return None

    media_type = _MEDIA_TYPES.get(image_format or "")
    if media_type is None:
        return None
    return GeneratedImage(
        data=image_bytes,
        media_type=media_type,
        width=width,
        height=height,
    )


def _artifact_failure() -> AppError:
    return AppError(
        code=ErrorCode.ARTIFACT_FAILURE,
        message="The generated image artifact is invalid.",
        status_code=502,
        retryable=False,
    )
