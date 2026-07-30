from domain.image_prompts import build_dish_prompt
from domain.recipe_options import Difficulty, RecipeOptionDraft


def recipe_option_draft(**updates: object) -> RecipeOptionDraft:
    values: dict[str, object] = {
        "name": "Tomato masala",
        "summary": "A quick tomato dish.",
        "cuisine": "Indian",
        "total_minutes": 20,
        "difficulty": Difficulty.EASY,
        "used_ingredients": ["Tomato", "Onion", "Cumin"],
    }
    values.update(updates)
    return RecipeOptionDraft(**values)


def test_prompt_describes_a_plated_dish_without_text_people_or_obscuring_utensils() -> (
    None
):
    option = recipe_option_draft()

    prompt = build_dish_prompt(option)

    assert "Tomato masala" in prompt
    assert "Indian" in prompt
    assert "Tomato, Onion, Cumin" in prompt
    assert "realistic food photography" in prompt.lower()
    assert "natural light" in prompt.lower()
    assert "plated serving" in prompt.lower()
    assert "no text" in prompt.lower()
    assert "no logos" in prompt.lower()
    assert "no people" in prompt.lower()
    assert "no utensils obscuring the dish" in prompt.lower()


def test_prompt_is_deterministic() -> None:
    option = recipe_option_draft()

    assert build_dish_prompt(option) == build_dish_prompt(option)


def test_prompt_stays_within_the_image_request_limit_for_long_valid_recipe_data() -> (
    None
):
    option = recipe_option_draft(
        name="N" * 5_000,
        cuisine="C" * 5_000,
        used_ingredients=["I" * 5_000 for _ in range(8)],
    )

    prompt = build_dish_prompt(option)

    assert len(prompt) <= 2_000
    assert "realistic food photography" in prompt.lower()
    assert "no text" in prompt.lower()
    assert "no people" in prompt.lower()
