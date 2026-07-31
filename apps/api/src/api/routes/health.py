"""Process health and external-dependency readiness routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.dependencies import get_ollama_health
from core.errors import AppError, ErrorCode
from schemas.errors import ErrorResponse
from schemas.health import HealthResponse, OllamaStatus, ReadinessResponse
from services.ollama_health import OllamaHealthService

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    operation_id="getHealth",
    summary="Check process health",
)
async def health() -> HealthResponse:
    """Report only whether the API process can serve requests."""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    operation_id="getReadiness",
    summary="Check local model readiness",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "Ollama is unavailable or a required model is missing.",
        }
    },
)
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
