"""Pure selection, commit, and rollback rules for complete recipes."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from core.errors import AppError, ErrorCode
from domain.recipe_options import RecipeOption
from domain.recipes import CompleteRecipe, RecipeFailure
from domain.session_service import require_stage
from domain.sessions import Session, SessionStage

_RECIPE_ENTRY_STAGES = frozenset(
    {
        SessionStage.OPTIONS_READY,
        SessionStage.RECIPES_READY,
    }
)


def begin_recipe_generation(
    session: Session,
    option_ids: Sequence[str],
) -> tuple[Session, list[RecipeOption], SessionStage]:
    """Resolve selected stored options and return a detached generating state."""
    _require_recipe_entry_stage(session)
    normalized_ids = _normalize_selection_ids(session, option_ids)
    options_by_id = {option.id: option for option in session.recipe_options}

    try:
        selected = [
            options_by_id[option_id].model_copy(deep=True)
            for option_id in normalized_ids
        ]
    except KeyError:
        raise _unknown_option_error(session) from None

    generating = session.model_copy(
        deep=True,
        update={
            "stage": SessionStage.GENERATING_RECIPES,
            "updated_at": _next_updated_at(session),
        },
    )
    return generating, selected, session.stage


def commit_recipe_results(
    session: Session,
    successes: Mapping[str, CompleteRecipe],
    failures: Mapping[str, RecipeFailure],
) -> Session:
    """Validate and atomically merge one recipe-generation result set."""
    require_stage(session, SessionStage.GENERATING_RECIPES)
    if not isinstance(successes, Mapping) or not isinstance(failures, Mapping):
        raise _invalid_request_error(
            session,
            "Recipe successes and failures must be mappings.",
        )
    if not successes and not failures:
        raise _invalid_request_error(session, "Recipe results must not be empty.")

    detached_successes = _validate_result_mapping(
        session,
        successes,
        CompleteRecipe,
    )
    detached_failures = _validate_result_mapping(
        session,
        failures,
        RecipeFailure,
    )
    if detached_successes.keys() & detached_failures.keys():
        raise _invalid_request_error(
            session,
            "Recipe successes and failures must be disjoint.",
        )

    stored_ids = {option.id for option in session.recipe_options}
    if any(
        option_id not in stored_ids
        for option_id in (*detached_successes, *detached_failures)
    ):
        raise _unknown_option_error(session)

    if not detached_successes:
        raise AppError(
            code=ErrorCode.MODEL_OUTPUT_INVALID,
            message="Every selected recipe failed to generate.",
            status_code=502,
            retryable=True,
            session_id=session.id,
        )

    merged_successes = {
        option_id: recipe.model_copy(deep=True)
        for option_id, recipe in session.complete_recipes.items()
    }
    merged_failures = {
        option_id: failure.model_copy(deep=True)
        for option_id, failure in session.recipe_failures.items()
    }
    for option_id, recipe in detached_successes.items():
        merged_successes[option_id] = recipe
        merged_failures.pop(option_id, None)
    for option_id, failure in detached_failures.items():
        merged_failures[option_id] = failure
        merged_successes.pop(option_id, None)

    ordered_successes = _order_by_stored_options(
        session.recipe_options,
        merged_successes,
    )
    ordered_failures = _order_by_stored_options(
        session.recipe_options,
        merged_failures,
    )
    return session.model_copy(
        deep=True,
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": ordered_successes,
            "recipe_failures": ordered_failures,
            "updated_at": _next_updated_at(session),
        },
    )


def restore_after_recipe_failure(session: Session) -> Session:
    """Restore the ready stage inferred from retained pre-generation results."""
    require_stage(session, SessionStage.GENERATING_RECIPES)
    previous_stage = (
        SessionStage.RECIPES_READY
        if session.complete_recipes or session.recipe_failures
        else SessionStage.OPTIONS_READY
    )
    return session.model_copy(
        deep=True,
        update={
            "stage": previous_stage,
            "updated_at": _next_updated_at(session),
        },
    )


def _require_recipe_entry_stage(session: Session) -> None:
    if session.stage not in _RECIPE_ENTRY_STAGES:
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Session must have recipe options ready for this operation.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )


def _normalize_selection_ids(
    session: Session,
    option_ids: Sequence[str],
) -> list[str]:
    if isinstance(option_ids, (str, bytes)) or not isinstance(option_ids, Sequence):
        raise _invalid_request_error(
            session,
            "Recipe option IDs must be supplied as a sequence.",
        )
    if not 1 <= len(option_ids) <= 6:
        raise _invalid_request_error(
            session,
            "Select between one and six recipe options.",
        )

    normalized_ids: list[str] = []
    for option_id in option_ids:
        if not isinstance(option_id, str) or not option_id.strip():
            raise _invalid_request_error(
                session,
                "Recipe option IDs must not be blank.",
            )
        normalized_ids.append(option_id.strip())
    if len(set(normalized_ids)) != len(normalized_ids):
        raise _invalid_request_error(
            session,
            "Recipe option IDs must be unique.",
        )
    return normalized_ids


def _validate_result_mapping[Result: BaseModel](
    session: Session,
    results: Mapping[str, Result],
    model_type: type[Result],
) -> dict[str, Result]:
    detached: dict[str, Result] = {}
    for raw_key, value in results.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise _invalid_request_error(
                session,
                "Recipe result IDs must not be blank.",
            )
        normalized_key = raw_key.strip()
        if normalized_key in detached:
            raise _invalid_request_error(
                session,
                "Recipe result IDs must be unique.",
            )
        if not isinstance(value, model_type):
            raise _invalid_request_error(
                session,
                "Recipe result values are invalid.",
            )
        try:
            detached_value = model_type.model_validate(
                value.model_dump(mode="python", round_trip=True, warnings="error")
            )
        except (TypeError, ValueError):
            raise _invalid_request_error(
                session,
                "Recipe result values are invalid.",
            ) from None
        if normalized_key != detached_value.option_id:
            raise _invalid_request_error(
                session,
                "Recipe result IDs must match their mapping keys.",
            )
        detached[normalized_key] = detached_value
    return detached


def _order_by_stored_options[Result](
    options: Sequence[RecipeOption],
    results: Mapping[str, Result],
) -> dict[str, Result]:
    ordered = {
        option.id: results[option.id] for option in options if option.id in results
    }
    ordered.update(
        (option_id, value)
        for option_id, value in results.items()
        if option_id not in ordered
    )
    return ordered


def _invalid_request_error(session: Session, message: str) -> AppError:
    return AppError(
        code=ErrorCode.INVALID_REQUEST,
        message=message,
        status_code=422,
        retryable=False,
        session_id=session.id,
    )


def _unknown_option_error(session: Session) -> AppError:
    return AppError(
        code=ErrorCode.RESOURCE_NOT_FOUND,
        message="Unknown recipe option.",
        status_code=404,
        retryable=False,
        session_id=session.id,
    )


def _next_updated_at(session: Session) -> datetime:
    return max(
        datetime.now(UTC),
        session.updated_at + timedelta(microseconds=1),
    )
