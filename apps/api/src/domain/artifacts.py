"""Domain models for temporary runtime artifacts."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class ArtifactKind(StrEnum):
    """Purpose attached immutably to every runtime artifact."""

    INGREDIENT_UPLOAD = "ingredient_upload"
    DISH_PREVIEW = "dish_preview"


class Artifact(BaseModel):
    """Metadata for an artifact owned by the current API runtime."""

    id: str
    path: Path
    media_type: str
    owner_session_id: str
    kind: ArtifactKind = Field(frozen=True)
    created_at: datetime
    last_accessed_at: datetime
