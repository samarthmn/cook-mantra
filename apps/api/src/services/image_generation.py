"""Beast API adapter for generated dish preview images."""

import asyncio
import hashlib
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
_TERMINAL_FAILURE_STATES = {"cancelled", "failed"}


class ImageGenerator(Protocol):
    """Generate one verified image while reporting bounded progress."""

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> GeneratedImage:
        raise NotImplementedError


class BeastImageGenerator:
    """Submit, poll, download, and verify one Beast API image job."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("transport cannot be supplied with an injected client")

        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._headers = {"Authorization": f"Bearer {api_key}"}
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
        """Generate and verify one image within a single overall timeout."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._generate(request, progress)
        except (httpx.TimeoutException, TimeoutError) as error:
            raise AppError(
                code=ErrorCode.OPERATION_TIMED_OUT,
                message="Image generation timed out.",
                status_code=504,
                retryable=True,
            ) from error
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            raise _provider_unavailable() from error

    async def _generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> GeneratedImage:
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": request.prompt,
            "width": request.width,
            "height": request.height,
            "output_format": "png",
        }
        if request.steps is not None:
            payload["model_options"] = {"steps": request.steps}

        response = await self._client.post(
            f"{self._base_url}/v1/images/generations",
            json=payload,
            headers=self._headers,
        )
        response.raise_for_status()
        if response.status_code != 202:
            raise _provider_unavailable()

        job = _job_resource(response)
        last_progress = -1
        while True:
            next_progress = _job_progress(job)
            if next_progress is not None and next_progress > last_progress:
                await progress(next_progress)
                last_progress = next_progress

            state = job.get("state")
            if state == "completed":
                break
            if state in _TERMINAL_FAILURE_STATES:
                raise _provider_unavailable()
            if state not in {"queued", "loading", "running"}:
                raise _provider_unavailable()

            job_id = job.get("id")
            if not isinstance(job_id, str) or not job_id:
                raise _provider_unavailable()
            await asyncio.sleep(self._poll_interval_seconds)
            response = await self._client.get(
                f"{self._base_url}/v1/jobs/{job_id}",
                headers=self._headers,
            )
            response.raise_for_status()
            if response.status_code != 200:
                raise _provider_unavailable()
            job = _job_resource(response)

        job_id = job.get("id")
        outputs = job.get("outputs")
        if (
            not isinstance(job_id, str)
            or not job_id
            or not isinstance(outputs, list)
            or not outputs
            or not isinstance(outputs[0], dict)
        ):
            raise _artifact_failure()

        output = outputs[0]
        expected_size = output.get("size_bytes")
        expected_sha256 = output.get("sha256")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size <= 0
            or not isinstance(expected_sha256, str)
            or not expected_sha256
        ):
            raise _artifact_failure()

        response = await self._client.get(
            self._output_url(job_id, 0),
            headers=self._headers,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise _provider_unavailable()
        image_bytes = response.content

        if len(image_bytes) != expected_size:
            raise _artifact_failure()
        if hashlib.sha256(image_bytes).hexdigest() != expected_sha256:
            raise _artifact_failure()

        generated_image = _decode_and_verify(image_bytes)
        if generated_image is None:
            raise _artifact_failure()

        await progress(100)
        return generated_image

    def _output_url(self, job_id: str, index: int) -> str:
        """Build the pending Beast output-download route in one place."""
        return f"{self._base_url}/v1/jobs/{job_id}/outputs/{index}"

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


class UnavailableImageGenerator:
    """Fail safely if preview generation is invoked without Beast settings."""

    async def generate(
        self,
        request: ImageGenerationRequest,
        progress: ProgressCallback,
    ) -> GeneratedImage:
        raise _provider_unavailable()


def _job_resource(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        raise _provider_unavailable() from None
    if not isinstance(payload, dict):
        raise _provider_unavailable()
    return payload


def _job_progress(job: dict[str, object]) -> int | None:
    value = job.get("progress")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value):
            return None
        percent = value * 100 if value <= 1 else value
        return int(max(0, min(percent, 99)))
    except OverflowError:
        return None


def _decode_and_verify(image_bytes: bytes) -> GeneratedImage | None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
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


def _provider_unavailable() -> AppError:
    return AppError(
        code=ErrorCode.IMAGE_PROVIDER_UNAVAILABLE,
        message="The image generation provider is unavailable.",
        status_code=503,
        retryable=True,
    )
