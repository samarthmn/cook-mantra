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


def get_model(
    agent: Agent | str,
    *,
    model: Model | str | None = None,
    temperature: float = 0.0,
    thinking: bool | Literal["low", "medium", "high"] | None = None,
    num_predict: int | None = None,
    num_ctx: int | None = None,
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
        timeout: Request timeout in seconds.
        settings: Override configuration, primarily for tests.

    Raises:
        ValueError: If the agent or model is unknown, or the resolved model
            generates images rather than text.
    """
    settings = settings or get_settings()
    resolved_model = (
        _select_model(model)
        if model is not None
        else settings.model_for(_select_agent(agent))
    )
    resolved_timeout = timeout if timeout is not None else settings.llm_timeout_seconds
    resolved_num_ctx = num_ctx if num_ctx is not None else settings.llm_num_ctx

    if resolved_model is Model.Z_IMAGE:
        raise ValueError(
            f"Model {resolved_model.value!r} generates images and cannot be used "
            f"as a chat model. Use the image generation service instead."
        )

    return ChatOllama(
        model=resolved_model.value,
        base_url=str(settings.ollama_base_url).rstrip("/"),
        temperature=temperature,
        reasoning=thinking,
        num_predict=num_predict,
        num_ctx=resolved_num_ctx,
        client_kwargs={"timeout": resolved_timeout},
    )


def _select_agent(agent: Agent | str) -> Agent:
    return _to_enum(Agent, agent, "agent")


def _select_model(model: Model | str) -> Model:
    return _to_enum(Model, model, "model")


def _to_enum[T: StrEnum](enum: type[T], value: str, label: str) -> T:
    try:
        return enum(value)
    except ValueError:
        supported = ", ".join(option.value for option in enum)
        raise ValueError(
            f"Unknown {label} {value!r}. Expected one of: {supported}."
        ) from None
