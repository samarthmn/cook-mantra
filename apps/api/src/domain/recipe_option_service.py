"""Pure rules for beginning, validating, and committing recipe options."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.errors import AppError, ErrorCode
from domain.recipe_options import (
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
    validate_confirmed_used_ingredients,
    validate_unique_option_ingredient_names,
)
from domain.session_service import confirmed_ingredient_names, require_stage
from domain.sessions import Session, SessionStage

_OPTION_ENTRY_STAGES = frozenset(
    {
        SessionStage.INGREDIENTS_CONFIRMED,
        SessionStage.OPTIONS_READY,
    }
)


class OptionGenerationContext(BaseModel):
    """Immutable intent and rollback data for one option-generation request."""

    model_config = ConfigDict(frozen=True)

    previous_stage: SessionStage
    more: bool
    session_id: str
    generation_id: str
    rollback_snapshot: str = Field(repr=False)


def normalize_recipe_name(name: str) -> str:
    """Return the canonical form used for every recipe-name comparison."""
    return " ".join(name.split()).casefold()


def validate_option_names(
    drafts: Sequence[RecipeOptionDraft],
    excluded_names: set[str],
) -> None:
    """Reject generated names repeated within or before the current batch."""
    seen_names = {normalize_recipe_name(name) for name in excluded_names}
    for draft in drafts:
        normalized_name = normalize_recipe_name(draft.name)
        if normalized_name in seen_names:
            raise AppError(
                code=ErrorCode.RECIPE_DUPLICATE,
                message="Generated options contain a repeated recipe name.",
                status_code=409,
                retryable=True,
            )
        seen_names.add(normalized_name)


def validate_option_ingredients(
    drafts: Sequence[RecipeOptionDraft],
    confirmed_names: Sequence[str],
) -> None:
    """Enforce ingredient-name consistency and the confirmation boundary."""
    try:
        validate_unique_option_ingredient_names(drafts)
    except ValueError:
        raise AppError(
            code=ErrorCode.MODEL_OUTPUT_INVALID,
            message="Generated options contain a repeated ingredient name.",
            status_code=502,
            retryable=True,
        ) from None

    try:
        validate_confirmed_used_ingredients(drafts, confirmed_names)
    except ValueError:
        raise AppError(
            code=ErrorCode.MODEL_OUTPUT_INVALID,
            message="Generated options contain an unconfirmed used ingredient.",
            status_code=502,
            retryable=True,
        ) from None


def begin_option_generation(
    session: Session,
    preferences: RecipePreferences | Mapping[str, object],
    more: bool,
) -> tuple[Session, SessionStage, OptionGenerationContext]:
    """Validate an option request and return its detached generating state."""
    expected_stage = (
        SessionStage.OPTIONS_READY if more else SessionStage.INGREDIENTS_CONFIRMED
    )
    require_stage(session, expected_stage)
    if not confirmed_ingredient_names(session):
        raise AppError(
            code=ErrorCode.INGREDIENTS_NOT_CONFIRMED,
            message="Ingredients must be confirmed before this operation.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )

    validated_preferences = RecipePreferences.model_validate(preferences)
    generation_id = str(uuid4())
    context = OptionGenerationContext(
        previous_stage=session.stage,
        more=more,
        session_id=session.id,
        generation_id=generation_id,
        rollback_snapshot=session.model_dump_json(),
    )
    exclusions = _canonical_exclusions(session) if more else set()
    generating = session.model_copy(
        deep=True,
        update={
            "stage": SessionStage.GENERATING_OPTIONS,
            "preferences": validated_preferences.model_copy(deep=True),
            "excluded_recipe_names": exclusions,
            "option_generation_id": generation_id,
            "updated_at": datetime.now(UTC),
        },
    )
    return generating, session.stage, context


def commit_option_batch(
    session: Session,
    drafts: Sequence[RecipeOptionDraft],
    context: OptionGenerationContext,
) -> Session:
    """Commit one validated batch and return a detached options-ready session."""
    require_stage(session, SessionStage.GENERATING_OPTIONS)
    _require_matching_context(session, context)
    validate_option_names(drafts, session.excluded_recipe_names)
    validate_option_ingredients(drafts, confirmed_ingredient_names(session))

    new_options = [RecipeOption(**draft.model_dump()) for draft in drafts]
    previous_options = (
        [option.model_copy(deep=True) for option in session.recipe_options]
        if context.more
        else []
    )
    committed_options = [*previous_options, *new_options]
    exclusions = {normalize_recipe_name(name) for name in session.excluded_recipe_names}
    exclusions.update(
        normalize_recipe_name(option.name) for option in committed_options
    )
    return session.model_copy(
        deep=True,
        update={
            "stage": SessionStage.OPTIONS_READY,
            "recipe_options": committed_options,
            "excluded_recipe_names": exclusions,
            "option_batch_number": session.option_batch_number + 1,
            "option_generation_id": None,
            "updated_at": datetime.now(UTC),
        },
    )


def restore_option_generation(
    session: Session,
    context: OptionGenerationContext,
) -> Session:
    """Restore every field from the immutable pre-generation snapshot."""
    require_stage(session, SessionStage.GENERATING_OPTIONS)
    _require_matching_context(session, context)
    restored = Session.model_validate_json(context.rollback_snapshot)
    if restored.stage is not context.previous_stage:
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Recipe option generation has invalid rollback data.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )
    return restored.model_copy(deep=True)


def _canonical_exclusions(session: Session) -> set[str]:
    names = {*session.excluded_recipe_names}
    names.update(option.name for option in session.recipe_options)
    return {normalize_recipe_name(name) for name in names}


def _require_matching_context(
    session: Session,
    context: OptionGenerationContext,
) -> None:
    expected_stage = (
        SessionStage.OPTIONS_READY
        if context.more
        else SessionStage.INGREDIENTS_CONFIRMED
    )
    if (
        context.session_id != session.id
        or context.generation_id != session.option_generation_id
        or context.previous_stage is not expected_stage
        or context.previous_stage not in _OPTION_ENTRY_STAGES
    ):
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Recipe option generation context does not match the session.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )
