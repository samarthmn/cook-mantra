import hashlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from io import BytesIO

import httpx
import pytest
from PIL import Image

from core.errors import AppError, ErrorCode
from domain.images import ImageGenerationRequest
from services.image_generation import BeastImageGenerator


def image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (320, 288),
) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "orange").save(buffer, format=image_format)
    return buffer.getvalue()


def job(
    state: str,
    *,
    progress: int | float = 0,
    outputs: list[dict[str, object]] | None = None,
    error: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": "job-123",
        "model_id": "z-image-turbo",
        "generation_type": "image",
        "state": state,
        "progress": progress,
        "outputs": outputs or [],
        "error": error,
    }


def output_for(data: bytes, *, sha256: str | None = None) -> dict[str, object]:
    return {
        "path": "jobs/job-123/output.png",
        "mime_type": "image/png",
        "size_bytes": len(data),
        "sha256": sha256 or hashlib.sha256(data).hexdigest(),
    }


class ProgressRecorder:
    def __init__(self) -> None:
        self.values: list[int] = []

    async def __call__(self, value: int) -> None:
        self.values.append(value)


def app_error_snapshot(error: AppError) -> tuple[ErrorCode, int, bool]:
    return error.code, error.status_code, error.retryable


@pytest.mark.asyncio
async def test_generate_submits_polls_downloads_and_verifies_image() -> None:
    data = image_bytes(size=(341, 299))
    poll_responses = deque(
        [
            job("running", progress=0.37),
            job("completed", progress=100, outputs=[output_for(data)]),
        ]
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json=job("queued", progress=0))
        if request.url.path == "/root/v1/jobs/job-123/outputs/0":
            return httpx.Response(
                200, content=data, headers={"Content-Type": "image/png"}
            )
        return httpx.Response(200, json=poll_responses.popleft())

    progress = ProgressRecorder()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        generator = BeastImageGenerator(
            base_url="http://beast.test/root/",
            api_key="secret-beast-key",
            model="configured-image-model",
            client=client,
            poll_interval_seconds=0.001,
        )
        result = await generator.generate(
            ImageGenerationRequest(
                prompt="Indian tomato curry",
                width=512,
                height=384,
                steps=9,
            ),
            progress,
        )

    assert result.data == data
    assert result.media_type == "image/png"
    assert (result.width, result.height) == (341, 299)
    assert progress.values == [0, 37, 99, 100]
    assert progress.values == sorted(progress.values)
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET"]
    assert [request.url.path for request in requests] == [
        "/root/v1/images/generations",
        "/root/v1/jobs/job-123",
        "/root/v1/jobs/job-123",
        "/root/v1/jobs/job-123/outputs/0",
    ]
    assert all(
        request.headers["authorization"] == "Bearer secret-beast-key"
        for request in requests
    )
    assert json.loads(requests[0].content) == {
        "model": "configured-image-model",
        "prompt": "Indian tomato curry",
        "width": 512,
        "height": 384,
        "output_format": "png",
        "model_options": {"steps": 9},
    }


@pytest.mark.asyncio
async def test_submit_omits_model_options_when_steps_are_not_configured() -> None:
    data = image_bytes()
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json=job("completed", outputs=[output_for(data)]),
            )
        return httpx.Response(200, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        generator = BeastImageGenerator(
            base_url="http://beast.test",
            api_key="key",
            model="z-image-turbo",
            client=client,
        )
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato curry", steps=None),
            ProgressRecorder(),
        )

    assert "model_options" not in json.loads(requests[0].content)


@pytest.mark.asyncio
async def test_failed_job_maps_to_image_provider_unavailable() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json=job("queued"))
        return httpx.Response(
            200,
            json=job(
                "failed",
                error={
                    "code": "model_failed",
                    "retryable": False,
                    "detail": "private provider detail",
                },
            ),
        )

    generator = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=httpx.MockTransport(respond),
        poll_interval_seconds=0.001,
    )
    try:
        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                ProgressRecorder(),
            )
    finally:
        await generator.aclose()

    assert app_error_snapshot(raised.value) == (
        ErrorCode.IMAGE_PROVIDER_UNAVAILABLE,
        503,
        True,
    )
    assert "private provider detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_submit_problem_json_maps_to_image_provider_unavailable() -> None:
    problem = {
        "type": "about:blank",
        "title": "Request failed",
        "status": 422,
        "detail": "private rejection detail",
        "code": "invalid_model_options",
        "retryable": False,
    }

    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json=problem,
            headers={"Content-Type": "application/problem+json"},
        )

    generator = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                ProgressRecorder(),
            )
    finally:
        await generator.aclose()

    assert app_error_snapshot(raised.value) == (
        ErrorCode.IMAGE_PROVIDER_UNAVAILABLE,
        503,
        True,
    )
    assert problem["detail"] not in str(raised.value)


@pytest.mark.asyncio
async def test_connection_error_maps_to_image_provider_unavailable() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-host refused connection", request=request)

    generator = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=httpx.MockTransport(fail),
    )
    try:
        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                ProgressRecorder(),
            )
    finally:
        await generator.aclose()

    assert raised.value.code is ErrorCode.IMAGE_PROVIDER_UNAVAILABLE
    assert "private-host" not in str(raised.value)


@pytest.mark.asyncio
async def test_overall_timeout_includes_poll_wait() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json=job("queued"))

    generator = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=httpx.MockTransport(respond),
        timeout_seconds=0.001,
        poll_interval_seconds=1,
    )
    try:
        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                ProgressRecorder(),
            )
    finally:
        await generator.aclose()

    assert app_error_snapshot(raised.value) == (
        ErrorCode.OPERATION_TIMED_OUT,
        504,
        True,
    )


@pytest.mark.asyncio
async def test_sha256_mismatch_is_an_artifact_failure() -> None:
    data = image_bytes()
    bad_sha = "0" * 64

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json=job(
                    "completed",
                    outputs=[output_for(data, sha256=bad_sha)],
                ),
            )
        return httpx.Response(200, content=data)

    await _assert_artifact_failure(respond)


@pytest.mark.asyncio
async def test_size_mismatch_is_an_artifact_failure() -> None:
    data = image_bytes()
    output = output_for(data)
    output["size_bytes"] = len(data) + 1

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json=job("completed", outputs=[output]),
            )
        return httpx.Response(200, content=data)

    await _assert_artifact_failure(respond)


@pytest.mark.asyncio
async def test_undecodable_bytes_are_an_artifact_failure() -> None:
    data = b"this is not an image"

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json=job("completed", outputs=[output_for(data)]),
            )
        return httpx.Response(200, content=data)

    await _assert_artifact_failure(respond)


@pytest.mark.asyncio
async def test_empty_outputs_on_completed_job_are_an_artifact_failure() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json=job("completed", outputs=[]))

    await _assert_artifact_failure(respond)


@pytest.mark.asyncio
async def test_progress_normalizes_fraction_and_percentage_scales_monotonically() -> (
    None
):
    data = image_bytes()
    poll_responses = deque(
        [
            job("loading", progress=0.2),
            job("running", progress=18),
            job("running", progress=45.9),
            job("completed", progress=1, outputs=[output_for(data)]),
        ]
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json=job("queued", progress=-2))
        if request.url.path.endswith("/outputs/0"):
            return httpx.Response(200, content=data)
        return httpx.Response(200, json=poll_responses.popleft())

    progress = ProgressRecorder()
    generator = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=httpx.MockTransport(respond),
        poll_interval_seconds=0.001,
    )
    try:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato curry"),
            progress,
        )
    finally:
        await generator.aclose()

    assert progress.values == [0, 20, 45, 99, 100]


@pytest.mark.asyncio
async def test_aclose_closes_only_an_owned_client() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(202, json=job("failed")))
    owned = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=transport,
    )
    await owned.aclose()
    await owned.aclose()
    assert owned._client.is_closed is True

    injected_client = httpx.AsyncClient(transport=transport)
    injected = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        client=injected_client,
    )
    await injected.aclose()
    assert injected_client.is_closed is False
    await injected_client.aclose()


async def _assert_artifact_failure(
    respond: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> None:
    generator = BeastImageGenerator(
        base_url="http://beast.test",
        api_key="key",
        model="z-image-turbo",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(AppError) as raised:
            await generator.generate(
                ImageGenerationRequest(prompt="Tomato curry"),
                ProgressRecorder(),
            )
    finally:
        await generator.aclose()

    assert app_error_snapshot(raised.value) == (
        ErrorCode.ARTIFACT_FAILURE,
        502,
        False,
    )
