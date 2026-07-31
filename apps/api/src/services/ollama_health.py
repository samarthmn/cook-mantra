"""Ollama connectivity and configured-model inventory checks."""

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
        """Return Ollama reachability and the full configured-model comparison."""
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AppError(
                code=ErrorCode.OLLAMA_UNAVAILABLE,
                message="Ollama is unavailable.",
                status_code=503,
                retryable=True,
            ) from error

        available_models = _available_model_names(payload)
        available_set = set(available_models)
        missing = [model.value for model in Model if model.value not in available_set]
        return {
            "reachable": True,
            "available_models": available_models,
            "missing": missing,
        }


def _available_model_names(payload: Any) -> list[str]:
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return [
        model["name"]
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    ]
