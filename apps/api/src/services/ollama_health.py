"""Ollama model readiness compatibility inventory."""

from typing import Any

import httpx

from core.config import Model
from core.errors import AppError, ErrorCode


class OllamaHealthService:
    """Inspect local Ollama state without changing or pulling models."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def inspect(self) -> dict[str, object]:
        """Return the deprecated safe text-model compatibility inventory."""
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                text_response = await client.get(f"{self._base_url}/api/tags")
                text_response.raise_for_status()
                text_models = _available_model_names(text_response.json())
        except (httpx.HTTPError, ValueError) as error:
            raise AppError(
                code=ErrorCode.OLLAMA_UNAVAILABLE,
                message="Ollama is unavailable.",
                status_code=503,
                retryable=True,
            ) from error

        text_model_set = set(text_models)
        missing = [model.value for model in Model if model.value not in text_model_set]
        inspection: dict[str, object] = {
            "reachable": True,
            "available_models": text_models,
            "missing": missing,
        }
        return inspection


def _available_model_names(payload: Any) -> list[str]:
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return [
        model["name"]
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    ]
