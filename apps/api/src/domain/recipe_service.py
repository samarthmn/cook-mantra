"""Pure selection, commit, and rollback rules for complete recipes."""

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.errors import AppError, ErrorCode
from domain.recipe_options import RecipeOption
from domain.recipes import CompleteRecipe, RecipeFailure
from domain.session_service import require_stage
from domain.sessions import Session, SessionStage

_RECIPE_ENTRY_STAGES = (
    SessionStage.OPTIONS_READY,
    SessionStage.RECIPES_READY,
)


class RecipeGenerationContext(BaseModel):
    """Immutable intent and rollback data for one recipe-generation request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    generation_id: str
    selected_option_ids: tuple[str, ...] = Field(min_length=1, max_length=6)
    previous_stage: SessionStage
    rollback_snapshot: str = Field(repr=False)

    @field_validator("session_id", "generation_id")
    @classmethod
    def normalize_required_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Recipe generation IDs must not be blank.")
        return normalized

    @field_validator("selected_option_ids")
    @classmethod
    def normalize_selected_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("Selected recipe option IDs must not be blank.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Selected recipe option IDs must be unique.")
        return normalized

    @field_validator("rollback_snapshot")
    @classmethod
    def validate_snapshot(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Recipe rollback snapshot must not be blank.")
        return value


def begin_recipe_generation(
    session: Session,
    option_ids: Sequence[str],
) -> tuple[
    Session,
    list[RecipeOption],
    SessionStage,
    RecipeGenerationContext,
]:
    """Resolve selected stored options and return a detached generating state."""
    _require_recipe_entry_stage(session)
    _require_no_active_recipe_generation(session)
    _require_valid_updated_at(session)
    normalized_ids = _normalize_selection_ids(session, option_ids)
    options_by_id = {option.id: option for option in session.recipe_options}

    try:
        selected = [
            options_by_id[option_id].model_copy(deep=True)
            for option_id in normalized_ids
        ]
    except KeyError:
        raise _unknown_option_error(session) from None

    rollback_snapshot = session.model_dump_json()
    generation_id = _new_generation_id(
        session_id=session.id,
        selected_option_ids=tuple(normalized_ids),
        previous_stage=session.stage,
        rollback_snapshot=rollback_snapshot,
    )
    context = RecipeGenerationContext(
        session_id=session.id,
        generation_id=generation_id,
        selected_option_ids=tuple(normalized_ids),
        previous_stage=session.stage,
        rollback_snapshot=rollback_snapshot,
    )
    generating = session.model_copy(
        deep=True,
        update={
            "stage": SessionStage.GENERATING_RECIPES,
            "recipe_generation_id": generation_id,
            "updated_at": _next_updated_at(session),
        },
    )
    return generating, selected, session.stage, context


def validate_recipe_generation(
    session: Session,
    context: RecipeGenerationContext,
) -> Session:
    """Validate one pristine persisted attempt and return its rollback snapshot."""
    require_stage(session, SessionStage.GENERATING_RECIPES)
    _require_valid_updated_at(session)
    validated_context, rollback = _require_matching_context(session, context)
    _require_pristine_generating_state(session, rollback, validated_context)
    return rollback.model_copy(deep=True)


def commit_recipe_results(
    session: Session,
    successes: Mapping[str, CompleteRecipe],
    failures: Mapping[str, RecipeFailure],
    context: RecipeGenerationContext,
) -> Session:
    """Validate and atomically merge one recipe-generation result set."""
    require_stage(session, SessionStage.GENERATING_RECIPES)
    _require_valid_updated_at(session)
    validated_context, rollback = _require_matching_context(session, context)
    _require_pristine_generating_state(session, rollback, validated_context)
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

    stored_ids = {option.id for option in rollback.recipe_options}
    if any(
        option_id not in stored_ids
        for option_id in (*detached_successes, *detached_failures)
    ):
        raise _unknown_option_error(session)

    result_ids = detached_successes.keys() | detached_failures.keys()
    if result_ids != set(validated_context.selected_option_ids):
        raise _invalid_request_error(
            session,
            "Recipe results must match the selection.",
        )

    selected_options = {
        option.id: option
        for option in rollback.recipe_options
        if option.id in result_ids
    }
    for option_id, recipe in detached_successes.items():
        option = selected_options[option_id]
        expected_name = _normalize_stored_recipe_identity(session, option.name)
        expected_cuisine = _normalize_stored_recipe_identity(
            session,
            option.cuisine,
        )
        if (
            recipe.name != expected_name
            or recipe.cuisine != expected_cuisine
            or recipe.servings != rollback.preferences.servings
        ):
            raise _invalid_request_error(
                session,
                "Complete recipe identity is invalid.",
            )

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
            "recipe_generation_id": None,
            "updated_at": _next_updated_at(session),
        },
    )


def restore_after_recipe_failure(
    session: Session,
    context: RecipeGenerationContext,
) -> Session:
    """Restore the exact detached state captured before recipe generation."""
    require_stage(session, SessionStage.GENERATING_RECIPES)
    _require_valid_updated_at(session)
    _, rollback = _require_matching_context(session, context)
    return rollback.model_copy(deep=True)


def _require_recipe_entry_stage(session: Session) -> None:
    if not any(session.stage is stage for stage in _RECIPE_ENTRY_STAGES):
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Session must have recipe options ready for this operation.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )


def _require_no_active_recipe_generation(session: Session) -> None:
    if session.recipe_generation_id is not None:
        raise _context_error(session)


def _require_matching_context(
    session: Session,
    context: RecipeGenerationContext,
) -> tuple[RecipeGenerationContext, Session]:
    validated_context = _revalidate_context(session, context)
    rollback = _revalidate_snapshot(session, validated_context)
    if (
        session.id != validated_context.session_id
        or session.recipe_generation_id != validated_context.generation_id
    ):
        raise _context_error(session)
    return validated_context, rollback


def _revalidate_context(
    session: Session,
    context: RecipeGenerationContext,
) -> RecipeGenerationContext:
    try:
        if not isinstance(context, RecipeGenerationContext):
            raise TypeError
        validated = RecipeGenerationContext.model_validate(
            context.model_dump(mode="python", round_trip=True, warnings="error")
        )
    except Exception:
        raise _context_error(session) from None
    if not any(
        validated.previous_stage is stage for stage in _RECIPE_ENTRY_STAGES
    ) or not _generation_id_matches(validated):
        raise _context_error(session)
    return validated


def _revalidate_snapshot(
    session: Session,
    context: RecipeGenerationContext,
) -> Session:
    try:
        rollback = Session.model_validate_json(context.rollback_snapshot)
    except Exception:
        raise _context_error(session) from None
    if (
        rollback.id != context.session_id
        or rollback.stage is not context.previous_stage
        or rollback.recipe_generation_id is not None
        or not any(rollback.stage is stage for stage in _RECIPE_ENTRY_STAGES)
    ):
        raise _context_error(session)
    _require_valid_updated_at(rollback, context_error=True, session_id=session.id)

    stored_ids = {option.id for option in rollback.recipe_options}
    if any(option_id not in stored_ids for option_id in context.selected_option_ids):
        raise _context_error(session)
    return rollback


def _require_pristine_generating_state(
    session: Session,
    rollback: Session,
    context: RecipeGenerationContext,
) -> None:
    expected = rollback.model_copy(
        deep=True,
        update={
            "stage": SessionStage.GENERATING_RECIPES,
            "recipe_generation_id": context.generation_id,
            "updated_at": session.updated_at,
        },
    )
    if session != expected:
        raise _context_error(session)


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


def _normalize_stored_recipe_identity(session: Session, value: object) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise _invalid_request_error(
            session,
            "Complete recipe identity is invalid.",
        )
    return normalized


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


def _context_error(session: Session) -> AppError:
    return AppError(
        code=ErrorCode.INVALID_SESSION_TRANSITION,
        message="Recipe generation context is invalid.",
        status_code=409,
        retryable=False,
        session_id=session.id,
    )


def _new_generation_id(
    *,
    session_id: str,
    selected_option_ids: tuple[str, ...],
    previous_stage: SessionStage,
    rollback_snapshot: str,
) -> str:
    nonce = uuid4().hex
    digest = _generation_digest(
        nonce=nonce,
        session_id=session_id,
        selected_option_ids=selected_option_ids,
        previous_stage=previous_stage,
        rollback_snapshot=rollback_snapshot,
    )
    return f"{nonce}.{digest}"


def _generation_id_matches(context: RecipeGenerationContext) -> bool:
    parts = context.generation_id.split(".")
    if len(parts) != 2:
        return False
    nonce, supplied_digest = parts
    if len(nonce) != 32 or len(supplied_digest) != 64:
        return False
    try:
        bytes.fromhex(nonce)
        bytes.fromhex(supplied_digest)
    except ValueError:
        return False
    expected_digest = _generation_digest(
        nonce=nonce,
        session_id=context.session_id,
        selected_option_ids=context.selected_option_ids,
        previous_stage=context.previous_stage,
        rollback_snapshot=context.rollback_snapshot,
    )
    return hmac.compare_digest(supplied_digest, expected_digest)


def _generation_digest(
    *,
    nonce: str,
    session_id: str,
    selected_option_ids: tuple[str, ...],
    previous_stage: SessionStage,
    rollback_snapshot: str,
) -> str:
    payload = json.dumps(
        {
            "previous_stage": previous_stage.value,
            "rollback_snapshot": rollback_snapshot,
            "selected_option_ids": selected_option_ids,
            "session_id": session_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(f"{nonce}\0{payload}".encode()).hexdigest()


def _require_valid_updated_at(
    session: Session,
    *,
    context_error: bool = False,
    session_id: str | None = None,
) -> datetime:
    updated_at = session.updated_at
    try:
        valid = (
            isinstance(updated_at, datetime)
            and updated_at.tzinfo is not None
            and updated_at.utcoffset() is not None
        )
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        if context_error:
            raise AppError(
                code=ErrorCode.INVALID_SESSION_TRANSITION,
                message="Recipe generation context is invalid.",
                status_code=409,
                retryable=False,
                session_id=session_id or session.id,
            )
        raise _invalid_request_error(session, "Session timestamp is invalid.")
    return updated_at


def _next_updated_at(session: Session) -> datetime:
    updated_at = _require_valid_updated_at(session)
    try:
        return max(
            datetime.now(UTC),
            updated_at + timedelta(microseconds=1),
        )
    except (OverflowError, TypeError, ValueError):
        raise _invalid_request_error(
            session,
            "Session timestamp is invalid.",
        ) from None
