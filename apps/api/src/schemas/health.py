"""Public health and readiness response schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Process-only health response."""

    status: Literal["ok"]


class OllamaStatus(BaseModel):
    """Safe Ollama model inventory returned by readiness checks."""

    reachable: bool
    available_models: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ReadinessResponse(BaseModel):
    """Readiness response for all current external dependencies."""

    status: Literal["ok"]
    ollama: OllamaStatus
