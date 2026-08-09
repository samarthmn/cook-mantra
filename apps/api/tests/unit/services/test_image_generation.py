import asyncio
import base64
import logging
import math
from io import BytesIO
from typing import cast

import pytest
from PIL import Image

from core.errors import AppError, ErrorCode
from core.logging import cause_chain, log_context
from core.runtime_config import RuntimeConfigurationSnapshot, get_runtime_snapshot
from domain.images import ImageGenerationRequest
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ImageOutputFormat,
    ImageTuning,
    ModelResult,
    ModelUsage,
    ProviderDiscoveryResult,
    ProviderError,
    ProviderErrorKind,
    ProviderImageOutput,
    ProviderName,
)
from services.image_generation import RuntimeImageGenerator, validate_generated_raster
from services.model_runtime import InProcessModelUsageJournal
from services.provider_errors import ProviderInvocationError

MAX_GENERATED_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ENCODED_IMAGE_CHARS = 4 * math.ceil(MAX_GENERATED_IMAGE_BYTES / 3)


def raster_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (256, 256),
) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "orange").save(buffer, format=image_format)
    return buffer.getvalue()


def encoded_output(
    data: bytes,
    *,
    media_type: str | None,
) -> ProviderImageOutput:
    return ProviderImageOutput(
        base64_data=base64.b64encode(data).decode("ascii"),
        media_type=media_type,
    )


def image_tuning(
    output_format: ImageOutputFormat,
    *,
    width: int = 256,
    height: int = 256,
) -> ImageTuning:
    return ImageTuning(
        width=width,
        height=height,
        output_format=output_format,
    )


@pytest.mark.parametrize(
    ("image_format", "output_format", "declared_media_type", "canonical_type"),
    [
        ("PNG", ImageOutputFormat.PNG, " IMAGE/PNG ", "image/png"),
        ("JPEG", ImageOutputFormat.JPEG, "image/jpeg", "image/jpeg"),
        ("WEBP", ImageOutputFormat.WEBP, None, "image/webp"),
    ],
)
def test_validator_accepts_one_exact_static_raster(
    image_format: str,
    output_format: ImageOutputFormat,
    declared_media_type: str | None,
    canonical_type: str,
) -> None:
    data = raster_bytes(image_format)

    result = validate_generated_raster(
        encoded_output(data, media_type=declared_media_type),
        image_tuning(output_format),
    )

    assert result.data == data
    assert result.media_type == canonical_type
    assert (result.width, result.height) == (256, 256)


@pytest.mark.parametrize(
    "raw_base64",
    [
        "",
        "not base64!",
        "YWJjZA=",
        "data:image/png;base64,AA==",
        "https://images.provider.invalid/output.png",
        "AA==\u2603",
    ],
)
def test_validator_rejects_non_raw_strict_ascii_base64(raw_base64: str) -> None:
    assert_invalid_output(
        ProviderImageOutput(base64_data=raw_base64, media_type="image/png"),
        image_tuning(ImageOutputFormat.PNG),
    )


def test_validator_rejects_encoded_input_over_limit_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decode_called = False

    def unexpected_decode(*_: object, **__: object) -> bytes:
        nonlocal decode_called
        decode_called = True
        return b""

    monkeypatch.setattr(base64, "b64decode", unexpected_decode)
    output = ProviderImageOutput(
        base64_data="A" * (MAX_ENCODED_IMAGE_CHARS + 1),
        media_type="image/png",
    )

    assert_invalid_output(output, image_tuning(ImageOutputFormat.PNG))

    assert decode_called is False


def test_validator_rejects_decoded_input_over_ten_mibibytes() -> None:
    oversized = b"x" * (MAX_GENERATED_IMAGE_BYTES + 1)

    assert_invalid_output(
        encoded_output(oversized, media_type="image/png"),
        image_tuning(ImageOutputFormat.PNG),
    )


def test_validator_decodes_the_provider_payload_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = raster_bytes("PNG")
    real_decode = base64.b64decode
    decode_calls = 0

    def count_decode(*args: object, **kwargs: object) -> bytes:
        nonlocal decode_calls
        decode_calls += 1
        return real_decode(*args, **kwargs)

    monkeypatch.setattr(base64, "b64decode", count_decode)

    validate_generated_raster(
        encoded_output(data, media_type="image/png"),
        image_tuning(ImageOutputFormat.PNG),
    )

    assert decode_calls == 1


@pytest.mark.parametrize(
    ("image_format", "declared_media_type"),
    [
        ("GIF", "image/gif"),
        ("PNG", "image/svg+xml"),
    ],
)
def test_validator_rejects_unlisted_declared_or_detected_formats(
    image_format: str,
    declared_media_type: str,
) -> None:
    assert_invalid_output(
        encoded_output(
            raster_bytes(image_format),
            media_type=declared_media_type,
        ),
        image_tuning(ImageOutputFormat.PNG),
    )


def test_validator_rejects_declared_media_type_that_disagrees_with_raster() -> None:
    assert_invalid_output(
        encoded_output(raster_bytes("PNG"), media_type="image/jpeg"),
        image_tuning(ImageOutputFormat.PNG),
    )


def test_validator_rejects_dimensions_that_differ_from_tuning() -> None:
    assert_invalid_output(
        encoded_output(
            raster_bytes("PNG", size=(257, 256)),
            media_type="image/png",
        ),
        image_tuning(ImageOutputFormat.PNG),
    )


def test_validator_rejects_detected_format_that_differs_from_tuning() -> None:
    assert_invalid_output(
        encoded_output(raster_bytes("PNG"), media_type="image/png"),
        image_tuning(ImageOutputFormat.WEBP),
    )


def test_validator_rejects_truncated_raster() -> None:
    jpeg = raster_bytes("JPEG")

    assert_invalid_output(
        encoded_output(jpeg[:-32], media_type="image/jpeg"),
        image_tuning(ImageOutputFormat.JPEG),
    )


def test_validator_treats_pillow_decompression_warnings_as_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 40_000)

    assert_invalid_output(
        encoded_output(raster_bytes("PNG"), media_type="image/png"),
        image_tuning(ImageOutputFormat.PNG),
    )


def test_validator_rejects_animated_raster() -> None:
    buffer = BytesIO()
    frames = [
        Image.new("RGB", (256, 256), "orange"),
        Image.new("RGB", (256, 256), "blue"),
    ]
    frames[0].save(
        buffer,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    animated = buffer.getvalue()
    with Image.open(BytesIO(animated)) as image:
        assert image.n_frames == 2

    assert_invalid_output(
        encoded_output(animated, media_type="image/webp"),
        image_tuning(ImageOutputFormat.WEBP),
    )


def test_invalid_output_has_detached_safe_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_canary = "private-output-canary://not-an-image"

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ProviderInvocationError) as raised,
    ):
        validate_generated_raster(
            ProviderImageOutput(
                base64_data=private_canary,
                media_type="image/png",
            ),
            image_tuning(ImageOutputFormat.PNG),
        )

    rendered = " ".join(
        [
            str(raised.value),
            repr(raised.value),
            repr(raised.value.error),
            repr(cause_chain(raised.value)),
            caplog.text,
        ]
    )
    assert raised.value.error.kind is ProviderErrorKind.INVALID_OUTPUT
    assert raised.value.error.retryable is False
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert private_canary not in rendered


class RecordingImageRuntime:
    def __init__(
        self,
        inspection: ProviderDiscoveryResult,
        result: ModelResult[ProviderImageOutput] | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.inspection = inspection
        self.result = result
        self.failure = failure
        self.inspect_calls = 0
        self.generation_calls: list[tuple[str, ImageTuning]] = []

    async def inspect(self) -> ProviderDiscoveryResult:
        self.inspect_calls += 1
        return self.inspection

    async def generate_image(
        self,
        prompt: str,
        *,
        tuning: ImageTuning,
    ) -> ModelResult[ProviderImageOutput]:
        self.generation_calls.append((prompt, tuning))
        if self.failure is not None:
            raise self.failure
        assert self.result is not None
        return self.result


def runtime_snapshot(
    tuning: ImageTuning,
    *,
    enabled: bool = True,
    provider: ProviderName = ProviderName.OLLAMA,
    capabilities: frozenset[Capability] = frozenset({Capability.IMAGE_OUTPUT}),
) -> RuntimeConfigurationSnapshot:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    selection = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={
            "enabled": enabled,
            "provider": provider,
            "model": "exact-image-model",
            "capabilities": capabilities,
            "image_tuning": tuning,
        }
    )
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "roles": {
                        **config.roles,
                        AgentRole.IMAGE_GENERATOR: selection,
                    }
                }
            )
        }
    )


def ready_inspection(
    *,
    provider: ProviderName = ProviderName.OLLAMA,
) -> ProviderDiscoveryResult:
    return ProviderDiscoveryResult(
        provider=provider,
        models={
            "exact-image-model": DiscoveredModel(
                model="exact-image-model",
                available=True,
                capabilities=(Capability.IMAGE_OUTPUT,),
            )
        },
        available_models=("exact-image-model",),
    )


@pytest.mark.asyncio
async def test_runtime_generator_selects_exact_runtime_and_records_usage() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    output = encoded_output(raster_bytes("PNG"), media_type="image/png")
    runtime = RecordingImageRuntime(
        ready_inspection(),
        ModelResult(
            output=output,
            usage=ModelUsage(
                input_tokens=17,
                output_tokens=3,
                provider_metadata={"private": "provider-metadata-canary"},
            ),
        ),
    )
    fallback = RecordingImageRuntime(
        ready_inspection(provider=ProviderName.OPENROUTER),
        ModelResult(output=output),
    )
    journal = InProcessModelUsageJournal()
    progress_values: list[int] = []

    async def progress(value: int) -> None:
        progress_values.append(value)

    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={
            ProviderName.OLLAMA: runtime,
            ProviderName.OPENROUTER: fallback,
        },
        usage_journal=journal,
    )
    with log_context(session_id="session-1", job_id="job-1"):
        result = await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            progress,
        )

    assert result.data == base64.b64decode(output.base64_data)
    assert runtime.inspect_calls == 1
    assert runtime.generation_calls == [("Tomato masala", tuning)]
    assert fallback.inspect_calls == 0
    assert fallback.generation_calls == []
    assert progress_values == [100]
    events = await journal.snapshot()
    assert len(events) == 1
    event = events[0]
    assert event.role is AgentRole.IMAGE_GENERATOR
    assert event.provider is ProviderName.OLLAMA
    assert event.model == "exact-image-model"
    assert event.usage.input_tokens == 17
    assert event.usage.output_tokens == 3
    assert event.usage.provider_metadata == {}
    assert event.session_id == "session-1"
    assert event.job_id == "job-1"
    assert "provider-metadata-canary" not in repr(event)


@pytest.mark.asyncio
async def test_runtime_generator_does_not_call_a_disabled_image_runtime() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    runtime = RecordingImageRuntime(ready_inspection())
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning, enabled=False),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    assert raised.value.code is ErrorCode.MODEL_CAPABILITY_MISSING
    assert runtime.inspect_calls == 0
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_ignores_an_injected_codex_image_invoker() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    runtime = RecordingImageRuntime(ready_inspection(provider=ProviderName.CODEX))
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning, provider=ProviderName.CODEX),
        image_runtimes={ProviderName.CODEX: runtime},
    )
    progress_values: list[int] = []

    async def progress(value: int) -> None:
        progress_values.append(value)

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            progress,
        )

    assert raised.value.code is ErrorCode.MODEL_CAPABILITY_MISSING
    assert progress_values == []
    assert runtime.inspect_calls == 0
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_rejects_tuning_outside_role_config() -> None:
    configured_tuning = image_tuning(ImageOutputFormat.PNG)
    alternate_tuning = image_tuning(ImageOutputFormat.PNG, width=512)
    runtime = RecordingImageRuntime(ready_inspection())
    generator = RuntimeImageGenerator(
        runtime_snapshot(configured_tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(
                prompt="Tomato masala",
                tuning=alternate_tuning,
            ),
            lambda _: asyncio.sleep(0),
        )

    assert raised.value.code is ErrorCode.MODEL_CONFIGURATION_INVALID
    assert runtime.inspect_calls == 0
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_requires_exact_discovered_model() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    inspection = ready_inspection().model_copy(
        update={
            "models": {
                "exact-image-model": DiscoveredModel(
                    model="exact-image-model",
                    available=False,
                    capabilities=(),
                    error=ProviderErrorKind.CAPABILITY_MISSING,
                )
            },
            "available_models": (),
        }
    )
    runtime = RecordingImageRuntime(inspection)
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    assert raised.value.code is ErrorCode.MODEL_CAPABILITY_MISSING
    assert runtime.inspect_calls == 1
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_rejects_discovery_from_a_different_provider() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    output = encoded_output(raster_bytes("PNG"), media_type="image/png")
    runtime = RecordingImageRuntime(
        ready_inspection(provider=ProviderName.OPENROUTER),
        ModelResult(output=output),
    )
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    assert raised.value.code is ErrorCode.PROVIDER_PROTOCOL_ERROR
    assert runtime.inspect_calls == 1
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_detaches_malformed_discovery_error() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    private_canary = "private-malformed-discovery-canary"
    runtime = RecordingImageRuntime(
        cast(ProviderDiscoveryResult, {"private": private_canary})
    )
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    rendered = " ".join(
        [str(raised.value), repr(raised.value), repr(cause_chain(raised.value))]
    )
    assert raised.value.code is ErrorCode.PROVIDER_PROTOCOL_ERROR
    assert private_canary not in rendered
    assert runtime.inspect_calls == 1
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_detaches_malformed_result_error() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    private_canary = "private-malformed-result-canary"
    runtime = RecordingImageRuntime(
        ready_inspection(),
        cast(ModelResult[ProviderImageOutput], {"private": private_canary}),
    )
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    rendered = " ".join(
        [str(raised.value), repr(raised.value), repr(cause_chain(raised.value))]
    )
    assert raised.value.code is ErrorCode.PROVIDER_PROTOCOL_ERROR
    assert private_canary not in rendered
    assert runtime.inspect_calls == 1
    assert len(runtime.generation_calls) == 1


@pytest.mark.asyncio
async def test_runtime_generator_rejects_malformed_usage_before_validation() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    private_canary = "private-malformed-usage-canary"
    runtime = RecordingImageRuntime(
        ready_inspection(),
        ModelResult(
            output=encoded_output(raster_bytes("PNG"), media_type="image/png"),
            usage=cast(ModelUsage, private_canary),
        ),
    )
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    rendered = " ".join(
        [str(raised.value), repr(raised.value), repr(cause_chain(raised.value))]
    )
    assert raised.value.code is ErrorCode.PROVIDER_PROTOCOL_ERROR
    assert private_canary not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_from", ["configuration", "discovery"])
async def test_runtime_generator_requires_image_output_capability_on_both_sides(
    missing_from: str,
) -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    inspection = ready_inspection()
    snapshot = runtime_snapshot(tuning)
    if missing_from == "configuration":
        snapshot = runtime_snapshot(tuning, capabilities=frozenset())
    else:
        inspection = inspection.model_copy(
            update={
                "models": {
                    "exact-image-model": DiscoveredModel(
                        model="exact-image-model",
                        available=True,
                        capabilities=(),
                    )
                }
            }
        )
    runtime = RecordingImageRuntime(
        inspection,
        ModelResult(output=encoded_output(raster_bytes("PNG"), media_type="image/png")),
    )
    generator = RuntimeImageGenerator(
        snapshot,
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    assert raised.value.code is ErrorCode.MODEL_CAPABILITY_MISSING
    assert runtime.inspect_calls == 1
    assert runtime.generation_calls == []


@pytest.mark.asyncio
async def test_runtime_generator_records_usage_before_invalid_raster() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    private_canary = "private-invalid-image-canary"
    runtime = RecordingImageRuntime(
        ready_inspection(),
        ModelResult(
            output=ProviderImageOutput(
                base64_data=private_canary,
                media_type="image/png",
            ),
            usage=ModelUsage(input_tokens=9, output_tokens=1),
        ),
    )
    journal = InProcessModelUsageJournal()
    progress_values: list[int] = []
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
        usage_journal=journal,
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            progress_values.append,  # type: ignore[arg-type]
        )

    rendered = " ".join(
        [str(raised.value), repr(raised.value), repr(cause_chain(raised.value))]
    )
    assert raised.value.code is ErrorCode.ARTIFACT_FAILURE
    assert raised.value.status_code == 502
    assert raised.value.retryable is False
    assert private_canary not in rendered
    assert progress_values == []
    assert len(await journal.snapshot()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_code", "expected_status"),
    [
        (ProviderErrorKind.UNAVAILABLE, ErrorCode.PROVIDER_UNAVAILABLE, 503),
        (
            ProviderErrorKind.AUTHENTICATION_FAILED,
            ErrorCode.PROVIDER_AUTHENTICATION_FAILED,
            401,
        ),
        (ProviderErrorKind.RATE_LIMITED, ErrorCode.PROVIDER_RATE_LIMITED, 429),
        (
            ProviderErrorKind.PAYMENT_REQUIRED,
            ErrorCode.PROVIDER_PAYMENT_REQUIRED,
            402,
        ),
        (
            ProviderErrorKind.CAPABILITY_MISSING,
            ErrorCode.MODEL_CAPABILITY_MISSING,
            503,
        ),
        (ProviderErrorKind.PROTOCOL_ERROR, ErrorCode.PROVIDER_PROTOCOL_ERROR, 502),
        (ProviderErrorKind.TIMED_OUT, ErrorCode.OPERATION_TIMED_OUT, 504),
        (ProviderErrorKind.INVALID_OUTPUT, ErrorCode.ARTIFACT_FAILURE, 502),
    ],
)
async def test_runtime_generator_maps_normalized_provider_failure_without_leaking(
    kind: ProviderErrorKind,
    expected_code: ErrorCode,
    expected_status: int,
) -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    private_canary = "private-provider-failure-canary"
    runtime = RecordingImageRuntime(
        ready_inspection(),
        failure=ProviderInvocationError(
            ProviderError(
                kind=kind,
                message=private_canary,
                retryable=kind
                in {
                    ProviderErrorKind.UNAVAILABLE,
                    ProviderErrorKind.RATE_LIMITED,
                    ProviderErrorKind.TIMED_OUT,
                },
                provider=ProviderName.OLLAMA,
                role=AgentRole.IMAGE_GENERATOR,
            )
        ),
    )
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
    )

    with pytest.raises(AppError) as raised:
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            lambda _: asyncio.sleep(0),
        )

    rendered = " ".join(
        [str(raised.value), repr(raised.value), repr(cause_chain(raised.value))]
    )
    assert raised.value.code is expected_code
    assert raised.value.status_code == expected_status
    assert private_canary not in rendered
    assert len(runtime.generation_calls) == 1


@pytest.mark.asyncio
async def test_runtime_generator_propagates_cancellation() -> None:
    tuning = image_tuning(ImageOutputFormat.PNG)
    runtime = RecordingImageRuntime(
        ready_inspection(),
        failure=asyncio.CancelledError(),
    )
    journal = InProcessModelUsageJournal()
    progress_values: list[int] = []
    generator = RuntimeImageGenerator(
        runtime_snapshot(tuning),
        image_runtimes={ProviderName.OLLAMA: runtime},
        usage_journal=journal,
    )

    async def progress(value: int) -> None:
        progress_values.append(value)

    with pytest.raises(asyncio.CancelledError):
        await generator.generate(
            ImageGenerationRequest(prompt="Tomato masala", tuning=tuning),
            progress,
        )

    assert progress_values == []
    assert await journal.snapshot() == ()


def assert_invalid_output(
    output: ProviderImageOutput,
    tuning: ImageTuning,
) -> None:
    with pytest.raises(ProviderInvocationError) as raised:
        validate_generated_raster(output, tuning)

    assert raised.value.error.kind is ProviderErrorKind.INVALID_OUTPUT
    assert raised.value.error.retryable is False
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
