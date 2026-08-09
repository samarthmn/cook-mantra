"""Domain models for generated recipe suggestions."""

from collections.abc import Sequence
from enum import StrEnum
from unicodedata import normalize as normalize_unicode
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

NUTRITION_DISCLAIMER = "Estimated values; not medical advice."
_SPICE_LEVELS = frozenset({"mild", "medium", "hot", "extra-hot"})


def _normalize_preference_values(values: list[str]) -> list[str]:
    """Trim preference values and reject case-insensitive duplicates."""
    normalized_values: list[str] = []
    seen_values: set[str] = set()
    for value in values:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Preference values must not be blank.")
        normalized_key = normalized_value.casefold()
        if normalized_key in seen_values:
            raise ValueError("Preference values must be unique.")
        seen_values.add(normalized_key)
        normalized_values.append(normalized_value)
    return normalized_values


class Difficulty(StrEnum):
    """The estimated effort needed to prepare a recipe."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class RecipePreferences(BaseModel):
    """Optional constraints for a batch of recipe suggestions."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "dietary_preferences": ["vegetarian"],
                    "allergens": ["peanut"],
                    "preferred_cuisines": ["Italian"],
                    "spice_level": "medium",
                    "special_instructions": "Use less oil and add extra vegetables.",
                    "max_total_minutes": 45,
                    "servings": 2,
                    "option_count": 4,
                }
            ]
        },
    )

    dietary_preferences: list[str] = Field(default_factory=list, max_length=20)
    allergens: list[str] = Field(default_factory=list, max_length=20)
    preferred_cuisines: list[str] = Field(default_factory=list, max_length=20)
    spice_level: str | None = None
    special_instructions: str = Field(default="", max_length=500)
    max_total_minutes: int | None = Field(default=None, ge=1, le=1_440)
    servings: int = Field(default=2, ge=1, le=12)
    option_count: int = Field(default=4, ge=1, le=6)

    @field_validator(
        "dietary_preferences", "allergens", "preferred_cuisines", mode="after"
    )
    @classmethod
    def normalize_preference_values(cls, values: list[str]) -> list[str]:
        """Keep each preference list meaningful and unambiguous."""
        return _normalize_preference_values(values)

    @field_validator("spice_level", mode="before")
    @classmethod
    def normalize_spice_level(cls, value: object) -> str | None:
        """Canonicalize supported spice levels while treating blanks as unset."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Spice level must be a string or null.")
        normalized_value = value.strip().casefold()
        if not normalized_value:
            return None
        if normalized_value not in _SPICE_LEVELS:
            raise ValueError("Spice level must be mild, medium, hot, or extra-hot.")
        return normalized_value

    @field_validator("special_instructions", mode="before")
    @classmethod
    def normalize_special_instructions(cls, value: object) -> object:
        """Keep user cooking notes compact and on one predictable line."""
        if isinstance(value, str):
            return " ".join(value.split())
        return value


class NutritionEstimate(BaseModel):
    """Estimated nutrition information for a recipe option."""

    calories_kcal: int = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbohydrates_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    diet_tags: list[str] = Field(default_factory=list)
    allergen_warnings: list[str] = Field(default_factory=list)
    disclaimer: str = NUTRITION_DISCLAIMER

    @field_validator("disclaimer")
    @classmethod
    def validate_disclaimer(cls, disclaimer: str) -> str:
        """Keep the nutrition notice explicitly non-medical."""
        if disclaimer != NUTRITION_DISCLAIMER:
            raise ValueError(f"Disclaimer must be {NUTRITION_DISCLAIMER!r}")
        return disclaimer


class IngredientRequirement(BaseModel):
    """An ingredient that is missing from or optional for a recipe."""

    name: str
    reason: str
    substitution: str | None = None


class RecipeOptionDraft(BaseModel):
    """A recipe suggestion before optional enrichment has completed."""

    name: str
    summary: str
    cuisine: str
    total_minutes: int = Field(ge=1, le=1_440)
    difficulty: Difficulty
    used_ingredients: list[str] = Field(min_length=1)
    missing_ingredients: list[IngredientRequirement] = Field(default_factory=list)
    optional_ingredients: list[IngredientRequirement] = Field(default_factory=list)


def _ingredient_key(name: str) -> str:
    """Compare ingredient names after NFC, whitespace, and case normalization.

    This is the same convention the specialized recipe agent uses for its
    availability boundary. Comparing raw strings instead would reject honest
    model output over casing ("onion" for a confirmed "Onion"), which is not
    the hallucination this boundary exists to stop.
    """
    return " ".join(normalize_unicode("NFC", name).split()).casefold()


def validate_unique_option_ingredient_names(
    options: Sequence[RecipeOptionDraft],
) -> None:
    """Reject repeated normalized names within each recipe option."""
    for option in options:
        names = [
            *option.used_ingredients,
            *(requirement.name for requirement in option.missing_ingredients),
            *(requirement.name for requirement in option.optional_ingredients),
        ]
        seen_names: set[str] = set()
        for name in names:
            normalized_name = _ingredient_key(name)
            if normalized_name in seen_names:
                raise ValueError(
                    "Ingredient names must be unique within a recipe option."
                )
            seen_names.add(normalized_name)


def validate_confirmed_used_ingredients(
    options: Sequence[RecipeOptionDraft],
    confirmed_names: Sequence[str],
) -> None:
    """Require every used ingredient to match a confirmed name."""
    confirmed = {_ingredient_key(name) for name in confirmed_names}
    if any(
        _ingredient_key(ingredient) not in confirmed
        for option in options
        for ingredient in option.used_ingredients
    ):
        raise ValueError("used_ingredients must come from the confirmed ingredients.")


def canonicalize_used_ingredients(
    options: Sequence[RecipeOptionDraft],
    confirmed_names: Sequence[str],
) -> list[RecipeOptionDraft]:
    """Rewrite used ingredients to the user's confirmed spelling.

    The model may echo a confirmed name with different casing or spacing.
    Anything that fails to match a confirmed name even after normalization is
    a genuinely unconfirmed ingredient and is rejected.
    """
    canonical_by_key = {_ingredient_key(name): name for name in confirmed_names}
    canonicalized: list[RecipeOptionDraft] = []
    for option in options:
        used: list[str] = []
        for ingredient in option.used_ingredients:
            canonical = canonical_by_key.get(_ingredient_key(ingredient))
            if canonical is None:
                raise ValueError(
                    "used_ingredients must come from the confirmed ingredients."
                )
            used.append(canonical)
        canonicalized.append(option.model_copy(update={"used_ingredients": used}))
    return canonicalized


UNCONFIRMED_INGREDIENT_REASON = "Not on your confirmed ingredient list."


def reconcile_used_ingredients(
    options: Sequence[RecipeOptionDraft],
    confirmed_names: Sequence[str],
) -> list[RecipeOptionDraft]:
    """Enforce the confirmation boundary without rejecting honest output.

    Used ingredients that match a confirmed name (after normalization) are
    rewritten to the user's own spelling. Anything else — a paraphrase the
    model invented or a genuinely new ingredient — is demoted to
    missing_ingredients rather than shown as available. Rejecting the whole
    batch here would turn a single spelling variant ("chili" for a confirmed
    "chilli") into a hard model_output_invalid failure.

    Duplicate normalized names across used, missing, and optional lists are
    collapsed the same way: used wins, then the first missing entry, then the
    first optional entry. Models often echo a seasoning in both used and
    missing; that must not fail structured-output parsing.
    """
    canonical_by_key = {_ingredient_key(name): name for name in confirmed_names}
    reconciled: list[RecipeOptionDraft] = []
    for option in options:
        used: list[str] = []
        used_keys: set[str] = set()
        already_listed = {
            _ingredient_key(requirement.name)
            for requirement in (
                *option.missing_ingredients,
                *option.optional_ingredients,
            )
        }
        demoted: list[IngredientRequirement] = []
        for ingredient in option.used_ingredients:
            key = _ingredient_key(ingredient)
            canonical = canonical_by_key.get(key)
            if canonical is not None:
                if key not in used_keys:
                    used_keys.add(key)
                    used.append(canonical)
            elif key not in used_keys and key not in already_listed:
                already_listed.add(key)
                demoted.append(
                    IngredientRequirement(
                        name=ingredient,
                        reason=UNCONFIRMED_INGREDIENT_REASON,
                    )
                )
        if not used:
            raise ValueError("A generated option uses no confirmed ingredient at all.")

        claimed_keys = set(used_keys)
        missing = _unique_requirements(option.missing_ingredients, claimed_keys)
        missing.extend(demoted)
        claimed_keys.update(_ingredient_key(item.name) for item in demoted)
        optional = _unique_requirements(option.optional_ingredients, claimed_keys)

        reconciled.append(
            option.model_copy(
                update={
                    "used_ingredients": used,
                    "missing_ingredients": missing,
                    "optional_ingredients": optional,
                }
            )
        )
    return reconciled


def _unique_requirements(
    requirements: Sequence[IngredientRequirement],
    claimed_keys: set[str],
) -> list[IngredientRequirement]:
    """Keep the first requirement whose normalized name is not yet claimed."""
    unique: list[IngredientRequirement] = []
    for requirement in requirements:
        key = _ingredient_key(requirement.name)
        if key in claimed_keys:
            continue
        claimed_keys.add(key)
        unique.append(requirement)
    return unique


class RecipeOptionBatch(BaseModel):
    """A bounded non-empty collection of generated recipe drafts."""

    options: list[RecipeOptionDraft] = Field(min_length=1, max_length=6)

    @field_validator("options")
    @classmethod
    def reject_blank_generated_text(
        cls,
        options: list[RecipeOptionDraft],
    ) -> list[RecipeOptionDraft]:
        """Reject unusable text at the model-output boundary."""
        for option in options:
            if any(not value.strip() for value in (option.name, option.cuisine)):
                raise ValueError("Generated recipe text must not be blank.")
            if any(not value.strip() for value in option.used_ingredients):
                raise ValueError("Used ingredients must not contain blank values.")
            # A blank summary is prose the model skipped, not a broken dish. It
            # happens most on short ingredient lists, exactly when the user can
            # least afford to lose the whole batch. Normalize it and let the UI
            # omit the line rather than fail four usable options over one
            # missing sentence.
            if not option.summary.strip():
                option.summary = ""
        # Uniqueness is enforced after parsing via reconcile_used_ingredients.
        # Rejecting duplicates here turns honest model echo (Salt listed in both
        # used and missing) into a hard model_output_invalid failure.
        return options


class RecipeOption(RecipeOptionDraft):
    """A stored recipe suggestion with deprecated nullable nutrition."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    nutrition: NutritionEstimate | None = None

    @field_validator("nutrition", mode="before")
    @classmethod
    def keep_nutrition_dormant(cls, _value: object) -> None:
        """Retain the response field while disabling nutrition computation."""
        return None
