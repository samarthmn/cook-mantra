"""Application and Ollama model configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Model(StrEnum):
    """Ollama models available to Cook Mantra agents."""

    QWEN_LARGE = "qwen3.5:27b"
    QWEN_SMALL = "qwen3.5:9b"
    GEMMA_LARGE = "gemma4:26b"
    GPT_OSS = "gpt-oss:20b"
    Z_IMAGE = "x/z-image-turbo:fp8"


class Agent(StrEnum):
    """The agents described in docs/prd.md."""

    INGREDIENT_EXTRACTION = "ingredient_extraction"
    MASTER_CHEF = "master_chef"
    NUTRITION = "nutrition"
    IMAGE = "image"
    SPECIALIZED_RECIPE = "specialized_recipe"


# Ingredient extraction needs vision; the specialized recipe agents run in
# parallel, so they use a smaller model to stay concurrent.
AGENT_MODELS: dict[Agent, Model] = {
    Agent.INGREDIENT_EXTRACTION: Model.QWEN_SMALL,
    Agent.MASTER_CHEF: Model.QWEN_LARGE,
    Agent.NUTRITION: Model.GPT_OSS,
    Agent.IMAGE: Model.Z_IMAGE,
    Agent.SPECIALIZED_RECIPE: Model.GEMMA_LARGE,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    ollama_base_url: AnyHttpUrl
    llm_timeout_seconds: float = 300.0
    max_concurrent_jobs: int = Field(default=2, gt=0)
    max_queued_jobs: int = Field(default=4, ge=0)
    max_concurrent_model_calls: int = Field(default=2, gt=0)
    cleanup_interval_seconds: int = Field(default=300, ge=10)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    image_width: int = Field(default=768, ge=256, le=2_048)
    image_height: int = Field(default=768, ge=256, le=2_048)
    image_steps: int | None = Field(default=None, ge=1, le=100)
    image_timeout_seconds: float = Field(default=600.0, gt=0)
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        le=10 * 1024 * 1024,
    )
    session_ttl_seconds: int = 21_600
    artifact_root: Path = PROJECT_ROOT / "tmp" / "cook-mantra-api"
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "cook-mantra"

    def model_for(self, agent: Agent) -> Model:
        """Return the Ollama model configured for an agent."""
        return AGENT_MODELS[agent]

    @field_validator("artifact_root")
    @classmethod
    def validate_artifact_root(cls, artifact_root: Path) -> Path:
        """Keep destructive artifact cleanup inside the project runtime namespace."""
        runtime_root = (PROJECT_ROOT / "tmp").resolve(strict=False)
        configured_root = (
            artifact_root
            if artifact_root.is_absolute()
            else PROJECT_ROOT / artifact_root
        ).resolve(strict=False)
        if not configured_root.is_relative_to(runtime_root):
            raise ValueError("artifact_root must be within PROJECT_ROOT/tmp")
        return configured_root


@lru_cache
def get_settings() -> Settings:
    return Settings()
