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
        image_base_url: str | None = None,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._image_base_url = (image_base_url or base_url).rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def inspect(self) -> dict[str, object]:
        """Return Ollama reachability and the full configured-model comparison."""
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
            ) as client:
                text_response = await client.get(f"{self._base_url}/api/tags")
                text_response.raise_for_status()
                text_models = _available_model_names(text_response.json())

                if self._image_base_url == self._base_url:
                    image_models = text_models
                else:
                    image_response = await client.get(
                        f"{self._image_base_url}/api/tags"
                    )
                    image_response.raise_for_status()
                    image_models = _available_model_names(image_response.json())
        except (httpx.HTTPError, ValueError) as error:
            raise AppError(
                code=ErrorCode.OLLAMA_UNAVAILABLE,
                message="Ollama is unavailable.",
                status_code=503,
                retryable=True,
            ) from error

        available_models = list(dict.fromkeys([*text_models, *image_models]))
        text_model_set = set(text_models)
        image_model_set = set(image_models)
        missing = [
            model.value
            for model in Model
            if model.value
            not in (image_model_set if model is Model.Z_IMAGE else text_model_set)
        ]
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
