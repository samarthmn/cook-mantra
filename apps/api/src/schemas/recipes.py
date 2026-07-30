"""Public API schemas for complete recipes."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.recipes import CompleteRecipe, RecipeFailure


def _normalize_option_id(option_id: str) -> str:
    normalized_id = option_id.strip()
    if not normalized_id:
        raise ValueError("Recipe option IDs must not be blank.")
    return normalized_id


class RecipeSelectionRequest(BaseModel):
    """A bounded selection of unique server-owned recipe options."""

    model_config = ConfigDict(extra="forbid")

    option_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("option_ids")
    @classmethod
    def normalize_unique_option_ids(cls, option_ids: list[str]) -> list[str]:
        """Normalize option IDs and reject duplicate selections."""
        normalized_ids = [_normalize_option_id(option_id) for option_id in option_ids]
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("Recipe option IDs must be unique.")
        return normalized_ids


class CompleteRecipeResponse(CompleteRecipe):
    """Public representation of a complete recipe."""

    model_config = ConfigDict(from_attributes=True)


class RecipeFailureResponse(RecipeFailure):
    """Public representation of one selected option failure."""

    model_config = ConfigDict(from_attributes=True)
