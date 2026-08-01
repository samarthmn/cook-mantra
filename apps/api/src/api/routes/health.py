"""Process health and external-dependency readiness routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.dependencies import get_ollama_health
from core.errors import AppError, ErrorCode
from schemas.errors import ErrorResponse
from schemas.health import BeastStatus, HealthResponse, OllamaStatus, ReadinessResponse
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
    response_model_exclude_none=True,
    operation_id="getReadiness",
    summary="Check local model readiness",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "Ollama or Beast is unavailable, or a required text model is missing."
            ),
        }
    },
)
async def ready(
    ollama_health: Annotated[OllamaHealthService, Depends(get_ollama_health)],
) -> ReadinessResponse:
    """Report text-model readiness and optional Beast API health."""
    raw_inspection = await ollama_health.inspect()
    inspection = OllamaStatus.model_validate(raw_inspection)
    if inspection.missing:
        raise AppError(
            code=ErrorCode.MODEL_NOT_FOUND,
            message="Required Ollama models are not available.",
            status_code=503,
            retryable=False,
            details={"missing_models": inspection.missing},
        )
    raw_beast = raw_inspection.get("beast")
    beast = BeastStatus.model_validate(raw_beast) if raw_beast is not None else None
    if beast is not None and not beast.reachable:
        raise AppError(
            code=ErrorCode.IMAGE_PROVIDER_UNAVAILABLE,
            message="The image generation provider is unavailable.",
            status_code=503,
            retryable=True,
            details={"beast": beast.model_dump()},
        )
    return ReadinessResponse(status="ok", ollama=inspection, beast=beast)
