from datetime import UTC, datetime

import pytest

from core.errors import AppError, ErrorCode
from domain.recipe_options import Difficulty, RecipeOption, RecipePreferences
from domain.recipe_service import (
    begin_recipe_generation,
    commit_recipe_results,
    restore_after_recipe_failure,
)
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeFailure,
    RecipeIngredient,
    RecipeStep,
)
from domain.sessions import Session, SessionStage

NOW = datetime(2026, 7, 30, tzinfo=UTC)


def stored_option(
    option_id: str,
    name: str,
    *,
    cuisine: str = "Indian",
) -> RecipeOption:
    return RecipeOption(
        id=option_id,
        name=name,
        summary=f"A comforting {name}.",
        cuisine=cuisine,
        total_minutes=30,
        difficulty=Difficulty.EASY,
        used_ingredients=["Tomato"],
    )


def complete_recipe(
    option_id: str,
    *,
    name: str = "Tomato Curry",
    cuisine: str = "Indian",
    total_minutes: int = 30,
) -> CompleteRecipe:
    return CompleteRecipe(
        option_id=option_id,
        name=name,
        cuisine=cuisine,
        servings=2,
        total_minutes=total_minutes,
        ingredients=(
            RecipeIngredient(
                name="Tomato",
                quantity="3 medium",
                availability=IngredientAvailability.AVAILABLE,
            ),
        ),
        steps=(RecipeStep(number=1, instruction="Cook until tender."),),
    )


def recipe_failure(
    option_id: str,
    *,
    message: str = "The generated recipe was invalid.",
) -> RecipeFailure:
    return RecipeFailure(
        option_id=option_id,
        code=ErrorCode.MODEL_OUTPUT_INVALID,
        message=message,
        retryable=True,
    )


@pytest.fixture
def options() -> list[RecipeOption]:
    return [
        stored_option("option-1", "Tomato Curry"),
        stored_option("option-2", "Onion Soup", cuisine="French"),
        stored_option("option-3", "Spinach Rice"),
    ]


@pytest.fixture
def options_session(options: list[RecipeOption]) -> Session:
    return Session(
        id="session-1",
        stage=SessionStage.OPTIONS_READY,
        preferences=RecipePreferences(
            preferred_cuisines=["Indian"],
            servings=2,
            option_count=3,
        ),
        recipe_options=options,
        created_at=NOW,
        updated_at=NOW,
    )


def assert_invalid_request(error: AppError) -> None:
    assert error.code is ErrorCode.INVALID_REQUEST
    assert error.status_code == 422
    assert error.retryable is False
    assert error.details == {}
    assert error.session_id == "session-1"


def test_selection_resolves_detached_server_owned_options_in_request_order(
    options_session: Session,
) -> None:
    generating, selected, previous_stage = begin_recipe_generation(
        options_session,
        ["option-3", "option-1"],
    )

    assert generating.stage is SessionStage.GENERATING_RECIPES
    assert selected == [
        options_session.recipe_options[2],
        options_session.recipe_options[0],
    ]
    assert previous_stage is SessionStage.OPTIONS_READY
    assert selected[0] is not options_session.recipe_options[2]
    assert selected[0] is not generating.recipe_options[2]

    selected[0].warnings.append("Changed selected copy.")
    generating.recipe_options[0].warnings.append("Changed generating copy.")

    assert options_session.recipe_options[2].warnings == []
    assert options_session.recipe_options[0].warnings == []


def test_begin_retains_prior_results_options_and_preferences(
    options_session: Session,
) -> None:
    prior_recipe = complete_recipe("option-1")
    prior_failure = recipe_failure("option-2")
    recipes_session = options_session.model_copy(
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": {"option-1": prior_recipe},
            "recipe_failures": {"option-2": prior_failure},
        }
    )

    generating, _, previous_stage = begin_recipe_generation(
        recipes_session,
        ["option-3"],
    )

    assert previous_stage is SessionStage.RECIPES_READY
    assert generating.recipe_options == recipes_session.recipe_options
    assert generating.preferences == recipes_session.preferences
    assert generating.complete_recipes == {"option-1": prior_recipe}
    assert generating.recipe_failures == {"option-2": prior_failure}
    assert generating.complete_recipes is not recipes_session.complete_recipes
    assert generating.recipe_failures is not recipes_session.recipe_failures


@pytest.mark.parametrize("count", [0, 7])
def test_begin_enforces_selection_size_for_direct_domain_callers(
    options_session: Session,
    count: int,
) -> None:
    option_ids = [f"option-{index}" for index in range(1, count + 1)]
    before = options_session.model_copy(deep=True)

    with pytest.raises(AppError) as raised:
        begin_recipe_generation(options_session, option_ids)

    assert_invalid_request(raised.value)
    assert raised.value.message == "Select between one and six recipe options."
    assert options_session == before


def test_begin_rejects_normalized_duplicate_ids_for_direct_domain_callers(
    options_session: Session,
) -> None:
    with pytest.raises(AppError) as raised:
        begin_recipe_generation(options_session, ["option-1", " option-1 "])

    assert_invalid_request(raised.value)
    assert raised.value.message == "Recipe option IDs must be unique."


@pytest.mark.parametrize("option_ids", [["   "], [1]])  # type: ignore[list-item]
def test_begin_rejects_invalid_ids_with_a_sanitized_error(
    options_session: Session,
    option_ids: list[str],
) -> None:
    with pytest.raises(AppError) as raised:
        begin_recipe_generation(options_session, option_ids)

    assert_invalid_request(raised.value)
    assert raised.value.message == "Recipe option IDs must not be blank."


def test_unknown_option_id_is_a_safe_not_found_error(
    options_session: Session,
) -> None:
    unknown_id = "private-client-value"

    with pytest.raises(AppError) as raised:
        begin_recipe_generation(options_session, [unknown_id])

    error = raised.value
    assert error.code is ErrorCode.RESOURCE_NOT_FOUND
    assert error.message == "Unknown recipe option."
    assert unknown_id not in str(error)
    assert error.status_code == 404
    assert error.retryable is False
    assert error.details == {}
    assert error.session_id == "session-1"
    assert options_session.stage is SessionStage.OPTIONS_READY


@pytest.mark.parametrize(
    "stage",
    [
        stage
        for stage in SessionStage
        if stage not in {SessionStage.OPTIONS_READY, SessionStage.RECIPES_READY}
    ],
)
def test_begin_requires_an_option_or_recipe_ready_session(
    options_session: Session,
    stage: SessionStage,
) -> None:
    session = options_session.model_copy(update={"stage": stage})

    with pytest.raises(AppError) as raised:
        begin_recipe_generation(session, ["option-1"])

    error = raised.value
    assert error.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert error.status_code == 409
    assert error.retryable is False
    assert error.session_id == "session-1"
    assert session.stage is stage


def test_begin_advances_timestamp_without_mutating_input(
    options_session: Session,
) -> None:
    generating, _, _ = begin_recipe_generation(options_session, ["option-1"])

    assert generating.updated_at > options_session.updated_at
    assert options_session.updated_at == NOW
    assert options_session.stage is SessionStage.OPTIONS_READY


def test_partial_success_moves_session_to_recipes_ready(
    options_session: Session,
) -> None:
    generating, _, _ = begin_recipe_generation(
        options_session,
        ["option-1", "option-2"],
    )
    recipe = complete_recipe("option-1")
    failure = recipe_failure("option-2")

    completed = commit_recipe_results(
        generating,
        successes={"option-1": recipe},
        failures={"option-2": failure},
    )

    assert completed.stage is SessionStage.RECIPES_READY
    assert completed.complete_recipes == {"option-1": recipe}
    assert completed.recipe_failures == {"option-2": failure}
    assert completed.complete_recipes["option-1"] is not recipe
    assert completed.recipe_failures["option-2"] is not failure


def test_commit_orders_results_by_stored_option_order(
    options_session: Session,
) -> None:
    generating, _, _ = begin_recipe_generation(
        options_session,
        ["option-3", "option-1", "option-2"],
    )

    completed = commit_recipe_results(
        generating,
        successes={
            "option-3": complete_recipe("option-3", name="Spinach Rice"),
            "option-1": complete_recipe("option-1"),
        },
        failures={"option-2": recipe_failure("option-2")},
    )

    assert list(completed.complete_recipes) == ["option-1", "option-3"]
    assert list(completed.recipe_failures) == ["option-2"]


def test_commit_does_not_mutate_or_alias_inputs(
    options_session: Session,
) -> None:
    generating, _, _ = begin_recipe_generation(options_session, ["option-1"])
    recipe = complete_recipe("option-1")
    successes = {"option-1": recipe}
    failures: dict[str, RecipeFailure] = {}
    generating_before = generating.model_copy(deep=True)

    completed = commit_recipe_results(generating, successes, failures)
    successes.clear()
    completed.complete_recipes.clear()
    completed.recipe_options[0].warnings.append("Changed completed copy.")

    assert generating == generating_before
    assert list(completed.recipe_failures) == []
    assert completed.complete_recipes == {}
    assert generating.complete_recipes == {}
    assert generating.recipe_options[0].warnings == []
    assert recipe.option_id == "option-1"


def test_commit_requires_at_least_one_result_mapping_entry(
    options_session: Session,
) -> None:
    generating, _, _ = begin_recipe_generation(options_session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(generating, successes={}, failures={})

    assert_invalid_request(raised.value)
    assert raised.value.message == "Recipe results must not be empty."
    assert generating.complete_recipes == {}
    assert generating.recipe_failures == {}


@pytest.mark.parametrize(
    ("successes", "failures", "expected_message"),
    [
        (
            {"   ": complete_recipe("option-1")},
            {},
            "Recipe result IDs must not be blank.",
        ),
        (
            {"option-2": complete_recipe("option-1")},
            {},
            "Recipe result IDs must match their mapping keys.",
        ),
        (
            {"option-1": complete_recipe("option-1")},
            {" option-1 ": recipe_failure("option-1")},
            "Recipe successes and failures must be disjoint.",
        ),
    ],
)
def test_commit_rejects_malformed_result_mappings(
    options_session: Session,
    successes: dict[str, CompleteRecipe],
    failures: dict[str, RecipeFailure],
    expected_message: str,
) -> None:
    generating, _, _ = begin_recipe_generation(options_session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(generating, successes, failures)

    assert_invalid_request(raised.value)
    assert raised.value.message == expected_message
    assert generating.complete_recipes == {}
    assert generating.recipe_failures == {}


def test_commit_rejects_unknown_result_option_with_a_safe_not_found_error(
    options_session: Session,
) -> None:
    unknown_id = "unknown-private-result"
    generating, _, _ = begin_recipe_generation(options_session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={unknown_id: complete_recipe(unknown_id)},
            failures={},
        )

    error = raised.value
    assert error.code is ErrorCode.RESOURCE_NOT_FOUND
    assert error.message == "Unknown recipe option."
    assert unknown_id not in str(error)
    assert error.status_code == 404
    assert error.retryable is False
    assert error.details == {}
    assert error.session_id == "session-1"
    assert generating.complete_recipes == {}


def test_all_failed_raises_retryable_safe_model_error_without_partial_state(
    options_session: Session,
) -> None:
    generating, _, _ = begin_recipe_generation(
        options_session,
        ["option-1", "option-2"],
    )
    generating_before = generating.model_copy(deep=True)
    private_failure = "provider traceback with private payload"

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={},
            failures={
                "option-1": recipe_failure("option-1", message=private_failure),
                "option-2": recipe_failure("option-2"),
            },
        )

    error = raised.value
    assert error.code is ErrorCode.MODEL_OUTPUT_INVALID
    assert error.message == "Every selected recipe failed to generate."
    assert private_failure not in str(error)
    assert error.status_code == 502
    assert error.retryable is True
    assert error.details == {}
    assert error.session_id == "session-1"
    assert generating == generating_before


def test_new_success_replaces_prior_failure_for_the_same_option(
    options_session: Session,
) -> None:
    recipes_session = options_session.model_copy(
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": {
                "option-1": complete_recipe("option-1", total_minutes=25)
            },
            "recipe_failures": {"option-2": recipe_failure("option-2")},
        }
    )
    generating, _, _ = begin_recipe_generation(recipes_session, ["option-2"])
    replacement = complete_recipe(
        "option-2",
        name="Onion Soup",
        cuisine="French",
        total_minutes=45,
    )

    completed = commit_recipe_results(
        generating,
        successes={"option-2": replacement},
        failures={},
    )

    assert list(completed.complete_recipes) == ["option-1", "option-2"]
    assert completed.complete_recipes["option-2"].total_minutes == 45
    assert "option-2" not in completed.recipe_failures


def test_new_failure_replaces_prior_success_for_the_same_option(
    options_session: Session,
) -> None:
    recipes_session = options_session.model_copy(
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": {
                "option-1": complete_recipe("option-1", total_minutes=25),
                "option-2": complete_recipe(
                    "option-2",
                    name="Onion Soup",
                    cuisine="French",
                ),
            },
        }
    )
    generating, _, _ = begin_recipe_generation(
        recipes_session,
        ["option-2", "option-3"],
    )

    completed = commit_recipe_results(
        generating,
        successes={
            "option-3": complete_recipe("option-3", name="Spinach Rice"),
        },
        failures={"option-2": recipe_failure("option-2")},
    )

    assert list(completed.complete_recipes) == ["option-1", "option-3"]
    assert "option-2" not in completed.complete_recipes
    assert completed.recipe_failures == {"option-2": recipe_failure("option-2")}


def test_selecting_a_different_option_preserves_prior_partial_results(
    options_session: Session,
) -> None:
    recipes_session = options_session.model_copy(
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": {"option-1": complete_recipe("option-1")},
            "recipe_failures": {"option-2": recipe_failure("option-2")},
        }
    )
    generating, selected, previous_stage = begin_recipe_generation(
        recipes_session,
        ["option-3"],
    )

    completed = commit_recipe_results(
        generating,
        successes={
            "option-3": complete_recipe("option-3", name="Spinach Rice"),
        },
        failures={},
    )

    assert previous_stage is SessionStage.RECIPES_READY
    assert [option.id for option in selected] == ["option-3"]
    assert list(completed.complete_recipes) == ["option-1", "option-3"]
    assert list(completed.recipe_failures) == ["option-2"]


def test_commit_advances_timestamp(options_session: Session) -> None:
    generating, _, _ = begin_recipe_generation(options_session, ["option-1"])

    completed = commit_recipe_results(
        generating,
        successes={"option-1": complete_recipe("option-1")},
        failures={},
    )

    assert completed.updated_at > generating.updated_at


@pytest.mark.parametrize(
    ("prior_successes", "prior_failures", "expected_stage"),
    [
        ({}, {}, SessionStage.OPTIONS_READY),
        (
            {"option-1": complete_recipe("option-1")},
            {"option-2": recipe_failure("option-2")},
            SessionStage.RECIPES_READY,
        ),
    ],
)
def test_restore_uses_retained_pre_generation_result_state(
    options_session: Session,
    prior_successes: dict[str, CompleteRecipe],
    prior_failures: dict[str, RecipeFailure],
    expected_stage: SessionStage,
) -> None:
    prior = options_session.model_copy(
        update={
            "stage": expected_stage,
            "complete_recipes": prior_successes,
            "recipe_failures": prior_failures,
        }
    )
    generating, _, _ = begin_recipe_generation(prior, ["option-3"])
    generating_before = generating.model_copy(deep=True)

    restored = restore_after_recipe_failure(generating)

    assert restored.stage is expected_stage
    assert restored.complete_recipes == generating_before.complete_recipes
    assert restored.recipe_failures == generating_before.recipe_failures
    assert restored.recipe_options == generating_before.recipe_options
    assert restored.preferences == generating_before.preferences
    assert restored.complete_recipes is not generating.complete_recipes
    assert restored.recipe_failures is not generating.recipe_failures
    assert restored.recipe_options is not generating.recipe_options
    assert restored.preferences is not generating.preferences
    assert restored.updated_at > generating.updated_at
    assert generating == generating_before


@pytest.mark.parametrize(
    "stage",
    [stage for stage in SessionStage if stage is not SessionStage.GENERATING_RECIPES],
)
def test_commit_and_restore_require_generating_recipes_stage(
    options_session: Session,
    stage: SessionStage,
) -> None:
    session = options_session.model_copy(update={"stage": stage})

    with pytest.raises(AppError) as commit_error:
        commit_recipe_results(
            session,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
        )
    with pytest.raises(AppError) as restore_error:
        restore_after_recipe_failure(session)

    for error in (commit_error.value, restore_error.value):
        assert error.code is ErrorCode.INVALID_SESSION_TRANSITION
        assert error.status_code == 409
        assert error.retryable is False
        assert error.session_id == "session-1"
    assert session.stage is stage
