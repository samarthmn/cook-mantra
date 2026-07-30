"""Domain models for temporary runtime artifacts."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class Artifact(BaseModel):
    """Metadata for an artifact owned by the current API runtime."""

    id: str
    path: Path
    media_type: str
    owner_session_id: str
    created_at: datetime
    last_accessed_at: datetime
