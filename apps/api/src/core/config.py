"""Application and Ollama model configuration."""

import os
from enum import StrEnum
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


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
        env_file=".local.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_timeout_seconds: float = os.getenv("LLM_TIMEOUT_SECONDS")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL")

    def model_for(self, agent: Agent) -> Model:
        """Return the Ollama model configured for an agent."""
        return AGENT_MODELS[agent]


@lru_cache
def get_settings() -> Settings:
    return Settings()
