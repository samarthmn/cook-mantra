"""Public health and readiness response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Process-only health response."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: Literal["ok"]


class OllamaStatus(BaseModel):
    """Safe Ollama model inventory returned by readiness checks."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "reachable": True,
                    "available_models": ["qwen3.5:9b"],
                    "missing": [],
                }
            ]
        }
    )

    reachable: bool
    available_models: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class BeastStatus(BaseModel):
    """Safe Beast public-health result returned by readiness checks."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"reachable": True, "status": "degraded"}]}
    )

    reachable: bool
    status: str | None = None


class ReadinessResponse(BaseModel):
    """Readiness response for all current external dependencies."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "ollama": {
                        "reachable": True,
                        "available_models": ["qwen3.5:9b"],
                        "missing": [],
                    },
                    "beast": {"reachable": True, "status": "degraded"},
                }
            ]
        }
    )

    status: Literal["ok"]
    ollama: OllamaStatus
    beast: BeastStatus | None = None
