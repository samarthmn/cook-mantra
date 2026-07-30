"""Public API schemas for cooking sessions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from domain.sessions import SessionStage
from schemas.ingredients import IngredientResponse


class SessionCreatedResponse(BaseModel):
    """Identifiers returned after ingredient extraction is queued."""

    session_id: str
    job_id: str


class SessionResponse(BaseModel):
    """Initial public representation of a cooking session."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    stage: SessionStage
    image_artifact_id: str | None
    ingredients: list[IngredientResponse]
    warnings: list[str]
    created_at: datetime
    updated_at: datetime
