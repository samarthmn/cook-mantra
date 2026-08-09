"""Optional LangSmith tracing with an explicit vision-data privacy boundary."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, cast

from langsmith import Client, traceable, tracing_context

from core.config import Agent, Settings
from core.logging import current_log_context
from domain.ingredients import ExtractionResult
from domain.model_runtime import (
    AgentRole,
    ModelResult,
    ProviderName,
)

type RunnableConfig = dict[str, Any]
type ClientFactory = Callable[..., object]

_BATCH_NUMBER: ContextVar[int | None] = ContextVar(
    "cook_mantra_trace_batch_number",
    default=None,
)
_VISION_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def _safe_text_model_inputs(inputs: dict[str, object]) -> dict[str, str]:
    """Allowlist provider-neutral identity; never trace prompts or operations."""
    role = inputs.get("role")
    provider = inputs.get("provider")
    model = inputs.get("model")
    return {
        "role": role.value if isinstance(role, AgentRole) else "invalid",
        "provider": (
            provider.value if isinstance(provider, ProviderName) else "invalid"
        ),
        "model": model if isinstance(model, str) else "invalid",
    }


def _safe_text_model_outputs(output: object) -> dict[str, int]:
    """Reduce provider output to normalized aggregate usage."""
    if not isinstance(output, ModelResult) or output.usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": output.usage.input_tokens,
        "output_tokens": output.usage.output_tokens,
        "total_tokens": output.usage.total_tokens,
    }


@traceable(
    name="model_invocation",
    run_type="llm",
    process_inputs=_safe_text_model_inputs,
    process_outputs=_safe_text_model_outputs,
    exceptions_to_handle=(BaseException,),
)
async def invoke_trace_safe_text_model[ResultT](
    *,
    role: AgentRole,
    provider: ProviderName,
    model: str,
    operation: Callable[[], Awaitable[ModelResult[ResultT]]],
) -> ModelResult[ResultT]:
    """Trace a native model call without exposing its messages or response."""
    return await operation()


def _safe_vision_inputs(inputs: dict[str, object]) -> dict[str, object]:
    """Allowlist the only vision input fields that a trace may receive."""
    image = inputs.get("image")
    media_type = inputs.get("media_type")
    return {
        "media_type": media_type if isinstance(media_type, str) else "invalid",
        "byte_count": len(image) if isinstance(image, bytes) else 0,
    }


def _safe_vision_outputs(output: object) -> dict[str, int]:
    """Reduce a vision result to aggregate counts before trace serialization."""
    if not isinstance(output, ExtractionResult):
        return {"detected_count": 0, "warning_count": 0}
    return {
        "detected_count": len(output.detected),
        "warning_count": len(output.warnings),
    }


@traceable(
    name="ingredient_extraction",
    run_type="chain",
    process_inputs=_safe_vision_inputs,
    process_outputs=_safe_vision_outputs,
    exceptions_to_handle=(BaseException,),
)
async def _invoke_trace_safe_vision(
    *,
    image: bytes,
    media_type: str,
    operation: Callable[[], Awaitable[ExtractionResult]],
) -> ExtractionResult:
    """Trace safe summaries while suppressing nested tracing around raw image data."""
    with tracing_context(enabled=False):
        return await operation()


@contextmanager
def trace_batch_number(batch_number: int) -> Iterator[None]:
    """Attach a validated option batch number to text calls in this task only."""
    if isinstance(batch_number, bool) or not isinstance(batch_number, int):
        raise TypeError("batch_number must be an integer")
    if batch_number < 1:
        raise ValueError("batch_number must be positive")
    token = _BATCH_NUMBER.set(batch_number)
    try:
        yield
    finally:
        _BATCH_NUMBER.reset(token)


class TracingService:
    """Own one opt-in LangSmith client without changing process-wide settings."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory = Client,
    ) -> None:
        key = settings.langsmith_api_key
        revealed_key = key.get_secret_value() if key is not None else ""
        self._enabled = settings.langsmith_tracing and bool(revealed_key.strip())
        self._settings = settings
        self._project = settings.langsmith_project
        self._client: object | None = None
        if self._enabled:
            try:
                self._client = client_factory(api_key=revealed_key.strip())
            except Exception:
                raise RuntimeError(
                    "LangSmith tracing could not be initialized."
                ) from None

    @property
    def enabled(self) -> bool:
        """Return whether both explicit opt-in and nonblank credentials exist."""
        return self._enabled

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(enabled={self.enabled!r}, "
            f"project={self._project!r})"
        )

    def runnable_config(
        self,
        agent: Agent,
        session_id: str,
        job_id: str,
        batch_number: int | None = None,
    ) -> RunnableConfig:
        """Build a fresh LangChain config containing only bounded identifiers."""
        if not self.enabled:
            return {}
        if not isinstance(agent, Agent):
            raise TypeError("agent must be an Agent")
        resolved_session_id = _validated_identifier(session_id, "session_id")
        resolved_job_id = _validated_identifier(job_id, "job_id")
        metadata: dict[str, object] = {
            "agent": agent.value,
            "session_id": resolved_session_id,
            "job_id": resolved_job_id,
        }
        if batch_number is not None:
            if isinstance(batch_number, bool) or not isinstance(batch_number, int):
                raise TypeError("batch_number must be an integer")
            if batch_number < 1:
                raise ValueError("batch_number must be positive")
            metadata["batch_number"] = batch_number
        return {
            "tags": ["cook-mantra", agent.value],
            "metadata": metadata,
        }

    def summarize_vision_input(
        self,
        image: object,
        media_type: object,
    ) -> dict[str, object]:
        """Validate a local image and return a trace-safe aggregate summary."""
        if not isinstance(image, bytes):
            raise TypeError("image must be bytes")
        if not image:
            raise ValueError("image must not be empty")
        if len(image) > self._settings.max_upload_bytes:
            raise ValueError("image exceeds the configured upload limit")
        if not isinstance(media_type, str):
            raise TypeError("media_type must be a string")
        if media_type not in _VISION_MEDIA_TYPES:
            raise ValueError("media_type is not a supported image type")
        return {
            "media_type": media_type,
            "byte_count": len(image),
        }

    @contextmanager
    def vision_context(
        self,
        session_id: str,
        job_id: str,
    ) -> Iterator[None]:
        """Open a LangSmith context containing only safe vision metadata."""
        if not self.enabled:
            yield
            return
        config = self.runnable_config(
            Agent.INGREDIENT_EXTRACTION,
            session_id,
            job_id,
        )
        with tracing_context(
            enabled=True,
            project_name=self._project,
            tags=list(config["tags"]),
            metadata=dict(config["metadata"]),
            client=cast(Client, self._client),
        ):
            yield

    async def invoke_text[ResultT](
        self,
        agent: Agent,
        operation: Callable[[RunnableConfig], Awaitable[ResultT]],
    ) -> ResultT:
        """Invoke one text operation inside an enabled, safely correlated context."""
        if not self.enabled:
            return await operation({})
        session_id, job_id = _current_job_correlation()
        config = self.runnable_config(
            agent,
            session_id,
            job_id,
            _BATCH_NUMBER.get(),
        )
        with tracing_context(
            enabled=True,
            project_name=self._project,
            tags=list(config["tags"]),
            metadata=dict(config["metadata"]),
            client=cast(Client, self._client),
        ):
            return await operation(config)

    async def invoke_vision(
        self,
        image: bytes,
        media_type: str,
        operation: Callable[[], Awaitable[ExtractionResult]],
    ) -> ExtractionResult:
        """Run vision locally while tracing only allowlisted aggregate summaries."""
        self.summarize_vision_input(image, media_type)
        if not self.enabled:
            return await operation()
        session_id, job_id = _current_job_correlation()
        with self.vision_context(session_id, job_id):
            return await _invoke_trace_safe_vision(
                image=image,
                media_type=media_type,
                operation=operation,
            )


def _current_job_correlation() -> tuple[str, str]:
    correlation = current_log_context()
    try:
        session_id = correlation["session_id"]
        job_id = correlation["job_id"]
    except KeyError:
        raise RuntimeError(
            "Enabled tracing requires current session and job correlation."
        ) from None
    return (
        _validated_identifier(session_id, "session_id"),
        _validated_identifier(job_id, "job_id"),
    )


def _validated_identifier(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    resolved = value.strip()
    if not resolved:
        raise ValueError(f"{label} must not be blank")
    if len(resolved) > 128:
        raise ValueError(f"{label} is too long")
    return resolved
