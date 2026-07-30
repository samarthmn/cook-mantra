"""Pure rules for beginning, validating, and committing recipe options."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from core.errors import AppError, ErrorCode
from domain.recipe_options import (
    NutritionEstimate,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
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
    context = OptionGenerationContext(
        previous_stage=session.stage,
        more=more,
        session_id=session.id,
        rollback_snapshot=session.model_dump_json(),
    )
    exclusions = _canonical_exclusions(session) if more else set()
    generating = session.model_copy(
        deep=True,
        update={
            "stage": SessionStage.GENERATING_OPTIONS,
            "preferences": validated_preferences.model_copy(deep=True),
            "excluded_recipe_names": exclusions,
            "updated_at": datetime.now(UTC),
        },
    )
    return generating, session.stage, context


def commit_option_batch(
    session: Session,
    drafts: Sequence[RecipeOptionDraft],
    nutrition: Sequence[NutritionEstimate | None],
    context: OptionGenerationContext,
) -> Session:
    """Commit one validated batch and return a detached options-ready session."""
    require_stage(session, SessionStage.GENERATING_OPTIONS)
    _require_matching_context(session, context)
    if len(drafts) != len(nutrition):
        raise AppError(
            code=ErrorCode.INVALID_REQUEST,
            message="Each recipe option must have a nutrition result.",
            status_code=422,
            retryable=False,
            session_id=session.id,
        )
    validate_option_names(drafts, session.excluded_recipe_names)

    new_options = [
        RecipeOption(
            **draft.model_dump(),
            nutrition=estimate.model_copy(deep=True) if estimate is not None else None,
        )
        for draft, estimate in zip(drafts, nutrition, strict=True)
    ]
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
