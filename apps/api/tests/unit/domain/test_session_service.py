from datetime import UTC, datetime

import pytest

from core.errors import AppError, ErrorCode
from domain.ingredients import Ingredient, IngredientDraft, IngredientSource
from domain.session_service import (
    confirm_ingredients,
    confirmed_ingredients,
    review_ingredients,
)
from domain.sessions import Session, SessionStage


@pytest.fixture
def review_session() -> Session:
    return Session(
        id="session-1",
        stage=SessionStage.REVIEWING_INGREDIENTS,
        ingredients=[
            Ingredient(
                id="ingredient-1",
                name="Tomato",
                source=IngredientSource.DETECTED,
                confidence=0.91,
            )
        ],
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        updated_at=datetime(2026, 7, 30, tzinfo=UTC),
    )


@pytest.fixture
def review_session_with_selection(review_session: Session) -> Session:
    return review_session.model_copy(
        update={
            "ingredients": [
                review_session.ingredients[0].model_copy(update={"confirmed": True})
            ]
        }
    )


@pytest.fixture
def confirmed_session(review_session_with_selection: Session) -> Session:
    return review_session_with_selection.model_copy(
        update={"stage": SessionStage.INGREDIENTS_CONFIRMED}
    )


def test_confirmation_requires_a_selected_ingredient(review_session: Session) -> None:
    with pytest.raises(AppError) as error:
        confirm_ingredients(review_session)

    assert error.value.code is ErrorCode.INVALID_REQUEST
    assert error.value.status_code == 422


def test_confirmation_moves_session_forward(
    review_session_with_selection: Session,
) -> None:
    confirmed = confirm_ingredients(review_session_with_selection)

    assert confirmed.stage is SessionStage.INGREDIENTS_CONFIRMED


def test_review_is_rejected_after_confirmation(confirmed_session: Session) -> None:
    with pytest.raises(AppError) as error:
        review_ingredients(confirmed_session, [])

    assert error.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert error.value.status_code == 409
    assert error.value.session_id == "session-1"


@pytest.mark.parametrize(
    "operation",
    [
        lambda session: review_ingredients(session, []),
        confirm_ingredients,
    ],
)
@pytest.mark.parametrize(
    "stage",
    [
        stage
        for stage in SessionStage
        if stage is not SessionStage.REVIEWING_INGREDIENTS
    ],
)
def test_editing_and_confirmation_require_reviewing_stage(
    operation,
    stage: SessionStage,
) -> None:
    session = Session(
        id="session-2",
        stage=stage,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        updated_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    with pytest.raises(AppError) as error:
        operation(session)

    assert error.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert error.value.status_code == 409
    assert error.value.session_id == "session-2"


def test_review_reconciles_ingredients_and_advances_updated_at(
    review_session: Session,
) -> None:
    reviewed = review_ingredients(
        review_session,
        [IngredientDraft(id="ingredient-1", name="Cherry tomato", confirmed=True)],
    )

    assert reviewed.ingredients[0].name == "Cherry tomato"
    assert reviewed.ingredients[0].confirmed is True
    assert reviewed.updated_at > review_session.updated_at


def test_review_does_not_mutate_its_input_session(review_session: Session) -> None:
    review_ingredients(
        review_session,
        [IngredientDraft(id="ingredient-1", name="Cherry tomato", confirmed=True)],
    )

    assert review_session.ingredients[0].name == "Tomato"
    assert review_session.ingredients[0].confirmed is False
    assert review_session.updated_at == datetime(2026, 7, 30, tzinfo=UTC)


def test_confirmation_does_not_mutate_its_input_session(
    review_session_with_selection: Session,
) -> None:
    confirmed = confirm_ingredients(review_session_with_selection)

    assert review_session_with_selection.stage is SessionStage.REVIEWING_INGREDIENTS
    assert confirmed is not review_session_with_selection


def test_confirmed_ingredients_returns_only_selected_items(
    confirmed_session: Session,
) -> None:
    session = confirmed_session.model_copy(
        update={
            "ingredients": [
                confirmed_session.ingredients[0],
                Ingredient(
                    id="ingredient-2",
                    name="Onion",
                    source=IngredientSource.PANTRY_SUGGESTION,
                    confirmed=False,
                ),
            ]
        }
    )

    assert [ingredient.name for ingredient in confirmed_ingredients(session)] == [
        "Tomato"
    ]


@pytest.mark.parametrize(
    "stage",
    [
        SessionStage.INGREDIENTS_CONFIRMED,
        SessionStage.GENERATING_OPTIONS,
        SessionStage.OPTIONS_READY,
        SessionStage.GENERATING_RECIPES,
        SessionStage.RECIPES_READY,
    ],
)
def test_confirmed_ingredients_is_available_after_confirmation(
    stage: SessionStage,
) -> None:
    session = Session(
        id="session-3",
        stage=stage,
        ingredients=[
            Ingredient(
                id="ingredient-3",
                name="Spinach",
                source=IngredientSource.USER_ADDED,
                confirmed=True,
            )
        ],
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        updated_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert [ingredient.name for ingredient in confirmed_ingredients(session)] == [
        "Spinach"
    ]


def test_confirmed_ingredients_rejects_a_session_before_confirmation(
    review_session_with_selection: Session,
) -> None:
    with pytest.raises(AppError) as error:
        confirmed_ingredients(review_session_with_selection)

    assert error.value.code is ErrorCode.INGREDIENTS_NOT_CONFIRMED
    assert error.value.status_code == 409
    assert error.value.session_id == "session-1"
