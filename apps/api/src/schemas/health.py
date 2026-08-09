"""Public health and readiness response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.model_runtime import ModelRuntimeReadiness


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


class ReadinessResponse(BaseModel):
    """Readiness response for all current external dependencies."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "model_runtime": {
                        "ready": True,
                        "roles": {
                            "ingredient_extractor": {
                                "role": "ingredient_extractor",
                                "provider": "ollama",
                                "model": "qwen3.5:9b",
                                "enabled": True,
                                "ready": True,
                                "required_capabilities": [
                                    "structured_output",
                                    "vision",
                                ],
                                "available_capabilities": [
                                    "structured_output",
                                    "vision",
                                ],
                            },
                            "master_chef": {
                                "role": "master_chef",
                                "provider": "ollama",
                                "model": "qwen3.5:9b",
                                "enabled": True,
                                "ready": True,
                                "required_capabilities": [
                                    "structured_output",
                                    "text",
                                ],
                                "available_capabilities": [
                                    "structured_output",
                                    "text",
                                ],
                            },
                            "recipe_writer": {
                                "role": "recipe_writer",
                                "provider": "openrouter",
                                "model": "openai/gpt-5.2",
                                "enabled": True,
                                "ready": True,
                                "required_capabilities": [
                                    "structured_output",
                                    "text",
                                ],
                                "available_capabilities": [
                                    "structured_output",
                                    "text",
                                ],
                            },
                            "image_generator": {
                                "role": "image_generator",
                                "provider": "openrouter",
                                "model": "google/gemini-2.5-flash-image",
                                "enabled": True,
                                "ready": True,
                                "required_capabilities": ["image_output"],
                                "available_capabilities": ["image_output"],
                            },
                        },
                    },
                    "ollama": {
                        "reachable": True,
                        "available_models": ["qwen3.5:9b"],
                        "missing": [],
                    },
                }
            ]
        }
    )

    status: Literal["ok"]
    model_runtime: ModelRuntimeReadiness
    ollama: OllamaStatus


class RuntimeStatusResponse(BaseModel):
    """Safe process-lifetime model runtime status for local clients."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "status": "attention",
                    "runtime_revision": ("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
                    "model_runtime": {
                        "ready": False,
                        "roles": {
                            "ingredient_extractor": {
                                "role": "ingredient_extractor",
                                "provider": "ollama",
                                "model": "qwen3.5:9b",
                                "enabled": True,
                                "ready": False,
                                "required_capabilities": [
                                    "structured_output",
                                    "vision",
                                ],
                                "available_capabilities": [],
                                "error": "unavailable",
                            },
                            "master_chef": {
                                "role": "master_chef",
                                "provider": "ollama",
                                "model": "qwen3.5:9b",
                                "enabled": True,
                                "ready": False,
                                "required_capabilities": [
                                    "structured_output",
                                    "text",
                                ],
                                "available_capabilities": [],
                                "error": "unavailable",
                            },
                            "recipe_writer": {
                                "role": "recipe_writer",
                                "provider": "openrouter",
                                "model": "openai/gpt-5.2",
                                "enabled": True,
                                "ready": False,
                                "required_capabilities": [
                                    "structured_output",
                                    "text",
                                ],
                                "available_capabilities": [],
                                "error": "unavailable",
                            },
                            "image_generator": {
                                "role": "image_generator",
                                "provider": "openrouter",
                                "model": "google/gemini-2.5-flash-image",
                                "enabled": True,
                                "ready": False,
                                "required_capabilities": ["image_output"],
                                "available_capabilities": [],
                                "error": "unavailable",
                            },
                        },
                    },
                }
            ]
        },
    )

    status: Literal["ok", "attention"]
    runtime_revision: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    model_runtime: ModelRuntimeReadiness
