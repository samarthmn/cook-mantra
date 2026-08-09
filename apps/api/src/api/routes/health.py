"""Process health and external-dependency readiness routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status

from api.dependencies import get_model_runtime_readiness
from schemas.errors import ErrorResponse
from schemas.health import (
    HealthResponse,
    OllamaStatus,
    ReadinessResponse,
    RuntimeStatusResponse,
)
from services.model_runtime import ModelRuntimeReadinessService

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
    "/runtime-status",
    response_model=RuntimeStatusResponse,
    operation_id="getRuntimeStatus",
    summary="Inspect the local model runtime",
    responses={
        status.HTTP_200_OK: {
            "headers": {
                "Cache-Control": {
                    "description": (
                        "Prevents caching of the process-local runtime snapshot."
                    ),
                    "schema": {"type": "string", "const": "no-store"},
                }
            }
        }
    },
)
async def runtime_status(
    request: Request,
    response: Response,
    model_runtime: Annotated[
        ModelRuntimeReadinessService,
        Depends(get_model_runtime_readiness),
    ],
) -> RuntimeStatusResponse:
    """Return a safe view of the process-cached provider inspection."""
    runtime_inspection = await model_runtime.inspect()
    roles = runtime_inspection.model_runtime.roles.values()
    needs_attention = not runtime_inspection.model_runtime.ready or any(
        role.enabled and not role.ready for role in roles
    )
    runtime_status: Literal["ok", "attention"] = (
        "attention" if needs_attention else "ok"
    )
    response.headers["Cache-Control"] = "no-store"
    return RuntimeStatusResponse(
        status=runtime_status,
        runtime_revision=request.app.state.runtime_revision,
        model_runtime=runtime_inspection.model_runtime,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    response_model_exclude_none=True,
    operation_id="getReadiness",
    summary="Check model runtime readiness",
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "A selected model provider rejected its credentials.",
        },
        status.HTTP_402_PAYMENT_REQUIRED: {
            "model": ErrorResponse,
            "description": "A selected model provider requires payment.",
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "model": ErrorResponse,
            "description": "A selected model provider is rate limited.",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": "A selected model returned an invalid response or output.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "A required model provider or capability is unavailable.",
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "model": ErrorResponse,
            "description": "A selected model operation timed out.",
        },
    },
)
async def ready(
    model_runtime: Annotated[
        ModelRuntimeReadinessService,
        Depends(get_model_runtime_readiness),
    ],
) -> ReadinessResponse:
    """Report required model readiness and deprecated Ollama inventory."""
    runtime_inspection = await model_runtime.inspect()
    if not runtime_inspection.model_runtime.ready:
        raise model_runtime.readiness_error(runtime_inspection.model_runtime)
    raw_inspection = runtime_inspection.ollama or {
        "reachable": False,
        "available_models": [],
        "missing": [],
    }
    inspection = OllamaStatus.model_validate(raw_inspection)
    return ReadinessResponse(
        status="ok",
        model_runtime=runtime_inspection.model_runtime,
        ollama=inspection,
    )
