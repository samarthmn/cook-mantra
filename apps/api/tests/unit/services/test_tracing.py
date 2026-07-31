import asyncio
from base64 import b64encode
from typing import Any

import pytest
from langsmith import traceable, tracing_context

from core.config import PROJECT_ROOT, Agent, Settings
from core.logging import log_context
from domain.ingredients import ExtractionResult
from services.tracing import TracingService


def settings(**updates: object) -> Settings:
    return Settings(
        _env_file=None,
        artifact_root=PROJECT_ROOT / "tmp" / "tracing-service-test",
        **updates,
    )


def test_tracing_is_disabled_without_credentials() -> None:
    tracing = TracingService(settings(langsmith_tracing=True))

    assert tracing.enabled is False


@pytest.mark.parametrize(
    ("flag", "key"),
    [
        (False, None),
        (False, "test-key"),
        (True, None),
        (True, ""),
        (True, " \t\n "),
    ],
)
def test_disabled_combinations_never_construct_a_client(
    flag: bool,
    key: str | None,
) -> None:
    def unexpected_client(**_kwargs: object) -> object:
        raise AssertionError("Disabled tracing constructed a LangSmith client.")

    tracing = TracingService(
        settings(
            langsmith_tracing=flag,
            langsmith_api_key=key,
        ),
        client_factory=unexpected_client,
    )

    assert tracing.enabled is False
    assert (
        tracing.runnable_config(
            Agent.MASTER_CHEF,
            session_id="session-1",
            job_id="job-1",
        )
        == {}
    )


def test_client_construction_failure_cannot_expose_the_secret() -> None:
    canary = "LANGSMITH-KEY-CANARY"

    def failing_client(**kwargs: object) -> object:
        raise RuntimeError(f"failed with {kwargs['api_key']}")

    with pytest.raises(RuntimeError) as raised:
        TracingService(
            settings(
                langsmith_tracing=True,
                langsmith_api_key=canary,
            ),
            client_factory=failing_client,
        )

    assert str(raised.value) == "LangSmith tracing could not be initialized."
    assert raised.value.__cause__ is None
    assert canary not in repr(raised.value)


def test_runnable_config_has_only_safe_detached_metadata() -> None:
    client = RecordingClient()
    tracing = enabled_tracing(client)

    first = tracing.runnable_config(
        Agent.MASTER_CHEF,
        session_id="session-1",
        job_id="job-1",
        batch_number=2,
    )

    assert first == {
        "tags": ["cook-mantra", "master_chef"],
        "metadata": {
            "agent": "master_chef",
            "model": "qwen3.5:27b",
            "session_id": "session-1",
            "job_id": "job-1",
            "batch_number": 2,
        },
    }
    first["tags"].append("untrusted")  # type: ignore[union-attr]
    first["metadata"]["prompt"] = "must-not-stick"  # type: ignore[index]
    second = tracing.runnable_config(
        Agent.MASTER_CHEF,
        session_id="session-1",
        job_id="job-1",
        batch_number=2,
    )
    assert second == {
        "tags": ["cook-mantra", "master_chef"],
        "metadata": {
            "agent": "master_chef",
            "model": "qwen3.5:27b",
            "session_id": "session-1",
            "job_id": "job-1",
            "batch_number": 2,
        },
    }
    assert "test-key" not in repr(tracing)
    assert "test-key" not in repr(second)


@pytest.mark.parametrize(
    ("agent", "model"),
    [
        (Agent.INGREDIENT_EXTRACTION, "qwen3.5:9b"),
        (Agent.MASTER_CHEF, "qwen3.5:27b"),
        (Agent.NUTRITION, "gpt-oss:20b"),
        (Agent.IMAGE, "x/z-image-turbo:fp8"),
        (Agent.SPECIALIZED_RECIPE, "gemma4:26b"),
    ],
)
def test_runnable_config_maps_each_agent_to_its_exact_model(
    agent: Agent,
    model: str,
) -> None:
    tracing = enabled_tracing(RecordingClient())

    config = tracing.runnable_config(
        agent,
        session_id="session-1",
        job_id="job-1",
    )

    assert config["metadata"] == {
        "agent": agent.value,
        "model": model,
        "session_id": "session-1",
        "job_id": "job-1",
    }


@pytest.mark.asyncio
async def test_text_invocation_uses_task_local_job_correlation() -> None:
    tracing = enabled_tracing(RecordingClient())

    async def invoke(label: str) -> dict[str, object]:
        with log_context(session_id=f"session-{label}", job_id=f"job-{label}"):
            return await tracing.invoke_text(
                Agent.NUTRITION,
                lambda config: async_value(config),
            )

    first, second = await asyncio.gather(invoke("a"), invoke("b"))

    assert first["metadata"]["session_id"] == "session-a"  # type: ignore[index]
    assert first["metadata"]["job_id"] == "job-a"  # type: ignore[index]
    assert second["metadata"]["session_id"] == "session-b"  # type: ignore[index]
    assert second["metadata"]["job_id"] == "job-b"  # type: ignore[index]


@pytest.mark.asyncio
async def test_text_trace_uses_configured_client_project_and_metadata() -> None:
    client = RecordingClient()
    tracing = enabled_tracing(client)

    @traceable(name="safe-text-call")
    async def text_model(prompt: str) -> str:
        return f"answer:{prompt}"

    async def invoke(_config: dict[str, object]) -> str:
        return await text_model("safe prompt")

    with log_context(session_id="session-1", job_id="job-1"):
        result = await tracing.invoke_text(Agent.MASTER_CHEF, invoke)

    assert result == "answer:safe prompt"
    assert [call[0] for call in client.calls] == ["create", "update"]
    created = client.calls[0][1]
    assert created["session_name"] == "cook-mantra"
    assert created["tags"] == ["cook-mantra", "master_chef"]
    assert created["extra"]["metadata"] == {
        "agent": "master_chef",
        "model": "qwen3.5:27b",
        "session_id": "session-1",
        "job_id": "job-1",
        "ls_method": "traceable",
    }


def test_vision_trace_summary_contains_no_image_data() -> None:
    tracing = TracingService(
        settings(
            langsmith_tracing=True,
            langsmith_api_key="test-key",
        )
    )

    summary = tracing.summarize_vision_input(
        image=b"raw-image-bytes",
        media_type="image/png",
    )

    assert summary == {"media_type": "image/png", "byte_count": 15}
    assert "raw-image" not in repr(summary)
    assert "base64" not in repr(summary).lower()


@pytest.mark.parametrize(
    ("image", "media_type"),
    [
        (b"", "image/png"),
        (b"x", "text/plain"),
        ("not-bytes", "image/png"),
    ],
)
def test_vision_summary_rejects_untrusted_types_and_bounds(
    image: object,
    media_type: str,
) -> None:
    tracing = enabled_tracing(RecordingClient())

    with pytest.raises((TypeError, ValueError)):
        tracing.summarize_vision_input(image=image, media_type=media_type)


@pytest.mark.asyncio
async def test_vision_callback_boundary_captures_only_safe_summaries() -> None:
    canary = b"VISION-CANARY-never-cross-this-boundary"
    encoded_canary = b64encode(canary).decode("ascii")
    client = RecordingClient()
    tracing = enabled_tracing(client)
    nested_model_traces = 0

    @traceable(name="automatic-vision-model")
    async def automatically_traced_model(message: str) -> ExtractionResult:
        nonlocal nested_model_traces
        nested_model_traces += 1
        assert encoded_canary in message
        return ExtractionResult(
            detected=[{"name": "Tomato", "confidence": 0.9}],
            warnings=["uncertain edge"],
        )

    async def local_model_call() -> ExtractionResult:
        return await automatically_traced_model(
            f"data:image/png;base64,{encoded_canary}"
        )

    with (
        tracing_context(enabled=False),
        log_context(session_id="session-1", job_id="job-1"),
    ):
        result = await tracing.invoke_vision(
            canary,
            "image/png",
            local_model_call,
        )

    assert result.detected[0].name == "Tomato"
    assert nested_model_traces == 1
    assert [call[0] for call in client.calls] == ["create", "update"]
    captured = repr(client.calls)
    assert canary.decode() not in captured
    assert encoded_canary not in captured
    assert "Tomato" not in captured
    assert "'inputs': {'media_type': 'image/png', 'byte_count': 39}" in captured
    assert "'outputs': {'detected_count': 1, 'warning_count': 1}" in captured


@pytest.mark.asyncio
async def test_vision_trace_client_failure_never_retries_with_raw_input() -> None:
    canary = b"VISION-CANARY-client-failure"
    client = RecordingClient(fail_writes=True)
    tracing = enabled_tracing(client)
    calls = 0

    async def local_model_call() -> ExtractionResult:
        nonlocal calls
        calls += 1
        return ExtractionResult(detected=[])

    with log_context(session_id="session-1", job_id="job-1"):
        result = await tracing.invoke_vision(canary, "image/png", local_model_call)

    assert result.detected == []
    assert calls == 1
    assert canary.decode() not in repr(client.calls)


@pytest.mark.asyncio
async def test_vision_trace_error_path_never_captures_exception_image_data() -> None:
    canary = b"VISION-CANARY-exception-path"
    client = RecordingClient()
    tracing = enabled_tracing(client)

    async def local_model_call() -> ExtractionResult:
        raise RuntimeError(canary.decode())

    with (
        log_context(session_id="session-1", job_id="job-1"),
        pytest.raises(RuntimeError, match="VISION-CANARY"),
    ):
        await tracing.invoke_vision(canary, "image/png", local_model_call)

    captured = repr(client.calls)
    assert canary.decode() not in captured
    assert client.calls[-1][1]["error"] is None


@pytest.mark.asyncio
async def test_tracing_context_exits_after_success_error_and_cancellation() -> None:
    client = RecordingClient()
    tracing = enabled_tracing(client)

    async def succeed(config: dict[str, object]) -> str:
        assert config["metadata"]["job_id"] == "job-1"  # type: ignore[index]
        return "ok"

    async def fail(_config: dict[str, object]) -> str:
        raise RuntimeError("local failure")

    async def cancel(_config: dict[str, object]) -> str:
        raise asyncio.CancelledError

    with tracing_context(enabled=False):
        with log_context(session_id="session-1", job_id="job-1"):
            assert await tracing.invoke_text(Agent.MASTER_CHEF, succeed) == "ok"
            with pytest.raises(RuntimeError, match="local failure"):
                await tracing.invoke_text(Agent.MASTER_CHEF, fail)
            with pytest.raises(asyncio.CancelledError):
                await tracing.invoke_text(Agent.MASTER_CHEF, cancel)

        @traceable(name="outside-service-context")
        async def outside() -> None:
            return None

        await outside()

    assert client.calls == []


async def async_value(value: dict[str, object]) -> dict[str, object]:
    return value


class RecordingClient:
    otel_exporter = None
    tracing_sample_rate = None

    def __init__(self, *, fail_writes: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_writes = fail_writes

    def create_run(self, **kwargs: Any) -> None:
        self.calls.append(("create", kwargs))
        if self.fail_writes:
            raise RuntimeError("safe trace write failed")

    def update_run(self, **kwargs: Any) -> None:
        self.calls.append(("update", kwargs))
        if self.fail_writes:
            raise RuntimeError("safe trace write failed")


def enabled_tracing(client: RecordingClient) -> TracingService:
    def factory(**_kwargs: object) -> RecordingClient:
        return client

    return TracingService(
        settings(
            langsmith_tracing=True,
            langsmith_api_key="test-key",
        ),
        client_factory=factory,
    )
