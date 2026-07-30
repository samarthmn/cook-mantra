"""Process health and external-dependency readiness routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from api.dependencies import get_ollama_health
from core.errors import AppError, ErrorCode
from schemas.health import HealthResponse, OllamaStatus, ReadinessResponse
from services.ollama_health import OllamaHealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report only whether the API process can serve requests."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(
    ollama_health: Annotated[OllamaHealthService, Depends(get_ollama_health)],
) -> ReadinessResponse:
    """Report Ollama connectivity and required model availability."""
    inspection = OllamaStatus.model_validate(await ollama_health.inspect())
    if inspection.missing:
        raise AppError(
            code=ErrorCode.MODEL_NOT_FOUND,
            message="Required Ollama models are not available.",
            status_code=503,
            retryable=False,
            details={"missing_models": inspection.missing},
        )
    return ReadinessResponse(status="ok", ollama=inspection)
