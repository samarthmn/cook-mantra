"""Ollama model readiness with optional Beast API reachability."""

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
        beast_base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._beast_base_url = (
            beast_base_url.rstrip("/") if beast_base_url is not None else None
        )
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def inspect(self) -> dict[str, object]:
        """Return text-model readiness and optional Beast health."""
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
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
        if self._beast_base_url is not None:
            inspection["beast"] = await self._inspect_beast()
        return inspection

    async def _inspect_beast(self) -> dict[str, object]:
        """Return safe public-health details without authenticating to Beast."""
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.get(f"{self._beast_base_url}/health")
                response.raise_for_status()
                payload = response.json()
            status = payload.get("status") if isinstance(payload, dict) else None
            if not isinstance(status, str):
                raise ValueError("Beast health response has no status string")
        except (httpx.HTTPError, ValueError):
            return {"reachable": False, "status": None}
        return {"reachable": True, "status": status}


def _available_model_names(payload: Any) -> list[str]:
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return [
        model["name"]
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    ]
