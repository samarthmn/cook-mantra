from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import AGENT_MODELS, PROJECT_ROOT, Agent, Model, Settings


def test_settings_resolve_env_file_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp_path: Path,
) -> None:
    monkeypatch.chdir(project_tmp_path)

    configured_env_file = Path(Settings.model_config["env_file"])

    assert configured_env_file == PROJECT_ROOT / ".env"
    assert configured_env_file.is_absolute()


def test_model_endpoint_is_no_longer_required_in_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    Settings(_env_file=None)

    assert "ollama_base_url" not in Settings.model_fields


def test_legacy_dish_preview_environment_flag_is_not_a_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISH_PREVIEWS_ENABLED", raising=False)

    Settings(_env_file=None)

    assert "dish_previews_enabled" not in Settings.model_fields


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_concurrent_jobs == 2
    assert settings.max_queued_jobs == 4
    assert settings.max_concurrent_model_calls == 2
    assert settings.cleanup_interval_seconds == 300
    assert settings.log_level == "INFO"
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.session_ttl_seconds == 21_600
    assert settings.artifact_root == PROJECT_ROOT / "tmp" / "cook-mantra-api"


def test_beast_and_image_tuning_are_not_parallel_settings_sources() -> None:
    assert {
        "beast_base_url",
        "beast_api_key",
        "beast_image_model",
        "beast_poll_interval_seconds",
        "image_width",
        "image_height",
        "image_steps",
        "image_timeout_seconds",
    }.isdisjoint(Settings.model_fields)


def test_langsmith_is_optional() -> None:
    settings = Settings(_env_file=None)

    assert settings.langsmith_tracing is False
    assert settings.langsmith_api_key is None
    assert settings.langsmith_project == "cook-mantra"


def test_settings_reject_an_unknown_log_level() -> None:
    with pytest.raises(ValidationError, match="log_level"):
        Settings(_env_file=None, log_level="VERBOSE")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_concurrent_jobs", 0),
        ("max_concurrent_jobs", -1),
        ("max_concurrent_model_calls", 0),
        ("max_concurrent_model_calls", -1),
        ("cleanup_interval_seconds", 9),
        ("cleanup_interval_seconds", 0),
    ],
)
def test_settings_reject_invalid_positive_tuning_values(
    field_name: str,
    invalid_value: int,
) -> None:
    with pytest.raises(ValidationError, match=field_name):
        Settings(_env_file=None, **{field_name: invalid_value})


def test_settings_accept_zero_queued_jobs_and_reject_negative_queue() -> None:
    assert Settings(_env_file=None, max_queued_jobs=0).max_queued_jobs == 0

    with pytest.raises(ValidationError, match="max_queued_jobs"):
        Settings(_env_file=None, max_queued_jobs=-1)


@pytest.mark.parametrize(
    "invalid_value",
    [0, -1, 10 * 1024 * 1024 + 1],
)
def test_settings_reject_upload_limits_outside_ten_mib(
    invalid_value: int,
) -> None:
    with pytest.raises(ValidationError, match="max_upload_bytes"):
        Settings(_env_file=None, max_upload_bytes=invalid_value)


def test_settings_reject_artifact_roots_outside_project_tmp() -> None:
    outside_runtime_namespace = PROJECT_ROOT / "artifacts"

    with pytest.raises(ValidationError, match="artifact_root"):
        Settings(_env_file=None, artifact_root=outside_runtime_namespace)


def test_agent_model_assignments_are_deliberate() -> None:
    """The one place that pins agent-to-model wiring.

    Every other test derives from AGENT_MODELS, so retuning which model an
    agent uses means updating this test and nothing else.
    """
    assert AGENT_MODELS == {
        Agent.INGREDIENT_EXTRACTION: Model.QWEN_SMALL,
        Agent.MASTER_CHEF: Model.GPT_OSS,
        Agent.SPECIALIZED_RECIPE: Model.GPT_OSS,
    }


def test_every_text_agent_has_an_ollama_model() -> None:
    assert set(AGENT_MODELS) == set(Agent)
