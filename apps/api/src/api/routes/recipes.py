"""Complete-recipe generation routes."""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    get_complete_recipes_runner,
    get_job_runner,
    get_session_store,
)
from domain.jobs import JobOperation
from domain.recipe_service import (
    RecipeGenerationContext,
    begin_recipe_generation,
    restore_after_recipe_failure,
)
from orchestration.graphs.complete_recipes import (
    CompleteRecipesRunner,
    ProgressReporter,
)
from orchestration.job_runner import JobRunner
from repositories.session_store import SessionStore
from schemas.errors import ErrorResponse
from schemas.recipes import RecipeSelectionRequest
from schemas.sessions import QueuedJobResponse

router = APIRouter(prefix="/sessions", tags=["complete recipes"])
logger = logging.getLogger(__name__)

_ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Session or recipe option not found.",
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Session stage conflict.",
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "Invalid recipe selection.",
    },
}


@router.post(
    "/{session_id}/recipes",
    response_model=QueuedJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERROR_RESPONSES,
)
async def generate_complete_recipes(
    session_id: str,
    request: RecipeSelectionRequest,
    session_store: Annotated[SessionStore, Depends(get_session_store)],
    job_runner: Annotated[JobRunner, Depends(get_job_runner)],
    complete_recipes_runner: Annotated[
        CompleteRecipesRunner,
        Depends(get_complete_recipes_runner),
    ],
) -> QueuedJobResponse:
    """Queue complete-recipe generation for selected stored options."""
    session = await session_store.require(session_id)
    generating, _selected, _previous_stage, generation_context = (
        begin_recipe_generation(session, request.option_ids)
    )
    persisted = await session_store.replace(
        generating.model_copy(update={"updated_at": session.updated_at})
    )

    async def worker(progress: ProgressReporter) -> dict[str, object]:
        output = await complete_recipes_runner(
            persisted.id,
            generation_context,
            progress,
        )
        return {
            "selected_option_ids": list(output["selected_option_ids"]),
            "recipe_option_ids": list(output["recipe_option_ids"]),
            "failed_option_ids": list(output["failed_option_ids"]),
        }

    try:
        job = await job_runner.submit(
            JobOperation.GENERATE_RECIPES,
            persisted.id,
            worker,
        )
    except asyncio.CancelledError:
        await _run_cancellation_safe(
            _restore_unowned_generation(
                session_store,
                persisted.id,
                generation_context,
            )
        )
        raise
    except Exception:
        await _run_cancellation_safe(
            _restore_unowned_generation(
                session_store,
                persisted.id,
                generation_context,
            )
        )
        raise

    return QueuedJobResponse(session_id=persisted.id, job_id=job.id)


async def _restore_unowned_generation(
    session_store: SessionStore,
    session_id: str,
    generation_context: RecipeGenerationContext,
) -> None:
    """Restore only the exact attempt that never transferred to a job."""
    try:
        current = await session_store.require(session_id)
        restored = restore_after_recipe_failure(current, generation_context)
        await session_store.replace(
            restored.model_copy(update={"updated_at": current.updated_at})
        )
    except Exception:
        logger.exception(
            "Unowned complete-recipe generation rollback was not applied",
            extra={"session_id": session_id},
        )


async def _run_cancellation_safe(operation: Awaitable[None]) -> None:
    """Drain a pre-ownership rollback despite repeated request cancellation."""
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
