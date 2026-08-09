"""Strict, process-lifetime local model runtime YAML configuration."""

import os
import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from core.config import PROJECT_ROOT
from domain.model_runtime import (
    AgentRole,
    Capability,
    ImageTuning,
    ProviderName,
    TextTuning,
)

DEFAULT_RUNTIME_CONFIG_PATH = PROJECT_ROOT / "config" / "cook-mantra.yaml"
CONFIG_PATH_ENV = "COOK_MANTRA_CONFIG_PATH"
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TEMPLATE_SYNTAX = re.compile(r"\{\{|\}\}|\$\{|<%|%>|\{%|%\}|\[\[|\]\]")


class ProviderConfiguration(BaseModel):
    """Connection metadata containing references to secrets, never secret values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: AnyHttpUrl | None = None
    command: tuple[str, ...] | None = Field(default=None, min_length=1)
    api_key_env: str | None = None
    allow_unverified_tool_boundary: bool = False

    @field_validator("command")
    @classmethod
    def validate_command(
        cls,
        command: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if command is not None:
            if any(not part.strip() for part in command):
                raise ValueError("command entries must not be blank")
            if (
                len(command) != 2
                or Path(command[0]).name != "codex"
                or command[1] != "app-server"
            ):
                raise ValueError("command must invoke only the Codex app server")
        return command

    @field_validator("endpoint")
    @classmethod
    def validate_secret_free_endpoint(
        cls,
        value: AnyHttpUrl | None,
    ) -> AnyHttpUrl | None:
        if value is not None and any(
            part is not None
            for part in (value.username, value.password, value.query, value.fragment)
        ):
            raise ValueError(
                "endpoint must not contain userinfo, query parameters, or fragments"
            )
        return value

    @field_validator("api_key_env")
    @classmethod
    def validate_secret_reference(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError("api_key_env must be an environment variable name")
        return value


class RoleConfiguration(BaseModel):
    """One provider/model selection and its static editable instruction layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    model: str = Field(min_length=1, max_length=300)
    enabled: bool = True
    provider_tag: str | None = None
    editable_instruction: str = Field(default="", max_length=4_000)
    capabilities: frozenset[Capability] | None = None
    text_tuning: TextTuning | None = None
    image_tuning: ImageTuning | None = None

    @field_validator("model", "editable_instruction")
    @classmethod
    def reject_template_syntax(cls, value: str) -> str:
        normalized = value.strip()
        if _TEMPLATE_SYNTAX.search(normalized):
            raise ValueError("placeholder/template syntax is not allowed")
        return normalized

    @field_validator("provider_tag")
    @classmethod
    def validate_provider_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not 1 <= len(normalized) <= 200:
            raise ValueError("provider_tag must contain between 1 and 200 characters")
        if _TEMPLATE_SYNTAX.search(normalized):
            raise ValueError("placeholder/template syntax is not allowed")
        if any(
            unicodedata.category(character).startswith("C") for character in normalized
        ):
            raise ValueError("provider_tag must not contain control characters")
        return normalized


class RuntimeConfiguration(BaseModel):
    """Complete validated local runtime configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    providers: dict[ProviderName, ProviderConfiguration]
    roles: dict[AgentRole, RoleConfiguration]
    source_path: Path


class RuntimeConfigurationSnapshot(BaseModel):
    """Safe startup result retained even when local YAML cannot be loaded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    config: RuntimeConfiguration | None = None
    diagnostics: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.config is not None


def _resolved_path(
    path: Path | str | None,
    environ: Mapping[str, str] | None,
) -> Path:
    if path is not None:
        selected = Path(path)
    else:
        selected = Path(
            (environ or os.environ).get(
                CONFIG_PATH_ENV,
                DEFAULT_RUNTIME_CONFIG_PATH,
            )
        )
    if not selected.is_absolute():
        selected = PROJECT_ROOT / selected
    return selected.resolve(strict=False)


def load_runtime_config(
    path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfiguration:
    """Load and validate the single local YAML file without resolving secrets."""
    source_path = _resolved_path(path, environ)
    with source_path.open("r", encoding="utf-8") as config_file:
        raw = yaml.safe_load(config_file)
    if not isinstance(raw, dict):
        raise ValueError("runtime configuration must be a YAML mapping")

    normalized = dict(raw)
    raw_roles = normalized.get("roles")
    if not isinstance(raw_roles, dict):
        raise ValueError("roles must be a YAML mapping")
    normalized_roles: dict[str, object] = {}
    for raw_role, raw_selection in raw_roles.items():
        role = AgentRole(raw_role)
        if not isinstance(raw_selection, dict):
            raise ValueError(f"{role.value} role selection must be a mapping")
        selection: dict[str, Any] = dict(raw_selection)
        tuning = selection.pop("tuning", {})
        tuning_field = (
            "image_tuning" if role is AgentRole.IMAGE_GENERATOR else "text_tuning"
        )
        selection[tuning_field] = tuning
        if role is not AgentRole.IMAGE_GENERATOR and "enabled" in selection:
            raise ValueError("enabled is only valid for the image_generator role")
        normalized_roles[role.value] = selection
    normalized["roles"] = normalized_roles
    normalized["source_path"] = source_path

    config = RuntimeConfiguration.model_validate(normalized)
    _validate_complete_configuration(config)
    return config


def _validate_complete_configuration(config: RuntimeConfiguration) -> None:
    missing_roles = [role.value for role in AgentRole if role not in config.roles]
    if missing_roles:
        raise ValueError(
            f"Missing required role selections: {', '.join(missing_roles)}"
        )

    for provider, definition in config.providers.items():
        if (
            definition.allow_unverified_tool_boundary
            and provider is not ProviderName.CODEX
        ):
            raise ValueError(
                "allow_unverified_tool_boundary is only valid for codex"
            )
        if provider in {ProviderName.OLLAMA, ProviderName.OPENROUTER}:
            if definition.endpoint is None or definition.command is not None:
                raise ValueError(
                    f"{provider.value} requires endpoint and forbids command"
                )
        elif definition.command is None or definition.endpoint is not None:
            raise ValueError("codex requires command and forbids endpoint")
        if provider is ProviderName.OPENROUTER and definition.api_key_env is None:
            raise ValueError("openrouter requires api_key_env")
        if (
            provider is not ProviderName.OPENROUTER
            and definition.api_key_env is not None
        ):
            raise ValueError(f"{provider.value} does not accept api_key_env")

    for role, selection in config.roles.items():
        if selection.provider not in config.providers:
            raise ValueError(
                f"{role.value} selects an unconfigured provider: "
                f"{selection.provider.value}"
            )
        if role is AgentRole.IMAGE_GENERATOR:
            if selection.text_tuning is not None or selection.image_tuning is None:
                raise ValueError("image_generator requires image tuning")
        elif selection.image_tuning is not None or selection.text_tuning is None:
            raise ValueError(f"{role.value} requires text tuning")
        if selection.provider_tag is not None and not (
            role is AgentRole.IMAGE_GENERATOR
            and selection.provider is ProviderName.OPENROUTER
        ):
            raise ValueError(
                "provider_tag is only valid for the OpenRouter image_generator role"
            )


def load_runtime_snapshot(
    path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfigurationSnapshot:
    """Capture malformed/incomplete YAML as a redacted startup diagnostic."""
    source_path = _resolved_path(path, environ)
    try:
        config = load_runtime_config(source_path, environ=environ)
    except Exception as error:
        return RuntimeConfigurationSnapshot(
            source_path=source_path,
            diagnostics=(
                f"Runtime configuration is unavailable ({type(error).__name__}).",
            ),
        )
    return RuntimeConfigurationSnapshot(source_path=source_path, config=config)


@lru_cache(maxsize=1)
def get_runtime_snapshot() -> RuntimeConfigurationSnapshot:
    """Return the immutable startup snapshot for this API process."""
    return load_runtime_snapshot()


def editable_instruction_for(role: AgentRole) -> str:
    """Return the process-owned static editable instruction for one role."""
    snapshot = get_runtime_snapshot()
    if snapshot.config is None:
        from core.errors import AppError, ErrorCode

        raise AppError(
            code=ErrorCode.MODEL_CONFIGURATION_INVALID,
            message="The model runtime configuration is invalid.",
            status_code=503,
            retryable=False,
            details={"diagnostics": list(snapshot.diagnostics)},
        )
    return snapshot.config.roles[role].editable_instruction
