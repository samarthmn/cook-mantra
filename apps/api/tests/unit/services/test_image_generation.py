import asyncio
import base64
import json
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image

from core.config import Model
from core.errors import AppError, ErrorCode
from domain.images import ImageGenerationRequest
from services.image_generation import OllamaImageGenerator


class StreamingByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        *,
        stream_error: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._stream_error = stream_error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._stream_error is not None:
            raise self._stream_error


class StreamingJsonTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        status_code: int = 200,
        request_error: Exception | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self._chunks = chunks or []
        self._status_code = status_code
        self._request_error = request_error
        self._stream_error = stream_error
        self.requests: list[httpx.Request] = []
        self.close_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._request_error is not None:
            raise self._request_error
        return httpx.Response(
            self._status_code,
            stream=StreamingByteStream(
                self._chunks,
                stream_error=self._stream_error,
            ),
            request=request,
        )

    async def aclose(self) -> None:
        self.close_calls += 1


def image_base64(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (320, 288),
) -> str:
    buffer = BytesIO()
    Image.new("RGB", size, "orange").save(buffer, format=image_format)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def png_with_bad_checksum_base64() -> str:
    image_bytes = bytearray(base64.b64decode(image_base64()))
    chunk_type_at = image_bytes.index(b"IDAT")
    chunk_length = int.from_bytes(
        image_bytes[chunk_type_at - 4 : chunk_type_at],
        byteorder="big",
    )
    checksum_at = chunk_type_at + 4 + chunk_length
    image_bytes[checksum_at] ^= 1
    return base64.b64encode(image_bytes).decode("ascii")


def png_with_invalid_base64_character() -> str:
    encoded = image_base64()
    return f"{encoded[:40]}${encoded[40:]}"


def verify_only_truncated_jpeg_base64() -> str:
    image_bytes = base64.b64decode(image_base64("JPEG"))
    return base64.b64encode(image_bytes[:-1]).decode("ascii")


def verify_only_corrupt_webp_base64() -> str:
    image_bytes = bytearray(base64.b64decode(image_base64("WEBP")))
    image_bytes[30] ^= 0xFF
    return base64.b64encode(image_bytes).decode("ascii")


def final_line(
    *,
    image: str | None = None,
    done: bool = True,
) -> bytes:
    payload: dict[str, Any] = {"done": done, "done_reason": "stop"}
    if image is not None:
        payload["image"] = image
    return json.dumps(payload, separators=(",", ":")).encode() + b"\n"


async def no_progress(_: int) -> None:
    pass


class ProgressRecorder:
    def __init__(self) -> None:
        self.values: list[int] = []

    async def __call__(self, value: int) -> None:
        self.values.append(value)


def app_error_snapshot(error: AppError) -> dict[str, object]:
    return {
        "code": error.code,
        "message": error.message,
        "status_code": error.status_code,
        "retryable": error.retryable,
        "details": error.details,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("steps", "expected_payload"),
    [
        (
            None,
            {
                "model": "x/z-image-turbo:fp8",
                "prompt": "Indian tomato curry",
                "stream": True,
                "width": 512,
                "height": 384,
            },
        ),
        (
            9,
            {
                "model": "x/z-image-turbo:fp8",
                "prompt": "Indian tomato curry",
                "stream": True,
                "width": 512,
                "height": 384,
                "steps": 9,
            },
        ),
    ],
)
async def test_generate_posts_the_exact_experimental_payload(
    steps: int | None,
    expected_payload: dict[str, object],
) -> None:
    transport = StreamingJsonTransport([final_line(image=image_base64())])
    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test/root/",
            model=Model.Z_IMAGE,
            client=client,
        )

        await generator.generate(
            ImageGenerationRequest(
                prompt="Indian tomato curry",
                width=512,
                height=384,
                steps=steps,
            ),
            no_progress,
        )

    assert len(transport.requests) == 1
    sent_request = transport.requests[0]
    assert sent_request.method == "POST"
    assert str(sent_request.url) == "http://ollama.test/root/api/generate"
    assert json.loads(sent_request.content) == expected_payload
    assert sent_request.headers["content-type"] == "application/json"


@pytest.mark.asyncio
async def test_generate_parses_split_and_combined_ndjson_chunks() -> None:
    body = (
        b'{"completed":1,"total":8,"done":false}\n'
        b"\n"
        b'{"completed":3,"total":8,"done":false}\n'
        + final_line(image=image_base64(size=(341, 299))).rstrip(b"\n")
    )
    boundaries = [1, 19, 48, 49, 73, len(body) - 11]
    chunks = [
        body[start:end]
        for start, end in zip(
            [0, *boundaries],
            [*boundaries, len(body)],
            strict=True,
        )
    ]
    transport = StreamingJsonTransport(chunks)
    progress = ProgressRecorder()

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        image = await generator.generate(
            ImageGenerationRequest(prompt="Tomato curry"),
            progress,
        )

    assert image.data == base64.b64decode(image_base64(size=(341, 299)))
    assert image.media_type == "image/png"
    assert (image.width, image.height) == (341, 299)
    assert progress.values == [12, 37, 100]


@pytest.mark.asyncio
async def test_generate_reports_monotonic_clamped_progress_before_completion() -> None:
    chunks = [
        (
            b'{"completed":2,"total":10,"done":false}\n'
            b'{"completed":1,"total":10,"done":false}\n'
            b'{"completed":2.9,"total":10,"done":false}\n'
            b'{"completed":50,"total":10,"done":false}\n'
            b'{"completed":4,"total":0,"done":false}\n'
        ),
        final_line(image=image_base64()),
    ]
    transport = StreamingJsonTransport(chunks)
    progress = ProgressRecorder()

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        await generator.generate(
            ImageGenerationRequest(prompt="Tomato curry"),
            progress,
        )

    assert progress.values == [20, 28, 99, 100]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_format", "expected_media_type"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
    ],
)
async def test_generate_returns_verified_supported_image_metadata(
    image_format: str,
    expected_media_type: str,
) -> None:
    encoded = image_base64(image_format, size=(333, 277))
    transport = StreamingJsonTransport([final_line(image=encoded)])
    progress = ProgressRecorder()

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        result = await generator.generate(
            ImageGenerationRequest(
                prompt="Tomato curry",
                width=768,
                height=768,
            ),
            progress,
        )

    assert result.data == base64.b64decode(encoded)
    assert result.media_type == expected_media_type
    assert (result.width, result.height) == (333, 277)
    assert progress.values == [100]


@pytest.mark.asyncio
async def test_generate_checks_http_status_without_leaking_response() -> None:
    secret_response = "private provider diagnostic"
    transport = StreamingJsonTransport(
        [secret_response.encode()],
        status_code=503,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.OLLAMA_UNAVAILABLE,
        "message": "Ollama is unavailable.",
        "status_code": 503,
        "retryable": True,
        "details": {},
    }
    assert secret_response not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "expected_progress"),
    [
        (
            [b'{"error":"private early provider detail","done":false}\n'],
            [],
        ),
        (
            [
                (
                    b'{"completed":2,"total":10,"done":false}\n'
                    b'{"error":"private midstream provider detail",'
                    b'"completed":8,"total":10,"done":false}\n'
                ),
                final_line(image=image_base64()),
            ],
            [20],
        ),
        (
            [
                (
                    b'{"error":"private terminal provider detail","done":true,'
                    b'"image":"' + image_base64().encode() + b'"}\n'
                )
            ],
            [],
        ),
    ],
    ids=["early", "midstream", "terminal"],
)
async def test_provider_error_frame_is_safe_retryable_ollama_unavailable(
    chunks: list[bytes],
    expected_progress: list[int],
) -> None:
    transport = StreamingJsonTransport(chunks)
    progress = ProgressRecorder()

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.OLLAMA_UNAVAILABLE,
        "message": "Ollama is unavailable.",
        "status_code": 503,
        "retryable": True,
        "details": {},
    }
    assert progress.values == expected_progress
    assert "private" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded_image",
    [
        pytest.param(verify_only_truncated_jpeg_base64(), id="truncated-jpeg"),
        pytest.param(verify_only_corrupt_webp_base64(), id="corrupt-webp"),
    ],
)
async def test_verify_only_image_that_fails_full_decode_is_rejected(
    encoded_image: str,
) -> None:
    image_bytes = base64.b64decode(encoded_image)
    with Image.open(BytesIO(image_bytes)) as image:
        image.verify()

    transport = StreamingJsonTransport([final_line(image=encoded_image)])
    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.ARTIFACT_FAILURE,
        "message": "The generated image artifact is invalid.",
        "status_code": 502,
        "retryable": False,
        "details": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        [b'{"done":tru private provider diagnostic}\n'],
        [b'["not","an","object"]\n'],
        [b'{"completed":1,"total":2,"done":false}\n'],
        [final_line(image=image_base64(), done=False)],
        [final_line()],
        [final_line(image=png_with_invalid_base64_character())],
        [final_line(image=base64.b64encode(b"not an image").decode("ascii"))],
        [final_line(image=png_with_bad_checksum_base64())],
        [final_line(image=image_base64("GIF"))],
    ],
    ids=[
        "malformed-json",
        "non-object-json",
        "missing-final-response",
        "image-without-done",
        "missing-final-image",
        "invalid-base64",
        "invalid-image-bytes",
        "corrupt-image-checksum",
        "unsupported-image-format",
    ],
)
async def test_invalid_provider_output_is_a_safe_non_retryable_artifact_failure(
    chunks: list[bytes],
) -> None:
    transport = StreamingJsonTransport(chunks)
    progress = ProgressRecorder()

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.ARTIFACT_FAILURE,
        "message": "The generated image artifact is invalid.",
        "status_code": 502,
        "retryable": False,
        "details": {},
    }
    assert "private provider diagnostic" not in str(raised.value)
    assert 100 not in progress.values


@pytest.mark.asyncio
async def test_parser_rejected_integer_is_a_safe_artifact_failure() -> None:
    provider_number = "9" * 5_000
    transport = StreamingJsonTransport(
        [(f'{{"completed":{provider_number},"total":1,"done":false}}\n').encode()]
    )

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.ARTIFACT_FAILURE,
        "message": "The generated image artifact is invalid.",
        "status_code": 502,
        "retryable": False,
        "details": {},
    }
    assert provider_number not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("huge_field", ["completed", "total"])
async def test_float_overflowing_progress_is_a_safe_artifact_failure(
    huge_field: str,
) -> None:
    provider_number = "9" * 400
    completed = provider_number if huge_field == "completed" else "1"
    total = provider_number if huge_field == "total" else "1"
    transport = StreamingJsonTransport(
        [(f'{{"completed":{completed},"total":{total},"done":false}}\n').encode()]
    )

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.ARTIFACT_FAILURE,
        "message": "The generated image artifact is invalid.",
        "status_code": 502,
        "retryable": False,
        "details": {},
    }
    assert provider_number not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_location", ["request", "stream"])
async def test_network_failure_is_mapped_to_ollama_unavailable(
    failure_location: str,
) -> None:
    network_error = httpx.ConnectError("private-host:11434 refused")
    transport = StreamingJsonTransport(
        request_error=network_error if failure_location == "request" else None,
        stream_error=network_error if failure_location == "stream" else None,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.OLLAMA_UNAVAILABLE,
        "message": "Ollama is unavailable.",
        "status_code": 503,
        "retryable": True,
        "details": {},
    }
    assert "private-host" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_location", ["request", "stream"])
@pytest.mark.parametrize(
    "timeout_error",
    [
        httpx.ReadTimeout("private timeout detail"),
        TimeoutError("private timeout detail"),
    ],
)
async def test_timeout_is_mapped_to_operation_timed_out(
    failure_location: str,
    timeout_error: Exception,
) -> None:
    transport = StreamingJsonTransport(
        request_error=timeout_error if failure_location == "request" else None,
        stream_error=timeout_error if failure_location == "stream" else None,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert app_error_snapshot(raised.value) == {
        "code": ErrorCode.OPERATION_TIMED_OUT,
        "message": "Image generation timed out.",
        "status_code": 504,
        "retryable": True,
        "details": {},
    }
    assert "private timeout detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_cancellation_propagates_unchanged() -> None:
    transport = StreamingJsonTransport(request_error=asyncio.CancelledError())

    async with httpx.AsyncClient(transport=transport) as client:
        generator = OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            client=client,
        )

        with pytest.raises(asyncio.CancelledError):
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )


@pytest.mark.asyncio
async def test_injected_client_remains_owned_by_the_caller() -> None:
    transport = StreamingJsonTransport([final_line(image=image_base64())])
    client = httpx.AsyncClient(transport=transport)
    generator = OllamaImageGenerator(
        base_url="http://ollama.test",
        model="configured-image-model",
        client=client,
    )

    await generator.generate(
        ImageGenerationRequest(prompt="Tomato curry"),
        no_progress,
    )
    await generator.aclose()

    assert client.is_closed is False
    assert transport.close_calls == 0

    await client.aclose()
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_internally_created_client_closes_once_through_async_lifecycle() -> None:
    transport = StreamingJsonTransport([final_line(image=image_base64())])

    async with OllamaImageGenerator(
        base_url="http://ollama.test",
        model="configured-image-model",
        transport=transport,
        timeout_seconds=13,
    ) as generator:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato curry"),
            no_progress,
        )

    assert transport.close_calls == 1

    await generator.aclose()
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_async_lifecycle_closes_internally_created_client_after_failure() -> None:
    transport = StreamingJsonTransport(
        request_error=httpx.ConnectError("connection refused")
    )

    with pytest.raises(AppError):
        async with OllamaImageGenerator(
            base_url="http://ollama.test",
            model="configured-image-model",
            transport=transport,
        ) as generator:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                no_progress,
            )

    assert transport.close_calls == 1
