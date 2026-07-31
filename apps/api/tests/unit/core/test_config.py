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


def test_settings_require_ollama_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    with pytest.raises(ValidationError, match="ollama_base_url"):
        Settings(_env_file=None)


def test_settings_read_ollama_base_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://configured-ollama.test:11434")

    settings = Settings(_env_file=None)

    assert str(settings.ollama_base_url).rstrip("/") == (
        "http://configured-ollama.test:11434"
    )


def test_image_base_url_falls_back_to_text_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_IMAGE_BASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        ollama_base_url="http://text-ollama.test:11434",
    )

    assert settings.image_base_url() == settings.ollama_base_url


def test_image_base_url_returns_configured_override() -> None:
    settings = Settings(
        _env_file=None,
        ollama_base_url="http://text-ollama.test:11434",
        ollama_image_base_url="http://image-ollama.test:11434",
    )

    assert settings.image_base_url() == settings.ollama_image_base_url


def test_dish_previews_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISH_PREVIEWS_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.dish_previews_enabled is False


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
def test_settings_reject_non_positive_concurrency(
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
        Agent.NUTRITION: Model.GPT_OSS,
        Agent.IMAGE: Model.Z_IMAGE,
        Agent.SPECIALIZED_RECIPE: Model.GPT_OSS,
    }


def test_every_agent_has_a_model_and_only_the_image_agent_generates_images() -> None:
    """A text agent pointed at the image model cannot produce structured output."""
    assert set(AGENT_MODELS) == set(Agent)

    image_agents = {
        agent for agent, model in AGENT_MODELS.items() if model is Model.Z_IMAGE
    }
    assert image_agents == {Agent.IMAGE}
