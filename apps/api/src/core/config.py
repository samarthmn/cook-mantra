"""Application and external model configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Model(StrEnum):
    """Ollama models available to Cook Mantra agents."""

    QWEN_LARGE = "qwen3.5:27b"
    QWEN_SMALL = "qwen3.5:9b"
    GEMMA_LARGE = "gemma4:26b"
    GPT_OSS = "gpt-oss:20b"


class Agent(StrEnum):
    """The agents described in docs/prd.md."""

    INGREDIENT_EXTRACTION = "ingredient_extraction"
    MASTER_CHEF = "master_chef"
    SPECIALIZED_RECIPE = "specialized_recipe"


# Ingredient extraction needs vision; the specialized recipe agents run in
# parallel, so they use a smaller model to stay concurrent.
AGENT_MODELS: dict[Agent, Model] = {
    Agent.INGREDIENT_EXTRACTION: Model.QWEN_SMALL,
    Agent.MASTER_CHEF: Model.GPT_OSS,
    Agent.SPECIALIZED_RECIPE: Model.GPT_OSS,
}

# --- Tuning constants -------------------------------------------------------
# These are product decisions, not per-machine facts, so they live here rather
# than in .env. Every one still reaches Settings as a field default, so a test
# can inject a different value and an operator can override it with the
# matching environment variable when a machine genuinely needs something else.

# Ollama's own default context window is 4096, which is too small to hold a
# prompt plus a full structured-output response: generation stops mid-JSON and
# the job fails with model_output_invalid.
LLM_NUM_CTX = 8_192
LLM_TIMEOUT_SECONDS = 300.0

# Text agents are slow, so a small queue keeps latency honest instead of
# accepting work the machine cannot start soon.
MAX_CONCURRENT_JOBS = 2
MAX_QUEUED_JOBS = 4
MAX_CONCURRENT_MODEL_CALLS = 2

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SESSION_TTL_SECONDS = 21_600
CLEANUP_INTERVAL_SECONDS = 300


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment-specific: these are the values a .env should carry -----
    openrouter_api_key: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    artifact_root: Path = PROJECT_ROOT / "tmp" / "cook-mantra-api"

    # --- LangSmith: opt-in --------------------------------------------------
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "cook-mantra"

    # --- Tuning: defaults come from the constants above --------------------
    llm_timeout_seconds: float = LLM_TIMEOUT_SECONDS
    llm_num_ctx: int = Field(default=LLM_NUM_CTX, ge=4_096, le=131_072)
    max_concurrent_jobs: int = Field(default=MAX_CONCURRENT_JOBS, gt=0)
    max_queued_jobs: int = Field(default=MAX_QUEUED_JOBS, ge=0)
    max_concurrent_model_calls: int = Field(default=MAX_CONCURRENT_MODEL_CALLS, gt=0)
    cleanup_interval_seconds: int = Field(default=CLEANUP_INTERVAL_SECONDS, ge=10)
    max_upload_bytes: int = Field(
        default=MAX_UPLOAD_BYTES,
        gt=0,
        le=MAX_UPLOAD_BYTES,
    )
    session_ttl_seconds: int = SESSION_TTL_SECONDS

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
