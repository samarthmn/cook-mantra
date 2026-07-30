"""Temporary generated-image artifact routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from api.dependencies import get_artifact_store
from core.errors import AppError, ErrorCode
from schemas.errors import ErrorResponse
from services.artifacts import ArtifactStore

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_SUPPORTED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_BINARY_RESPONSES = {
    media_type: {"schema": {"type": "string", "format": "binary"}}
    for media_type in sorted(_SUPPORTED_MEDIA_TYPES)
}


@router.get(
    "/{artifact_id}",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "description": "Generated dish image.",
            "content": _BINARY_RESPONSES,
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Artifact not found.",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Artifact storage failure.",
        },
    },
)
async def get_artifact(
    artifact_id: str,
    artifact_store: Annotated[ArtifactStore, Depends(get_artifact_store)],
) -> Response:
    """Return one current runtime artifact without making it cacheable."""
    artifact = await artifact_store.require(artifact_id)
    if artifact.media_type not in _SUPPORTED_MEDIA_TYPES:
        raise AppError(
            code=ErrorCode.ARTIFACT_FAILURE,
            message="The artifact format is not supported.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            retryable=False,
        )
    content, media_type = await artifact_store.read(
        artifact.id,
        artifact.owner_session_id,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )
