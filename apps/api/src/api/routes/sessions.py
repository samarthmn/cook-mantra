"""Cooking-session upload and retrieval routes."""

import asyncio
import logging
from collections.abc import Awaitable
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status

from api.dependencies import (
    get_artifact_store,
    get_ingredient_extraction_runner,
    get_job_runner,
    get_session_store,
    get_upload_validator,
)
from domain.jobs import JobOperation
from domain.sessions import SessionStage
from orchestration.graphs.ingredient_extraction import IngredientExtractionRunner
from orchestration.job_runner import JobRunner
from repositories.session_store import SessionStore
from schemas.sessions import SessionCreatedResponse, SessionResponse
from services.artifacts import ArtifactStore
from services.uploads import ImageUploadValidator

router = APIRouter(prefix="/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_session(
    image: Annotated[UploadFile, File()],
    validator: Annotated[ImageUploadValidator, Depends(get_upload_validator)],
    session_store: Annotated[SessionStore, Depends(get_session_store)],
    artifact_store: Annotated[ArtifactStore, Depends(get_artifact_store)],
    job_runner: Annotated[JobRunner, Depends(get_job_runner)],
    extraction_runner: Annotated[
        IngredientExtractionRunner,
        Depends(get_ingredient_extraction_runner),
    ],
) -> SessionCreatedResponse:
    """Validate an ingredient image and queue extraction."""
    try:
        validated = await validator.read(image)
    finally:
        await _run_cancellation_safe(image.close())

    session = await session_store.create(SessionStage.EXTRACTING)
    artifact = None
    try:
        artifact = await artifact_store.write(
            validated.data,
            validated.media_type,
            validated.suffix,
            owner_session_id=session.id,
        )
        job = await job_runner.submit(
            JobOperation.EXTRACT_INGREDIENTS,
            session.id,
            partial(extraction_runner, session.id, artifact.id),
        )
    except asyncio.CancelledError:
        await _run_cancellation_safe(
            _rollback_created_upload(
                session_store,
                artifact_store,
                session_id=session.id,
                artifact_id=artifact.id if artifact is not None else None,
            )
        )
        raise
    except Exception:
        await _run_cancellation_safe(
            _rollback_created_upload(
                session_store,
                artifact_store,
                session_id=session.id,
                artifact_id=artifact.id if artifact is not None else None,
            )
        )
        raise

    return SessionCreatedResponse(session_id=session.id, job_id=job.id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    session_store: Annotated[SessionStore, Depends(get_session_store)],
) -> SessionResponse:
    """Return the current public state of one cooking session."""
    return SessionResponse.model_validate(await session_store.require(session_id))


async def _preserving_cleanup(
    cleanup: Awaitable[None],
    *,
    resource: str,
    resource_id: str,
) -> None:
    """Attempt rollback without replacing the request's original safe error."""
    try:
        await cleanup
    except Exception:
        logger.exception(
            "Session upload rollback failed",
            extra={
                "resource": resource,
                "resource_id": resource_id,
            },
        )


async def _rollback_created_upload(
    session_store: SessionStore,
    artifact_store: ArtifactStore,
    *,
    session_id: str,
    artifact_id: str | None,
) -> None:
    if artifact_id is not None:
        await _preserving_cleanup(
            artifact_store.delete(artifact_id),
            resource="artifact",
            resource_id=artifact_id,
        )
    await _preserving_cleanup(
        session_store.delete(session_id),
        resource="session",
        resource_id=session_id,
    )


async def _run_cancellation_safe(operation: Awaitable[None]) -> None:
    """Drain one owned operation before propagating request cancellation."""
    operation_task = asyncio.create_task(operation)
    cancellation: asyncio.CancelledError | None = None
    while not operation_task.done():
        try:
            await asyncio.shield(operation_task)
        except asyncio.CancelledError as error:
            cancellation = error
            continue
    operation_task.result()
    if cancellation is not None:
        raise cancellation
