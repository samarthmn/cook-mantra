"""Pure rules for beginning, validating, and committing recipe options."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

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
) -> tuple[Session, SessionStage]:
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
    return generating, session.stage


def commit_option_batch(
    session: Session,
    drafts: Sequence[RecipeOptionDraft],
    nutrition: Sequence[NutritionEstimate | None],
) -> Session:
    """Commit one validated batch and return a detached options-ready session."""
    require_stage(session, SessionStage.GENERATING_OPTIONS)
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
        if session.option_batch_number > 0
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
    previous_stage: SessionStage,
) -> Session:
    """Restore a failed option workflow to its exact valid entry stage."""
    require_stage(session, SessionStage.GENERATING_OPTIONS)
    if previous_stage not in _OPTION_ENTRY_STAGES:
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message="Recipe option generation cannot restore that session stage.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )
    return session.model_copy(
        deep=True,
        update={
            "stage": previous_stage,
            "updated_at": datetime.now(UTC),
        },
    )


def _canonical_exclusions(session: Session) -> set[str]:
    names = {*session.excluded_recipe_names}
    names.update(option.name for option in session.recipe_options)
    return {normalize_recipe_name(name) for name in names}
