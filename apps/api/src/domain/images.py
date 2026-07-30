"""Domain models for generated dish preview images."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

ILLUSTRATION_LABEL = "AI-generated illustration"


class ImageGenerationRequest(BaseModel):
    """A bounded request for a generated dish image."""

    prompt: str = Field(min_length=1, max_length=2_000)
    width: int = Field(default=768, ge=256, le=2_048)
    height: int = Field(default=768, ge=256, le=2_048)
    steps: int | None = Field(default=None, ge=1, le=100)


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """Verified image bytes returned by an image generator."""

    data: bytes
    media_type: str
    width: int
    height: int


class DishPreview(BaseModel):
    """A stored reference to a generated dish illustration."""

    artifact_id: str
    label: str = ILLUSTRATION_LABEL

    @field_validator("label", mode="before")
    @classmethod
    def set_illustration_label(cls, _: object) -> str:
        """Keep the public label fixed regardless of model or caller input."""
        return ILLUSTRATION_LABEL
