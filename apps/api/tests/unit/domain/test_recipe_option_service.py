from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.errors import AppError, ErrorCode
from domain.ingredients import Ingredient, IngredientSource
from domain.recipe_option_service import (
    begin_option_generation,
    commit_option_batch,
    normalize_recipe_name,
    restore_option_generation,
    validate_option_names,
)
from domain.recipe_options import (
    Difficulty,
    IngredientRequirement,
    RecipeOption,
    RecipeOptionDraft,
    RecipePreferences,
)
from domain.session_service import confirmed_ingredient_names
from domain.sessions import Session, SessionStage
from schemas.sessions import SessionResponse

NOW = datetime(2026, 7, 30, tzinfo=UTC)


def option_draft(name: str) -> RecipeOptionDraft:
    return RecipeOptionDraft(
        name=name,
        summary="A comforting dish.",
        cuisine="Indian",
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato"],
    )


def confirmed_session(**updates: object) -> Session:
    session = Session(
        id="session-1",
        stage=SessionStage.INGREDIENTS_CONFIRMED,
        ingredients=[
            Ingredient(
                id="ingredient-1",
                name="Tomato",
                source=IngredientSource.DETECTED,
                confidence=0.92,
                confirmed=True,
            ),
            Ingredient(
                id="ingredient-2",
                name="Onion",
                source=IngredientSource.PANTRY_SUGGESTION,
                confirmed=False,
            ),
        ],
        created_at=NOW,
        updated_at=NOW,
    )
    return session.model_copy(update=updates)


def stored_option(name: str) -> RecipeOption:
    return RecipeOption(**option_draft(name).model_dump())


def test_recipe_name_normalization_is_case_and_space_insensitive() -> None:
    assert normalize_recipe_name("  Straße   CURRY \n") == "strasse curry"


def test_batch_rejects_internal_duplicates() -> None:
    drafts = [
        option_draft("Tomato Curry"),
        option_draft(" tomato  curry "),
    ]

    with pytest.raises(AppError, match="repeated recipe") as error:
        validate_option_names(drafts, excluded_names=set())

    assert error.value.code is ErrorCode.RECIPE_DUPLICATE
    assert error.value.retryable is True


def test_batch_rejects_a_prior_duplicate_using_canonical_comparison() -> None:
    with pytest.raises(AppError, match="repeated recipe") as error:
        validate_option_names(
            [option_draft("Tomato Curry")],
            excluded_names={"  TOMATO   curry "},
        )

    assert error.value.code is ErrorCode.RECIPE_DUPLICATE


@pytest.mark.parametrize(
    ("stage", "more"),
    [
        (SessionStage.OPTIONS_READY, False),
        (SessionStage.INGREDIENTS_CONFIRMED, True),
    ],
)
def test_generation_requires_the_stage_for_its_requested_mode(
    stage: SessionStage,
    more: bool,
) -> None:
    session = confirmed_session(stage=stage)

    with pytest.raises(AppError) as error:
        begin_option_generation(session, RecipePreferences(), more=more)

    assert error.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert session.stage is stage


def test_first_generation_returns_only_confirmed_ingredient_names() -> None:
    session = confirmed_session()

    generating, previous_stage, context = begin_option_generation(
        session,
        RecipePreferences(option_count=2),
        more=False,
    )

    assert confirmed_ingredient_names(generating) == ["Tomato"]
    assert previous_stage is SessionStage.INGREDIENTS_CONFIRMED
    assert context.previous_stage is previous_stage
    assert context.more is False
    assert generating.stage is SessionStage.GENERATING_OPTIONS


def test_generation_rejects_a_session_without_confirmed_ingredients() -> None:
    session = confirmed_session(
        ingredients=[
            Ingredient(
                id="ingredient-2",
                name="Onion",
                source=IngredientSource.PANTRY_SUGGESTION,
                confirmed=False,
            )
        ]
    )

    with pytest.raises(AppError) as error:
        begin_option_generation(session, RecipePreferences(), more=False)

    assert error.value.code is ErrorCode.INGREDIENTS_NOT_CONFIRMED
    assert session.stage is SessionStage.INGREDIENTS_CONFIRMED


def test_first_generation_stores_validated_preferences_and_starts_fresh() -> None:
    session = confirmed_session(
        excluded_recipe_names={"  stale   Curry "},
        preferences=RecipePreferences(option_count=1),
    )

    generating, _, _ = begin_option_generation(
        session,
        {
            "option_count": 2,
            "preferred_cuisines": ["  Indian  "],
        },
        more=False,
    )

    assert generating.preferences == RecipePreferences(
        option_count=2,
        preferred_cuisines=["Indian"],
    )
    assert generating.excluded_recipe_names == set()
    assert session.excluded_recipe_names == {"  stale   Curry "}


def test_invalid_first_generation_does_not_clear_exclusions() -> None:
    session = confirmed_session(
        stage=SessionStage.REVIEWING_INGREDIENTS,
        excluded_recipe_names={"tomato curry"},
    )

    with pytest.raises(AppError):
        begin_option_generation(session, RecipePreferences(), more=False)

    assert session.excluded_recipe_names == {"tomato curry"}


def test_begin_rejects_invalid_preference_data() -> None:
    session = confirmed_session()

    with pytest.raises(ValidationError):
        begin_option_generation(session, {"option_count": 7}, more=False)

    assert session.stage is SessionStage.INGREDIENTS_CONFIRMED
    assert session.preferences == RecipePreferences()


def test_more_preserves_every_shown_name_as_a_canonical_exclusion() -> None:
    session = confirmed_session(
        stage=SessionStage.OPTIONS_READY,
        recipe_options=[
            stored_option("  Tomato   Curry "),
            stored_option("Onion Soup"),
        ],
        excluded_recipe_names={"  Older   Dish "},
        option_batch_number=1,
    )

    generating, previous_stage, context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=True,
    )

    assert previous_stage is SessionStage.OPTIONS_READY
    assert context.previous_stage is previous_stage
    assert context.more is True
    assert generating.excluded_recipe_names == {
        "tomato curry",
        "onion soup",
        "older dish",
    }
    assert [option.name for option in generating.recipe_options] == [
        "  Tomato   Curry ",
        "Onion Soup",
    ]


def test_generation_attempt_identity_is_server_owned_unique_and_not_public() -> None:
    session = confirmed_session()

    first, _, first_context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )
    second, _, second_context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )

    assert first.option_generation_id == first_context.generation_id
    assert second.option_generation_id == second_context.generation_id
    assert first.option_generation_id != second.option_generation_id
    assert str(UUID(first.option_generation_id)) == first.option_generation_id
    assert (
        "option_generation_id" not in SessionResponse.model_validate(first).model_dump()
    )


def test_stale_context_cannot_commit_a_newer_same_mode_attempt() -> None:
    session = confirmed_session()
    _, _, stale_context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )
    generating, _, _ = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )
    before_commit = generating.model_copy(deep=True)

    with pytest.raises(AppError, match="context does not match") as error:
        commit_option_batch(
            generating,
            [option_draft("Tomato Curry")],
            stale_context,
        )

    assert error.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert error.value.retryable is False
    assert generating == before_commit


def test_stale_context_cannot_restore_over_a_newer_same_mode_attempt() -> None:
    session = confirmed_session()
    _, _, stale_context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )
    generating, _, _ = begin_option_generation(
        session,
        RecipePreferences(option_count=2),
        more=False,
    )
    before_restore = generating.model_copy(deep=True)

    with pytest.raises(AppError, match="context does not match") as error:
        restore_option_generation(generating, stale_context)

    assert error.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert error.value.retryable is False
    assert generating == before_restore


def test_first_commit_replaces_options_despite_a_stale_nonzero_batch_count() -> None:
    generating, _, context = begin_option_generation(
        confirmed_session(
            recipe_options=[stored_option("Stale option")],
            option_batch_number=7,
        ),
        RecipePreferences(option_count=2),
        more=False,
    )
    drafts = [option_draft("Tomato Curry"), option_draft("Onion Soup")]
    committed = commit_option_batch(generating, drafts, context)

    assert committed.stage is SessionStage.OPTIONS_READY
    assert committed.option_generation_id is None
    assert committed.option_batch_number == 8
    assert [option.name for option in committed.recipe_options] == [
        "Tomato Curry",
        "Onion Soup",
    ]
    option_ids = [option.id for option in committed.recipe_options]
    assert len(set(option_ids)) == 2
    assert all(str(UUID(option_id)) == option_id for option_id in option_ids)
    assert committed.recipe_options[0].nutrition is None
    assert committed.excluded_recipe_names == {"tomato curry", "onion soup"}


def test_more_commit_appends_despite_an_inconsistent_zero_batch_count() -> None:
    previous = stored_option("Tomato Curry")
    session = confirmed_session(
        stage=SessionStage.OPTIONS_READY,
        recipe_options=[previous],
        excluded_recipe_names={" TOMATO   CURRY ", "  Historic Dish "},
        option_batch_number=0,
    )
    generating, _, context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=True,
    )

    committed = commit_option_batch(
        generating,
        [option_draft("Onion Soup")],
        context,
    )

    assert committed.option_batch_number == 1
    assert [option.id for option in committed.recipe_options] == [
        previous.id,
        committed.recipe_options[1].id,
    ]
    assert [option.name for option in committed.recipe_options] == [
        "Tomato Curry",
        "Onion Soup",
    ]
    assert committed.excluded_recipe_names == {
        "tomato curry",
        "historic dish",
        "onion soup",
    }


def test_commit_rejects_duplicates_before_changing_the_session() -> None:
    session = confirmed_session(
        stage=SessionStage.OPTIONS_READY,
        recipe_options=[stored_option("Tomato Curry")],
        option_batch_number=1,
    )
    generating, _, context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=True,
    )

    with pytest.raises(AppError) as error:
        commit_option_batch(
            generating,
            [option_draft(" tomato  CURRY ")],
            context,
        )

    assert error.value.code is ErrorCode.RECIPE_DUPLICATE
    assert generating.stage is SessionStage.GENERATING_OPTIONS
    assert generating.option_batch_number == 1
    assert len(generating.recipe_options) == 1


@pytest.mark.parametrize("used_ingredient", ["Paneer", "Coconut milk"])
def test_commit_rejects_used_ingredients_not_copied_from_confirmed_names(
    used_ingredient: str,
) -> None:
    generating, _, context = begin_option_generation(
        confirmed_session(),
        RecipePreferences(option_count=1),
        more=False,
    )
    draft = option_draft("Tomato Curry").model_copy(
        update={"used_ingredients": [used_ingredient]}
    )

    with pytest.raises(AppError, match="unconfirmed") as error:
        commit_option_batch(
            generating,
            [draft],
            context,
        )

    assert error.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert error.value.status_code == 502
    assert error.value.retryable is True
    assert generating.recipe_options == []


def test_commit_accepts_confirmed_names_despite_model_casing() -> None:
    """'tomato' for a confirmed 'Tomato' is honest output, not a hallucination."""
    generating, _, context = begin_option_generation(
        confirmed_session(),
        RecipePreferences(option_count=1),
        more=False,
    )
    draft = option_draft("Tomato Curry").model_copy(
        update={"used_ingredients": ["tomato"]}
    )

    committed = commit_option_batch(
        generating,
        [draft],
        context,
    )

    assert committed.recipe_options[0].used_ingredients == ["tomato"]


def test_commit_rejects_repeated_ingredient_names_before_persisting() -> None:
    generating, _, context = begin_option_generation(
        confirmed_session(),
        RecipePreferences(option_count=1),
        more=False,
    )
    draft = option_draft("Tomato Curry").model_copy(
        update={
            "missing_ingredients": [
                IngredientRequirement(
                    name="Paneer",
                    reason="Needed for body.",
                )
            ],
            "optional_ingredients": [
                IngredientRequirement(
                    name=" paneer ",
                    reason="Useful garnish.",
                )
            ],
        }
    )

    with pytest.raises(AppError, match="repeated ingredient") as error:
        commit_option_batch(
            generating,
            [draft],
            context,
        )

    assert error.value.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert error.value.retryable is True
    assert generating.recipe_options == []


def test_failed_generation_restores_exact_stage_and_prior_options() -> None:
    previous_option = stored_option("Tomato Curry")
    session = confirmed_session(
        stage=SessionStage.OPTIONS_READY,
        preferences=RecipePreferences(
            option_count=3,
            preferred_cuisines=["Japanese"],
        ),
        recipe_options=[previous_option],
        excluded_recipe_names={"tomato curry", "historic dish"},
        option_batch_number=7,
        option_generation_id="5d0db4da-6f84-4a7a-b32f-1c33b7ab72c9",
    )
    generating, previous_stage, context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=True,
    )
    session.preferences.preferred_cuisines.append("Mutated later")
    session.excluded_recipe_names.add("Mutated later")

    with pytest.raises(ValidationError):
        context.more = False

    restored = restore_option_generation(generating, context)

    assert restored.stage is SessionStage.OPTIONS_READY
    assert restored.preferences == RecipePreferences(
        option_count=3,
        preferred_cuisines=["Japanese"],
    )
    assert restored.recipe_options == [previous_option]
    assert restored.excluded_recipe_names == {"tomato curry", "historic dish"}
    assert restored.option_batch_number == 7
    assert restored.option_generation_id == "5d0db4da-6f84-4a7a-b32f-1c33b7ab72c9"
    assert restored.updated_at == NOW
    assert restored is not session
    assert generating.stage is SessionStage.GENERATING_OPTIONS
    assert previous_stage is SessionStage.OPTIONS_READY


def test_failed_first_generation_restores_ingredients_confirmed() -> None:
    session = confirmed_session()
    generating, previous_stage, context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )

    restored = restore_option_generation(generating, context)

    assert restored.stage is SessionStage.INGREDIENTS_CONFIRMED
    assert previous_stage is SessionStage.INGREDIENTS_CONFIRMED
    assert generating.stage is SessionStage.GENERATING_OPTIONS


def test_option_generation_operations_do_not_mutate_input_sessions() -> None:
    session = confirmed_session()
    generating, _, context = begin_option_generation(
        session,
        RecipePreferences(option_count=1),
        more=False,
    )
    committed = commit_option_batch(
        generating,
        [option_draft("Tomato Curry")],
        context,
    )

    committed.recipe_options[0].used_ingredients.append("Changed after commit.")

    assert session.stage is SessionStage.INGREDIENTS_CONFIRMED
    assert session.recipe_options == []
    assert generating.stage is SessionStage.GENERATING_OPTIONS
    assert generating.recipe_options == []
