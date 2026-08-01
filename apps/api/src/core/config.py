"""Application and external model configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
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
    NUTRITION = "nutrition"
    SPECIALIZED_RECIPE = "specialized_recipe"


# Ingredient extraction needs vision; the specialized recipe agents run in
# parallel, so they use a smaller model to stay concurrent.
AGENT_MODELS: dict[Agent, Model] = {
    Agent.INGREDIENT_EXTRACTION: Model.QWEN_SMALL,
    Agent.MASTER_CHEF: Model.GPT_OSS,
    Agent.NUTRITION: Model.GPT_OSS,
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

IMAGE_WIDTH = 768
IMAGE_HEIGHT = 768
IMAGE_STEPS: int | None = None  # None defers to the image model's own default.
IMAGE_TIMEOUT_SECONDS = 600.0
BEAST_IMAGE_MODEL = "z-image-turbo"
BEAST_POLL_INTERVAL_SECONDS = 2.0

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
    # Where the text agents run. Required because there is no sane default.
    ollama_base_url: AnyHttpUrl
    # Dish previews use the separate Beast API and remain opt-in.
    beast_base_url: AnyHttpUrl | None = None
    beast_api_key: SecretStr | None = None
    dish_previews_enabled: bool = False
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
    image_width: int = Field(default=IMAGE_WIDTH, ge=256, le=2_048)
    image_height: int = Field(default=IMAGE_HEIGHT, ge=256, le=2_048)
    image_steps: int | None = Field(default=IMAGE_STEPS, ge=1, le=100)
    image_timeout_seconds: float = Field(default=IMAGE_TIMEOUT_SECONDS, gt=0)
    beast_image_model: str = BEAST_IMAGE_MODEL
    beast_poll_interval_seconds: float = Field(
        default=BEAST_POLL_INTERVAL_SECONDS,
        gt=0,
    )
    max_upload_bytes: int = Field(
        default=MAX_UPLOAD_BYTES,
        gt=0,
        le=MAX_UPLOAD_BYTES,
    )
    session_ttl_seconds: int = SESSION_TTL_SECONDS

    def model_for(self, agent: Agent) -> Model:
        """Return the Ollama model configured for an agent."""
        return AGENT_MODELS[agent]

    @model_validator(mode="after")
    def validate_beast_preview_configuration(self) -> "Settings":
        """Require the Beast endpoint and credential when previews are enabled."""
        if self.dish_previews_enabled and (
            self.beast_base_url is None or self.beast_api_key is None
        ):
            raise ValueError(
                "dish previews require both beast_base_url and beast_api_key"
            )
        return self

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
