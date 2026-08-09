from pathlib import Path

import pytest
from pydantic import ValidationError

from core.runtime_config import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    RoleConfiguration,
    get_runtime_snapshot,
    load_runtime_config,
    load_runtime_snapshot,
)
from domain.model_runtime import (
    AgentRole,
    Capability,
    ImageOutputProvider,
    ImageTuning,
    ModelUsage,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
    StructuredOutputProvider,
    TextProvider,
    VisionProvider,
)

VALID_CONFIG = """
version: 1
providers:
  ollama:
    endpoint: http://127.0.0.1:11434
  openrouter:
    endpoint: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
  codex:
    command: [codex, app-server]
roles:
  ingredient_extractor:
    provider: ollama
    model: qwen3.5:9b
    tuning:
      temperature: 0
      reasoning_effort: low
      context_window: 16384
    editable_instruction: Prefer short culinary names.
    capabilities: [vision, structured_output]
  master_chef:
    provider: ollama
    model: gpt-oss:20b
    tuning:
      temperature: 0
      max_output_tokens: 8192
    editable_instruction: Suggest practical dishes.
    capabilities: [text, structured_output]
  recipe_writer:
    provider: ollama
    model: gpt-oss:20b
    tuning: {}
    editable_instruction: Give clear cooking steps.
    capabilities: [text, structured_output]
  image_generator:
    enabled: false
    provider: ollama
    model: x/z-image-turbo:fp8
    tuning:
      width: 1024
      height: 1024
      output_format: webp
    editable_instruction: Create an appetizing cooked-dish preview.
    capabilities: [image_output]
"""


def _write_config(path: Path, content: str = VALID_CONFIG) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_runtime_domain_exposes_provider_neutral_types() -> None:
    assert set(ProviderName) == {
        ProviderName.OLLAMA,
        ProviderName.OPENROUTER,
        ProviderName.CODEX,
    }
    assert AgentRole.INGREDIENT_EXTRACTOR.required_capabilities == frozenset(
        {Capability.VISION, Capability.STRUCTURED_OUTPUT}
    )
    assert AgentRole.MASTER_CHEF.required_capabilities == frozenset(
        {Capability.TEXT, Capability.STRUCTURED_OUTPUT}
    )
    assert AgentRole.RECIPE_WRITER.required_capabilities == frozenset(
        {Capability.TEXT, Capability.STRUCTURED_OUTPUT}
    )
    assert AgentRole.IMAGE_GENERATOR.required_capabilities == frozenset(
        {Capability.IMAGE_OUTPUT}
    )
    assert ModelUsage(input_tokens=3, output_tokens=5).total_tokens == 8
    assert (
        ProviderError(
            kind=ProviderErrorKind.RATE_LIMITED,
            message="safe",
            retryable=True,
        ).kind
        is ProviderErrorKind.RATE_LIMITED
    )
    assert TextProvider.generate_text
    assert VisionProvider.analyze_image
    assert StructuredOutputProvider.generate_structured
    assert ImageOutputProvider.generate_image


def test_loader_accepts_all_local_providers_and_authoritative_capabilities(
    project_tmp_path: Path,
) -> None:
    config = load_runtime_config(_write_config(project_tmp_path / "runtime.yaml"))

    assert set(config.providers) == set(ProviderName)
    assert config.roles[AgentRole.INGREDIENT_EXTRACTOR].capabilities == frozenset(
        {Capability.VISION, Capability.STRUCTURED_OUTPUT}
    )
    assert config.roles[AgentRole.IMAGE_GENERATOR].enabled is False
    assert config.roles[AgentRole.MASTER_CHEF].text_tuning.max_output_tokens == 8192
    assert config.roles[AgentRole.IMAGE_GENERATOR].image_tuning.width == 1024
    assert config.roles[AgentRole.IMAGE_GENERATOR].provider_tag is None


def test_codex_tool_boundary_opt_in_defaults_off_and_can_be_enabled(
    project_tmp_path: Path,
) -> None:
    default_config = load_runtime_config(
        _write_config(project_tmp_path / "codex-default.yaml")
    )
    opted_in_config = load_runtime_config(
        _write_config(
            project_tmp_path / "codex-opt-in.yaml",
            VALID_CONFIG.replace(
                "    command: [codex, app-server]",
                "    command: [codex, app-server]\n"
                "    allow_unverified_tool_boundary: true",
                1,
            ),
        )
    )

    assert (
        default_config.providers[ProviderName.CODEX].allow_unverified_tool_boundary
        is False
    )
    assert (
        opted_in_config.providers[ProviderName.CODEX].allow_unverified_tool_boundary
        is True
    )


@pytest.mark.parametrize("provider", ["ollama", "openrouter"])
def test_tool_boundary_opt_in_is_rejected_outside_codex(
    project_tmp_path: Path,
    provider: str,
) -> None:
    marker = (
        "    endpoint: http://127.0.0.1:11434"
        if provider == "ollama"
        else "    api_key_env: OPENROUTER_API_KEY"
    )
    content = VALID_CONFIG.replace(
        marker,
        f"{marker}\n    allow_unverified_tool_boundary: true",
        1,
    )

    with pytest.raises(ValueError, match="only valid for codex"):
        load_runtime_config(
            _write_config(project_tmp_path / f"{provider}-opt-in.yaml", content)
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_loader_accepts_a_scoped_trimmed_openrouter_image_provider_tag(
    project_tmp_path: Path,
    enabled: bool,
) -> None:
    content = VALID_CONFIG.replace(
        "  image_generator:\n    enabled: false\n    provider: ollama",
        "  image_generator:\n"
        f"    enabled: {str(enabled).lower()}\n"
        "    provider: openrouter\n"
        '    provider_tag: "  upstream/exact  "',
        1,
    )

    config = load_runtime_config(
        _write_config(project_tmp_path / "openrouter-image.yaml", content)
    )

    assert config.roles[AgentRole.IMAGE_GENERATOR].provider_tag == "upstream/exact"


def test_loader_allows_enabled_openrouter_image_without_tag_for_semantic_readiness(
    project_tmp_path: Path,
) -> None:
    content = VALID_CONFIG.replace(
        "  image_generator:\n    enabled: false\n    provider: ollama",
        "  image_generator:\n    enabled: true\n    provider: openrouter",
        1,
    )

    config = load_runtime_config(
        _write_config(project_tmp_path / "openrouter-image-missing-tag.yaml", content)
    )

    assert config.roles[AgentRole.IMAGE_GENERATOR].provider_tag is None


def test_provider_tag_rejects_invisible_control_characters() -> None:
    with pytest.raises(ValidationError, match="control"):
        RoleConfiguration(
            provider=ProviderName.OPENROUTER,
            model="vendor/image-model",
            provider_tag="upstream\u200btag",
            image_tuning=ImageTuning(),
        )


@pytest.mark.parametrize(
    ("before", "after", "match"),
    [
        (
            "    model: x/z-image-turbo:fp8\n",
            "    model: x/z-image-turbo:fp8\n    provider_tag: upstream/exact\n",
            "provider_tag",
        ),
        (
            "  master_chef:\n    provider: ollama\n",
            "  master_chef:\n"
            "    provider: openrouter\n"
            "    provider_tag: upstream/exact\n",
            "provider_tag",
        ),
        (
            "    model: x/z-image-turbo:fp8\n",
            '    model: x/z-image-turbo:fp8\n    provider_tag: "   "\n',
            "provider_tag",
        ),
        (
            "    model: x/z-image-turbo:fp8\n",
            f"    model: x/z-image-turbo:fp8\n    provider_tag: {'x' * 201}\n",
            "provider_tag",
        ),
        (
            "    model: x/z-image-turbo:fp8\n",
            '    model: x/z-image-turbo:fp8\n    provider_tag: "{{upstream}}"\n',
            "placeholder|template|provider_tag",
        ),
    ],
)
def test_loader_rejects_invalid_or_mis_scoped_image_provider_tags(
    project_tmp_path: Path,
    before: str,
    after: str,
    match: str,
) -> None:
    content = VALID_CONFIG.replace(before, after, 1)

    with pytest.raises((ValidationError, ValueError), match=match):
        load_runtime_config(
            _write_config(project_tmp_path / "invalid-provider-tag.yaml", content)
        )


@pytest.mark.parametrize(
    ("before", "after", "match"),
    [
        ("  ollama:\n", "  openai:\n", "openai|provider"),
        ("  master_chef:\n", "  nutrition:\n", "nutrition|role"),
        ("      temperature: 0\n", "      top_k: 12\n", "top_k"),
        (
            "    editable_instruction: Suggest practical dishes.\n",
            "    editable_instruction: Use {{ingredients}}.\n",
            "placeholder|template",
        ),
    ],
)
def test_loader_rejects_forbidden_local_configuration(
    project_tmp_path: Path,
    before: str,
    after: str,
    match: str,
) -> None:
    config_path = _write_config(
        project_tmp_path / "invalid.yaml",
        VALID_CONFIG.replace(before, after, 1),
    )

    with pytest.raises((ValidationError, ValueError), match=match):
        load_runtime_config(config_path)


def test_loader_rejects_missing_required_role_selection(
    project_tmp_path: Path,
) -> None:
    start = VALID_CONFIG.index("  master_chef:")
    end = VALID_CONFIG.index("  recipe_writer:")
    config_path = _write_config(
        project_tmp_path / "incomplete.yaml",
        VALID_CONFIG[:start] + VALID_CONFIG[end:],
    )

    with pytest.raises(ValueError, match="master_chef"):
        load_runtime_config(config_path)


def test_loader_rejects_literal_secret_instead_of_environment_name(
    project_tmp_path: Path,
) -> None:
    config_path = _write_config(
        project_tmp_path / "secret.yaml",
        VALID_CONFIG.replace("OPENROUTER_API_KEY", "sk-live-secret", 1),
    )

    with pytest.raises((ValidationError, ValueError), match="api_key_env"):
        load_runtime_config(config_path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1?api_key=literal-secret",
        "https://openrouter.ai/api/v1#literal-secret",
    ],
)
def test_loader_rejects_endpoint_components_that_can_embed_secrets(
    project_tmp_path: Path,
    endpoint: str,
) -> None:
    config_path = _write_config(
        project_tmp_path / "endpoint-secret.yaml",
        VALID_CONFIG.replace("https://openrouter.ai/api/v1", endpoint, 1),
    )

    with pytest.raises((ValidationError, ValueError), match="endpoint"):
        load_runtime_config(config_path)


def test_loader_rejects_codex_command_arguments_that_can_embed_secrets(
    project_tmp_path: Path,
) -> None:
    config_path = _write_config(
        project_tmp_path / "command-secret.yaml",
        VALID_CONFIG.replace(
            "command: [codex, app-server]",
            "command: [codex, app-server, --api-key, literal-secret]",
            1,
        ),
    )

    with pytest.raises((ValidationError, ValueError), match="command"):
        load_runtime_config(config_path)


def test_loader_uses_environment_path_override(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = _write_config(project_tmp_path / "override.yaml")
    monkeypatch.setenv("COOK_MANTRA_CONFIG_PATH", str(override))

    config = load_runtime_config(environ=dict(**__import__("os").environ))

    assert config.source_path == override.resolve()


def test_malformed_yaml_is_captured_as_a_redacted_diagnostic_snapshot(
    project_tmp_path: Path,
) -> None:
    path = _write_config(project_tmp_path / "broken.yaml", "providers: [")

    snapshot = load_runtime_snapshot(path)

    assert snapshot.config is None
    assert snapshot.available is False
    assert snapshot.source_path == path.resolve()
    assert snapshot.diagnostics
    assert "providers" not in " ".join(snapshot.diagnostics)


def test_default_configuration_is_valid_and_cached_for_process_lifetime() -> None:
    get_runtime_snapshot.cache_clear()

    first = get_runtime_snapshot()
    second = get_runtime_snapshot()

    assert DEFAULT_RUNTIME_CONFIG_PATH.is_file()
    assert first is second
    assert first.available is True
    assert first.config is not None
    assert set(first.config.roles) == set(AgentRole)


def test_redacted_example_documents_openrouter_image_provider_tag() -> None:
    example_path = DEFAULT_RUNTIME_CONFIG_PATH.parent / "cook-mantra.example.yaml"
    content = example_path.read_text(encoding="utf-8")

    assert "provider_tag: provider-endpoint-tag" in content
