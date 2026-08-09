"""Ollama chat model construction.

Ollama is called through its native API rather than its OpenAI-compatible `/v1`
endpoint on purpose: the compatibility layer accepts an OpenAI `response_format`
json_schema but does not reliably enforce it, which would silently break the
structured output every agent depends on.
"""

from enum import StrEnum
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from core import Agent, Model, Settings, get_settings
from core.errors import AppError, ErrorCode
from core.runtime_config import get_runtime_snapshot
from domain.model_runtime import AgentRole, ProviderName, ReasoningEffort


def get_model(
    agent: Agent | str,
    *,
    model: Model | str | None = None,
    temperature: float | None = None,
    thinking: bool | Literal["low", "medium", "high"] | None = None,
    num_predict: int | None = None,
    num_ctx: int | None = None,
    keep_alive: int | str | None = None,
    timeout: float | None = None,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Build the Ollama chat model assigned to an agent.

    Args:
        agent: Which agent the model is for.
        model: Ollama model overriding the one configured for `agent`.
        temperature: Sampling temperature. Defaults to 0 because every agent in
            this app produces structured output rather than prose.
        thinking: Enable or disable reasoning traces. None leaves the model's
            own default in place.
        num_predict: Maximum generated tokens. None leaves the model default.
        num_ctx: Context window size. None uses the configured
            `llm_num_ctx` rather than Ollama's 4k default, which is too small
            to hold a prompt plus a full structured-output response.
        keep_alive: How long Ollama keeps the model loaded. None leaves
            Ollama's default in place.
        timeout: Request timeout in seconds.
        settings: Override configuration, primarily for tests.

    Raises:
        ValueError: If the agent or model is unknown.
    """
    settings = settings or get_settings()
    selected_agent = _select_agent(agent)
    role = _role_for(selected_agent)
    snapshot = get_runtime_snapshot()
    if snapshot.config is None:
        raise AppError(
            code=ErrorCode.MODEL_CONFIGURATION_INVALID,
            message="The model runtime configuration is invalid.",
            status_code=503,
            retryable=False,
            details={"diagnostics": list(snapshot.diagnostics)},
        )
    selection = snapshot.config.roles[role]
    if selection.provider is not ProviderName.OLLAMA:
        raise AppError(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="The selected provider adapter is unavailable.",
            status_code=503,
            retryable=False,
            details={"role": role.value},
        )
    provider = snapshot.config.providers[ProviderName.OLLAMA]
    if provider.endpoint is None or selection.text_tuning is None:
        raise AppError(
            code=ErrorCode.MODEL_CONFIGURATION_INVALID,
            message="The model runtime configuration is invalid.",
            status_code=503,
            retryable=False,
        )
    tuning = selection.text_tuning
    resolved_model = (
        _select_model(model).value if model is not None else selection.model
    )
    resolved_timeout = (
        timeout
        if timeout is not None
        else tuning.timeout_seconds or settings.llm_timeout_seconds
    )
    resolved_num_ctx = (
        num_ctx
        if num_ctx is not None
        else tuning.context_window or settings.llm_num_ctx
    )
    resolved_temperature = (
        temperature if temperature is not None else tuning.temperature
    )
    resolved_thinking = (
        thinking if thinking is not None else _ollama_reasoning(tuning.reasoning_effort)
    )
    resolved_num_predict = (
        num_predict if num_predict is not None else tuning.max_output_tokens
    )

    return ChatOllama(
        model=resolved_model,
        base_url=str(provider.endpoint).rstrip("/"),
        temperature=resolved_temperature,
        reasoning=resolved_thinking,
        num_predict=resolved_num_predict,
        num_ctx=resolved_num_ctx,
        keep_alive=keep_alive,
        client_kwargs={"timeout": resolved_timeout},
    )


def _select_agent(agent: Agent | str) -> Agent:
    return _to_enum(Agent, agent, "agent")


def _select_model(model: Model | str) -> Model:
    return _to_enum(Model, model, "model")


def _role_for(agent: Agent) -> AgentRole:
    return {
        Agent.INGREDIENT_EXTRACTION: AgentRole.INGREDIENT_EXTRACTOR,
        Agent.MASTER_CHEF: AgentRole.MASTER_CHEF,
        Agent.SPECIALIZED_RECIPE: AgentRole.RECIPE_WRITER,
    }[agent]


def _ollama_reasoning(
    effort: ReasoningEffort | None,
) -> bool | Literal["low", "medium", "high"] | None:
    if effort is None:
        return None
    if effort is ReasoningEffort.NONE:
        return False
    return effort.value


def _to_enum[T: StrEnum](enum: type[T], value: str, label: str) -> T:
    try:
        return enum(value)
    except ValueError:
        supported = ", ".join(option.value for option in enum)
        raise ValueError(
            f"Unknown {label} {value!r}. Expected one of: {supported}."
        ) from None
