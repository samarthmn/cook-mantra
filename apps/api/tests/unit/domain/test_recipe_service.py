from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.errors import AppError, ErrorCode
from domain.images import DishPreview
from domain.recipe_options import Difficulty, RecipeOption, RecipePreferences
from domain.recipe_service import (
    RecipeGenerationContext,
    begin_recipe_generation,
    commit_recipe_results,
    recipe_preview_artifact_ids,
    restore_after_recipe_failure,
    superseded_recipe_preview_artifact_ids,
)
from domain.recipes import (
    CompleteRecipe,
    IngredientAvailability,
    RecipeFailure,
    RecipeIngredient,
    RecipeStep,
)
from domain.sessions import Session, SessionStage
from schemas.sessions import SessionResponse

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
    servings: int = 2,
    total_minutes: int = 30,
) -> CompleteRecipe:
    return CompleteRecipe(
        option_id=option_id,
        name=name,
        cuisine=cuisine,
        servings=servings,
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


def assert_invalid_transition(error: AppError) -> None:
    assert error.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert error.status_code == 409
    assert error.retryable is False
    assert error.details == {}
    assert error.session_id == "session-1"


def test_preview_artifact_sets_are_detached_deduplicated_and_reference_safe(
    options_session: Session,
) -> None:
    shared = complete_recipe("option-1").model_copy(
        update={"preview": DishPreview(artifact_id="preview-shared")}
    )
    removed = complete_recipe(
        "option-2",
        name="Onion Soup",
        cuisine="French",
    ).model_copy(update={"preview": DishPreview(artifact_id="preview-removed")})
    previous = options_session.model_copy(
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": {
                "option-1": shared,
                "option-2": removed,
                "option-3": complete_recipe(
                    "option-3",
                    name="Spinach Rice",
                ).model_copy(
                    update={"preview": DishPreview(artifact_id="preview-shared")}
                ),
            },
        }
    )
    committed = previous.model_copy(
        update={
            "complete_recipes": {
                "option-1": shared,
                "option-2": removed.model_copy(
                    update={"preview": DishPreview(artifact_id="preview-new")}
                ),
            }
        }
    )

    assert recipe_preview_artifact_ids(previous) == frozenset(
        {"preview-shared", "preview-removed"}
    )
    assert superseded_recipe_preview_artifact_ids(previous, committed) == frozenset(
        {"preview-removed"}
    )


def test_selection_resolves_detached_server_owned_options_in_request_order(
    options_session: Session,
) -> None:
    generating, selected, previous_stage, context = begin_recipe_generation(
        options_session,
        ["option-3", "option-1"],
    )

    assert generating.stage is SessionStage.GENERATING_RECIPES
    assert selected == [
        options_session.recipe_options[2],
        options_session.recipe_options[0],
    ]
    assert previous_stage is SessionStage.OPTIONS_READY
    assert context.previous_stage is previous_stage
    assert context.selected_option_ids == ("option-3", "option-1")
    assert selected[0] is not options_session.recipe_options[2]
    assert selected[0] is not generating.recipe_options[2]

    selected[0].used_ingredients.append("Changed selected copy.")
    generating.recipe_options[0].used_ingredients.append("Changed generating copy.")

    assert options_session.recipe_options[2].used_ingredients == ["Tomato"]
    assert options_session.recipe_options[0].used_ingredients == ["Tomato"]


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

    generating, _, previous_stage, context = begin_recipe_generation(
        recipes_session,
        ["option-3"],
    )

    assert previous_stage is SessionStage.RECIPES_READY
    assert context.previous_stage is previous_stage
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
    generating, _, _, _ = begin_recipe_generation(options_session, ["option-1"])

    assert generating.updated_at > options_session.updated_at
    assert options_session.updated_at == NOW
    assert options_session.stage is SessionStage.OPTIONS_READY


def test_begin_returns_immutable_bound_context_and_private_attempt_token(
    options_session: Session,
) -> None:
    generating, _, previous_stage, context = begin_recipe_generation(
        options_session,
        [" option-2 ", "option-1"],
    )

    assert context.selected_option_ids == ("option-2", "option-1")
    assert context.previous_stage is previous_stage
    assert context.session_id == "session-1"
    assert context.rollback_snapshot == options_session.model_dump_json()
    assert generating.recipe_generation_id == context.generation_id
    assert context.rollback_snapshot not in repr(context)
    assert (
        "recipe_generation_id"
        not in SessionResponse.model_validate(generating).model_dump()
    )

    with pytest.raises(ValidationError):
        context.previous_stage = SessionStage.RECIPES_READY


def test_begin_creates_a_unique_bound_token_for_each_attempt(
    options_session: Session,
) -> None:
    first, _, _, first_context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )
    second, _, _, second_context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )

    assert first_context.generation_id != second_context.generation_id
    assert first.recipe_generation_id == first_context.generation_id
    assert second.recipe_generation_id == second_context.generation_id


def test_begin_rejects_an_unchecked_string_stage(
    options_session: Session,
) -> None:
    unchecked = options_session.model_copy(update={"stage": "options_ready"})

    with pytest.raises(AppError) as raised:
        begin_recipe_generation(unchecked, ["option-1"])

    assert_invalid_transition(raised.value)


@pytest.mark.parametrize("invalid_timestamp", [datetime(2026, 7, 30), "invalid"])
def test_begin_rejects_invalid_updated_at_with_a_safe_error(
    options_session: Session,
    invalid_timestamp: object,
) -> None:
    unchecked = options_session.model_copy(update={"updated_at": invalid_timestamp})

    with pytest.raises(AppError) as raised:
        begin_recipe_generation(unchecked, ["option-1"])

    assert_invalid_request(raised.value)
    assert raised.value.message == "Session timestamp is invalid."


def test_partial_success_moves_session_to_recipes_ready(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1", "option-2"],
    )
    recipe = complete_recipe("option-1")
    failure = recipe_failure("option-2")

    completed = commit_recipe_results(
        generating,
        successes={"option-1": recipe},
        failures={"option-2": failure},
        context=context,
    )

    assert completed.stage is SessionStage.RECIPES_READY
    assert completed.complete_recipes == {"option-1": recipe}
    assert completed.recipe_failures == {"option-2": failure}
    assert completed.complete_recipes["option-1"] is not recipe
    assert completed.recipe_failures["option-2"] is not failure
    assert completed.recipe_generation_id is None


def test_commit_requires_every_and_only_selected_option_to_settle(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1", "option-2"],
    )

    with pytest.raises(AppError) as omitted:
        commit_recipe_results(
            generating,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=context,
        )

    assert_invalid_request(omitted.value)
    assert omitted.value.message == "Recipe results must match the selection."


def test_commit_rejects_a_stored_but_unselected_result(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )

    with pytest.raises(AppError) as unselected:
        commit_recipe_results(
            generating,
            successes={
                "option-2": complete_recipe(
                    "option-2",
                    name="Onion Soup",
                    cuisine="French",
                )
            },
            failures={},
            context=context,
        )

    assert_invalid_request(unselected.value)
    assert unselected.value.message == "Recipe results must match the selection."


def test_commit_rejects_an_extra_result_beyond_the_selection(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )

    with pytest.raises(AppError) as extra:
        commit_recipe_results(
            generating,
            successes={
                "option-1": complete_recipe("option-1"),
                "option-2": complete_recipe(
                    "option-2",
                    name="Onion Soup",
                    cuisine="French",
                ),
            },
            failures={},
            context=context,
        )

    assert_invalid_request(extra.value)
    assert extra.value.message == "Recipe results must match the selection."


def test_commit_accepts_task_two_normalized_stored_recipe_identity(
    options_session: Session,
) -> None:
    padded_option = stored_option(
        "option-1",
        " \tTomato Curry\n ",
        cuisine="\n Indian \t",
    )
    session = options_session.model_copy(update={"recipe_options": [padded_option]})
    generating, _, _, context = begin_recipe_generation(session, ["option-1"])
    recipe = complete_recipe(
        "option-1",
        name="Tomato Curry",
        cuisine="Indian",
    )

    completed = commit_recipe_results(
        generating,
        successes={"option-1": recipe},
        failures={},
        context=context,
    )

    assert completed.complete_recipes == {"option-1": recipe}


@pytest.mark.parametrize(
    "recipe",
    [
        complete_recipe("option-1", name="Different Dish"),
        complete_recipe("option-1", name="tomato curry"),
        complete_recipe("option-1", name="Tomato  Curry"),
        complete_recipe("option-1", cuisine="French"),
        complete_recipe("option-1", cuisine="indian"),
        complete_recipe("option-1", servings=3),
    ],
)
def test_commit_rejects_mismatched_server_owned_recipe_identity(
    options_session: Session,
    recipe: CompleteRecipe,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={"option-1": recipe},
            failures={},
            context=context,
        )

    assert_invalid_request(raised.value)
    assert raised.value.message == "Complete recipe identity is invalid."


@pytest.mark.parametrize("field_name", ["name", "cuisine"])
def test_commit_rejects_unchecked_blank_stored_recipe_identity(
    options_session: Session,
    field_name: str,
) -> None:
    valid_option = options_session.recipe_options[0]
    constructed = RecipeOption.model_construct(
        **{
            **valid_option.__dict__,
            field_name: " \t\n ",
        }
    )
    session = options_session.model_copy(update={"recipe_options": [constructed]})
    generating, _, _, context = begin_recipe_generation(session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=context,
        )

    assert_invalid_request(raised.value)
    assert raised.value.message == "Complete recipe identity is invalid."


def test_commit_revalidates_an_unchecked_constructed_recipe(
    options_session: Session,
) -> None:
    valid = complete_recipe("option-1")
    constructed = CompleteRecipe.model_construct(
        **{
            **valid.__dict__,
            "name": "   ",
        }
    )
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={"option-1": constructed},
            failures={},
            context=context,
        )

    assert_invalid_request(raised.value)
    assert raised.value.message == "Recipe result values are invalid."


def test_commit_orders_results_by_stored_option_order(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
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
        context=context,
    )

    assert list(completed.complete_recipes) == ["option-1", "option-3"]
    assert list(completed.recipe_failures) == ["option-2"]


def test_commit_does_not_mutate_or_alias_inputs(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(options_session, ["option-1"])
    recipe = complete_recipe("option-1")
    successes = {"option-1": recipe}
    failures: dict[str, RecipeFailure] = {}
    generating_before = generating.model_copy(deep=True)

    completed = commit_recipe_results(generating, successes, failures, context)
    successes.clear()
    completed.complete_recipes.clear()
    completed.recipe_options[0].used_ingredients.append("Changed completed copy.")

    assert generating == generating_before
    assert list(completed.recipe_failures) == []
    assert completed.complete_recipes == {}
    assert generating.complete_recipes == {}
    assert generating.recipe_options[0].used_ingredients == ["Tomato"]
    assert recipe.option_id == "option-1"


def test_commit_requires_at_least_one_result_mapping_entry(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(options_session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={},
            failures={},
            context=context,
        )

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
    generating, _, _, context = begin_recipe_generation(options_session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(generating, successes, failures, context)

    assert_invalid_request(raised.value)
    assert raised.value.message == expected_message
    assert generating.complete_recipes == {}
    assert generating.recipe_failures == {}


def test_commit_rejects_unknown_result_option_with_a_safe_not_found_error(
    options_session: Session,
) -> None:
    unknown_id = "unknown-private-result"
    generating, _, _, context = begin_recipe_generation(options_session, ["option-1"])

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={unknown_id: complete_recipe(unknown_id)},
            failures={},
            context=context,
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
    generating, _, _, context = begin_recipe_generation(
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
            context=context,
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
    assert generating.recipe_generation_id == context.generation_id


def test_commit_rejects_a_stale_attempt_context(
    options_session: Session,
) -> None:
    _, _, _, stale_context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )
    generating, _, _, _ = begin_recipe_generation(
        options_session,
        ["option-1"],
    )

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=stale_context,
        )

    assert_invalid_transition(raised.value)
    assert raised.value.message == "Recipe generation context is invalid."


@pytest.mark.parametrize(
    "context_update",
    [
        {"generation_id": "forged-token"},
        {"session_id": "different-session"},
        {"previous_stage": SessionStage.RECIPES_READY},
        {"selected_option_ids": ("option-2",)},
        {"rollback_snapshot": "{}"},
    ],
)
def test_commit_rejects_forged_or_mismatched_context_fields(
    options_session: Session,
    context_update: dict[str, object],
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )
    forged = context.model_copy(update=context_update)

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=forged,
        )

    assert_invalid_transition(raised.value)
    assert raised.value.message == "Recipe generation context is invalid."


def test_commit_revalidates_a_constructed_context(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )
    constructed = RecipeGenerationContext.model_construct(
        **{
            **context.__dict__,
            "selected_option_ids": ("   ",),
        }
    )

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            generating,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=constructed,
        )

    assert_invalid_transition(raised.value)


def test_commit_rejects_a_mutated_generating_session_timestamp_safely(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )
    unchecked = generating.model_copy(update={"updated_at": datetime(2026, 7, 30)})

    with pytest.raises(AppError) as raised:
        commit_recipe_results(
            unchecked,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=context,
        )

    assert_invalid_request(raised.value)
    assert raised.value.message == "Session timestamp is invalid."


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
    generating, _, _, context = begin_recipe_generation(recipes_session, ["option-2"])
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
        context=context,
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
    generating, _, _, context = begin_recipe_generation(
        recipes_session,
        ["option-2", "option-3"],
    )

    completed = commit_recipe_results(
        generating,
        successes={
            "option-3": complete_recipe("option-3", name="Spinach Rice"),
        },
        failures={"option-2": recipe_failure("option-2")},
        context=context,
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
    generating, selected, previous_stage, context = begin_recipe_generation(
        recipes_session,
        ["option-3"],
    )

    completed = commit_recipe_results(
        generating,
        successes={
            "option-3": complete_recipe("option-3", name="Spinach Rice"),
        },
        failures={},
        context=context,
    )

    assert previous_stage is SessionStage.RECIPES_READY
    assert [option.id for option in selected] == ["option-3"]
    assert list(completed.complete_recipes) == ["option-1", "option-3"]
    assert list(completed.recipe_failures) == ["option-2"]


def test_commit_advances_timestamp(options_session: Session) -> None:
    generating, _, _, context = begin_recipe_generation(options_session, ["option-1"])

    completed = commit_recipe_results(
        generating,
        successes={"option-1": complete_recipe("option-1")},
        failures={},
        context=context,
    )

    assert completed.updated_at > generating.updated_at


def test_commit_preserves_orphaned_prior_result_keys_exactly(
    options_session: Session,
) -> None:
    orphan_recipe = complete_recipe(
        "orphan-success",
        name="Historic Dish",
        cuisine="Historic",
    )
    orphan_failure = recipe_failure("orphan-failure")
    prior = options_session.model_copy(
        update={
            "stage": SessionStage.RECIPES_READY,
            "complete_recipes": {"orphan-success": orphan_recipe},
            "recipe_failures": {"orphan-failure": orphan_failure},
        }
    )
    generating, _, _, context = begin_recipe_generation(prior, ["option-1"])

    completed = commit_recipe_results(
        generating,
        successes={"option-1": complete_recipe("option-1")},
        failures={},
        context=context,
    )

    assert completed.complete_recipes["orphan-success"] == orphan_recipe
    assert completed.recipe_failures["orphan-failure"] == orphan_failure
    assert list(completed.complete_recipes) == ["option-1", "orphan-success"]
    assert list(completed.recipe_failures) == ["orphan-failure"]


@pytest.mark.parametrize(
    ("stage", "prior_successes", "prior_failures"),
    [
        (
            SessionStage.OPTIONS_READY,
            {"option-1": complete_recipe("option-1")},
            {"orphan-failure": recipe_failure("orphan-failure")},
        ),
        (SessionStage.RECIPES_READY, {}, {}),
    ],
)
def test_restore_reconstructs_the_exact_unusual_ready_snapshot(
    options_session: Session,
    stage: SessionStage,
    prior_successes: dict[str, CompleteRecipe],
    prior_failures: dict[str, RecipeFailure],
) -> None:
    prior = options_session.model_copy(
        update={
            "stage": stage,
            "complete_recipes": prior_successes,
            "recipe_failures": prior_failures,
        }
    )
    generating, _, _, context = begin_recipe_generation(prior, ["option-3"])
    generating.recipe_options[0].used_ingredients.append("Mutated after begin.")
    generating.preferences.preferred_cuisines.append("Mutated after begin")
    generating.complete_recipes.clear()
    generating.recipe_failures["option-3"] = recipe_failure("option-3")
    generating.warnings.append("Mutated after begin.")

    restored = restore_after_recipe_failure(generating, context)

    assert restored == prior
    assert restored.stage is stage
    assert restored.updated_at == NOW
    assert restored.recipe_generation_id is None
    assert restored.complete_recipes is not generating.complete_recipes
    assert restored.recipe_failures is not generating.recipe_failures
    assert restored.recipe_options is not generating.recipe_options
    assert restored.preferences is not generating.preferences


def test_restore_rejects_a_context_with_a_different_bound_snapshot(
    options_session: Session,
) -> None:
    generating, _, _, context = begin_recipe_generation(
        options_session,
        ["option-1"],
    )
    different_snapshot = options_session.model_copy(
        update={"warnings": ["Forged rollback state."]}
    ).model_dump_json()
    forged = context.model_copy(update={"rollback_snapshot": different_snapshot})

    with pytest.raises(AppError) as raised:
        restore_after_recipe_failure(generating, forged)

    assert_invalid_transition(raised.value)
    assert raised.value.message == "Recipe generation context is invalid."


@pytest.mark.parametrize(
    "stage",
    [stage for stage in SessionStage if stage is not SessionStage.GENERATING_RECIPES],
)
def test_commit_and_restore_require_generating_recipes_stage(
    options_session: Session,
    stage: SessionStage,
) -> None:
    _, _, _, context = begin_recipe_generation(options_session, ["option-1"])
    session = options_session.model_copy(update={"stage": stage})

    with pytest.raises(AppError) as commit_error:
        commit_recipe_results(
            session,
            successes={"option-1": complete_recipe("option-1")},
            failures={},
            context=context,
        )
    with pytest.raises(AppError) as restore_error:
        restore_after_recipe_failure(session, context)

    for error in (commit_error.value, restore_error.value):
        assert error.code is ErrorCode.INVALID_SESSION_TRANSITION
        assert error.status_code == 409
        assert error.retryable is False
        assert error.session_id == "session-1"
    assert session.stage is stage
