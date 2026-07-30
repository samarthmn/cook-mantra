"""Public API schemas for cooking sessions."""

from datetime import datetime

from pydantic import BaseModel

from domain.sessions import SessionStage


class SessionResponse(BaseModel):
    """Initial public representation of a cooking session."""

    id: str
    stage: SessionStage
    created_at: datetime
    updated_at: datetime
