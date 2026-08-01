"""Deterministic prompts for generated dish preview images."""

from domain.recipe_options import RecipeOptionDraft

_MAX_PROMPT_LENGTH = 2_000
_INTRODUCTION = (
    "Vibrant color food photography of the finished, fully cooked and plated "
)
_NAME_TO_CUISINE = ", a "
_CUISINE_TO_INGREDIENTS = " dish, prepared with these main ingredients: "
_FINISH = (
    ". Served ready to eat with rich, appetizing natural colors, warm natural light, "
    "and shallow depth of field. No text, no logos, no people, no raw ingredients, "
    "no sketches or illustrations, no black-and-white or monochrome rendering, and "
    "no utensils obscuring the dish."
)


def build_dish_prompt(option: RecipeOptionDraft) -> str:
    """Build a concise, bounded food-photography instruction for one recipe."""
    name = _normalize(option.name)
    cuisine = _normalize(option.cuisine)
    ingredients = _normalize(
        ", ".join(_normalize(ingredient) for ingredient in option.used_ingredients)
    )
    content_budget = _MAX_PROMPT_LENGTH - len(
        _INTRODUCTION + _NAME_TO_CUISINE + _CUISINE_TO_INGREDIENTS + _FINISH
    )
    name_budget = content_budget // 3
    cuisine_budget = content_budget // 3
    ingredients_budget = content_budget - name_budget - cuisine_budget

    return (
        f"{_INTRODUCTION}{_truncate(name, name_budget)}{_NAME_TO_CUISINE}"
        f"{_truncate(cuisine, cuisine_budget)}{_CUISINE_TO_INGREDIENTS}"
        f"{_truncate(ingredients, ingredients_budget)}{_FINISH}"
    )


def _normalize(value: str) -> str:
    """Keep untrusted recipe text on one predictable prompt line."""
    return " ".join(value.split())


def _truncate(value: str, maximum_length: int) -> str:
    """Preserve a stable prefix while reserving the prompt safety instructions."""
    if len(value) <= maximum_length:
        return value
    if maximum_length == 0:
        return ""
    return f"{value[: maximum_length - 1].rstrip()}…"
