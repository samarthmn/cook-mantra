"""Domain models for generated dish preview images."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.model_runtime import ImageTuning

PREVIEW_LABEL = "AI-generated image"


class ImageGenerationRequest(BaseModel):
    """A bounded request for a generated dish image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1, max_length=2_000, repr=False)
    tuning: ImageTuning


@dataclass(frozen=True, slots=True)
class VerifiedRaster:
    """Verified image bytes returned by an image generator."""

    data: bytes = field(repr=False)
    media_type: str
    width: int
    height: int


class DishPreview(BaseModel):
    """A stored reference to a generated dish image."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "artifact_id": "artifact-preview-123",
                    "label": PREVIEW_LABEL,
                }
            ]
        },
    )

    artifact_id: str
    label: Literal["AI-generated image"] = PREVIEW_LABEL

    @field_validator("label", mode="before")
    @classmethod
    def set_preview_label(cls, _: object) -> str:
        """Keep the public label fixed regardless of model or caller input."""
        return PREVIEW_LABEL
