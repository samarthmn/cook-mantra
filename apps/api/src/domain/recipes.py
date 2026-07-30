"""Domain models for complete, cookable recipes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.errors import ErrorCode

NUTRITION_NOTICE = "Estimated values; not medical advice."
ALLERGEN_NOTICE = "Check ingredient labels for allergens."


def _normalize_nonblank(value: str) -> str:
    """Trim a string while requiring visible content."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("String values must not be blank.")
    return normalized


class IngredientAvailability(StrEnum):
    """Whether an ingredient is confirmed, missing, or optional."""

    AVAILABLE = "available"
    MISSING = "missing"
    OPTIONAL = "optional"


class RecipeIngredient(BaseModel):
    """One measured ingredient in a complete recipe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    quantity: str
    availability: IngredientAvailability
    substitution: str | None = None

    @field_validator("name", "quantity")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Keep ingredient descriptions meaningful and normalized."""
        return _normalize_nonblank(value)

    @field_validator("substitution")
    @classmethod
    def normalize_substitution(cls, value: str | None) -> str | None:
        """Normalize a supplied substitution without requiring one."""
        return _normalize_nonblank(value) if value is not None else None


class RecipeStep(BaseModel):
    """One numbered cooking instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1, strict=True)
    instruction: str
    duration_minutes: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        """Keep the cooking instruction meaningful and normalized."""
        return _normalize_nonblank(value)


class RecipeFailure(BaseModel):
    """Safe per-option failure details retained in a session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str
    code: ErrorCode
    message: str
    retryable: bool = Field(strict=True)

    @field_validator("option_id", "message")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Keep failure identity and public detail meaningful."""
        return _normalize_nonblank(value)


class CompleteRecipe(BaseModel):
    """A validated recipe ready for a user to cook."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str
    name: str
    cuisine: str
    servings: int = Field(ge=1, le=12, strict=True)
    total_minutes: int = Field(ge=1, le=1_440, strict=True)
    ingredients: tuple[RecipeIngredient, ...] = Field(min_length=1)
    steps: tuple[RecipeStep, ...] = Field(min_length=1)
    tips: tuple[str, ...] = ()
    substitutions: tuple[str, ...] = ()
    nutrition_notice: str = NUTRITION_NOTICE
    allergen_notice: str = ALLERGEN_NOTICE
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator(
        "option_id",
        "name",
        "cuisine",
        "nutrition_notice",
        "allergen_notice",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Keep scalar recipe text meaningful and normalized."""
        return _normalize_nonblank(value)

    @field_validator("tips", "substitutions", "assumptions", "warnings")
    @classmethod
    def normalize_text_collection(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize every optional recipe note."""
        return tuple(_normalize_nonblank(value) for value in values)

    @model_validator(mode="after")
    def validate_step_numbers(self) -> "CompleteRecipe":
        """Require cooking steps numbered exactly one through N."""
        expected_numbers = list(range(1, len(self.steps) + 1))
        if [step.number for step in self.steps] != expected_numbers:
            raise ValueError(
                "Recipe step numbers must be consecutive and start at one."
            )
        return self
