"""Recipe-option generation routes."""

import asyncio
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    get_job_runner,
    get_recipe_options_runner,
    get_session_store,
)
from domain.jobs import JobOperation
from domain.recipe_option_service import (
    OptionGenerationContext,
    begin_option_generation,
    restore_option_generation,
)
from domain.recipe_options import RecipePreferences
from orchestration.graphs.recipe_options import ProgressReporter, RecipeOptionsRunner
from orchestration.job_runner import JobRunner
from repositories.session_store import SessionStore
from schemas.errors import ErrorResponse
from schemas.sessions import SessionCreatedResponse

router = APIRouter(prefix="/sessions", tags=["recipe options"])

_ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Session not found.",
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Session stage conflict.",
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "Invalid recipe preferences.",
    },
}


@router.post(
    "/{session_id}/recipe-options",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERROR_RESPONSES,
)
async def generate_recipe_options(
    session_id: str,
    preferences: RecipePreferences,
    session_store: Annotated[SessionStore, Depends(get_session_store)],
    job_runner: Annotated[JobRunner, Depends(get_job_runner)],
    recipe_options_runner: Annotated[
        RecipeOptionsRunner,
        Depends(get_recipe_options_runner),
    ],
) -> SessionCreatedResponse:
    """Queue the first recipe-option batch for confirmed ingredients."""
    return await _queue_recipe_options(
        session_id,
        preferences,
        more=False,
        session_store=session_store,
        job_runner=job_runner,
        recipe_options_runner=recipe_options_runner,
    )


@router.post(
    "/{session_id}/recipe-options/more",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERROR_RESPONSES,
)
async def generate_more_recipe_options(
    session_id: str,
    preferences: RecipePreferences,
    session_store: Annotated[SessionStore, Depends(get_session_store)],
    job_runner: Annotated[JobRunner, Depends(get_job_runner)],
    recipe_options_runner: Annotated[
        RecipeOptionsRunner,
        Depends(get_recipe_options_runner),
    ],
) -> SessionCreatedResponse:
    """Queue a fresh recipe-option batch excluding every shown name."""
    return await _queue_recipe_options(
        session_id,
        preferences,
        more=True,
        session_store=session_store,
        job_runner=job_runner,
        recipe_options_runner=recipe_options_runner,
    )


async def _queue_recipe_options(
    session_id: str,
    preferences: RecipePreferences,
    *,
    more: bool,
    session_store: SessionStore,
    job_runner: JobRunner,
    recipe_options_runner: RecipeOptionsRunner,
) -> SessionCreatedResponse:
    session = await session_store.require(session_id)
    generating, _previous_stage, generation_context = begin_option_generation(
        session,
        preferences,
        more,
    )
    persisted = await session_store.replace(
        generating.model_copy(update={"updated_at": session.updated_at})
    )
    batch_start = len(persisted.recipe_options) if more else 0

    async def worker(progress: ProgressReporter) -> dict[str, object]:
        await recipe_options_runner(
            persisted.id,
            persisted.preferences,
            more,
            generation_context,
            progress,
        )
        saved_session = await session_store.require(persisted.id)
        saved_options = saved_session.recipe_options[batch_start:]
        return {
            "option_ids": [option.id for option in saved_options],
            "batch_number": saved_session.option_batch_number,
        }

    operation = (
        JobOperation.GENERATE_MORE_OPTIONS if more else JobOperation.GENERATE_OPTIONS
    )
    try:
        job = await job_runner.submit(operation, persisted.id, worker)
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
    return SessionCreatedResponse(session_id=persisted.id, job_id=job.id)


async def _restore_unowned_generation(
    session_store: SessionStore,
    session_id: str,
    generation_context: OptionGenerationContext,
) -> None:
    current = await session_store.require(session_id)
    restored = restore_option_generation(current, generation_context)
    await session_store.replace(
        restored.model_copy(update={"updated_at": current.updated_at})
    )


async def _run_cancellation_safe(operation: Awaitable[None]) -> None:
    """Drain a pre-ownership rollback before propagating cancellation."""
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
