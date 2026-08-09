"""Shared complete runtime fixtures for API integration tests."""

from core.runtime_config import RuntimeConfigurationSnapshot, get_runtime_snapshot
from domain.model_runtime import AgentRole

INSTALLED_REQUIRED_MODELS = ["qwen3.5:9b", "gpt-oss:20b"]


def image_enabled_runtime_snapshot() -> RuntimeConfigurationSnapshot:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    role = AgentRole.IMAGE_GENERATOR
    image_selection = config.roles[role].model_copy(update={"enabled": True})
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={"roles": {**config.roles, role: image_selection}}
            )
        }
    )
