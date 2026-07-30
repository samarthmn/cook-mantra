"""Session transitions and confirmed ingredient access."""

from datetime import UTC, datetime

from core.errors import AppError, ErrorCode
from domain.ingredients import Ingredient, IngredientDraft, apply_ingredient_review
from domain.sessions import Session, SessionStage

_POST_CONFIRMATION_STAGES = frozenset(
    {
        SessionStage.INGREDIENTS_CONFIRMED,
        SessionStage.GENERATING_OPTIONS,
        SessionStage.OPTIONS_READY,
        SessionStage.GENERATING_RECIPES,
        SessionStage.RECIPES_READY,
    }
)


def require_stage(session: Session, expected: SessionStage) -> None:
    """Raise a public error unless a session is in the expected stage."""
    if session.stage is not expected:
        raise AppError(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message=f"Session must be {expected.value} for this operation.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )


def review_ingredients(session: Session, drafts: list[IngredientDraft]) -> Session:
    """Apply a review submission while the session is being reviewed."""
    require_stage(session, SessionStage.REVIEWING_INGREDIENTS)
    return session.model_copy(
        update={
            "ingredients": apply_ingredient_review(session.ingredients, drafts),
            "updated_at": datetime.now(UTC),
        }
    )


def confirm_ingredients(session: Session) -> Session:
    """Confirm the reviewed ingredients before recipe generation."""
    require_stage(session, SessionStage.REVIEWING_INGREDIENTS)
    if not any(ingredient.confirmed for ingredient in session.ingredients):
        raise AppError(
            code=ErrorCode.INVALID_REQUEST,
            message="Select at least one ingredient before confirmation.",
            status_code=422,
            retryable=False,
            session_id=session.id,
        )
    return session.model_copy(
        update={
            "stage": SessionStage.INGREDIENTS_CONFIRMED,
            "updated_at": datetime.now(UTC),
        }
    )


def confirmed_ingredients(session: Session) -> list[Ingredient]:
    """Return ingredients approved for generation after confirmation."""
    if session.stage not in _POST_CONFIRMATION_STAGES:
        raise AppError(
            code=ErrorCode.INGREDIENTS_NOT_CONFIRMED,
            message="Ingredients must be confirmed before this operation.",
            status_code=409,
            retryable=False,
            session_id=session.id,
        )
    return [ingredient for ingredient in session.ingredients if ingredient.confirmed]


def confirmed_ingredient_names(session: Session) -> list[str]:
    """Return only the ingredient names approved for recipe generation."""
    return [ingredient.name for ingredient in confirmed_ingredients(session)]
