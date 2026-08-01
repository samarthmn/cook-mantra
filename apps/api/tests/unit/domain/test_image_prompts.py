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


def test_prompt_describes_a_vibrant_finished_dish_without_disallowed_content() -> None:
    option = recipe_option_draft()

    prompt = build_dish_prompt(option)

    assert "Tomato masala" in prompt
    assert "Indian" in prompt
    assert "Tomato, Onion, Cumin" in prompt
    normalized_prompt = prompt.lower()
    assert "vibrant color food photography" in normalized_prompt
    assert "finished, fully cooked and plated" in normalized_prompt
    assert "served ready to eat" in normalized_prompt
    assert "rich, appetizing natural colors" in normalized_prompt
    assert "warm natural light" in normalized_prompt
    assert "shallow depth of field" in normalized_prompt
    assert "no text" in normalized_prompt
    assert "no logos" in normalized_prompt
    assert "no people" in normalized_prompt
    assert "no raw ingredients" in normalized_prompt
    assert "no sketches or illustrations" in normalized_prompt
    assert "no black-and-white or monochrome rendering" in normalized_prompt
    assert "no utensils obscuring the dish" in normalized_prompt


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

    assert len(prompt) == 2_000
    assert prompt.startswith("Vibrant color food photography")
    assert prompt.count("…") == 3
    assert "finished, fully cooked and plated" in prompt.lower()
    assert "no text" in prompt.lower()
    assert "no people" in prompt.lower()
    assert "no raw ingredients" in prompt.lower()
    assert "no black-and-white or monochrome rendering" in prompt.lower()
    assert prompt.endswith("no utensils obscuring the dish.")
