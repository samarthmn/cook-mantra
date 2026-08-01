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


def test_dish_previews_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISH_PREVIEWS_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.dish_previews_enabled is False


@pytest.mark.parametrize(
    ("beast_base_url", "beast_api_key"),
    [
        (None, None),
        ("http://beast.test:4900", None),
        (None, "secret-key"),
    ],
)
def test_enabled_previews_require_complete_beast_configuration(
    beast_base_url: str | None,
    beast_api_key: str | None,
) -> None:
    with pytest.raises(
        ValidationError,
        match="dish previews require both beast_base_url and beast_api_key",
    ):
        Settings(
            _env_file=None,
            dish_previews_enabled=True,
            beast_base_url=beast_base_url,
            beast_api_key=beast_api_key,
        )


def test_disabled_previews_allow_missing_beast_configuration() -> None:
    settings = Settings(
        _env_file=None,
        dish_previews_enabled=False,
        beast_base_url=None,
        beast_api_key=None,
    )

    assert settings.beast_base_url is None
    assert settings.beast_api_key is None


def test_nutrition_lookup_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.nutrition_lookup_enabled is False
    assert settings.nutrition_api_base_url is None


def test_enabled_nutrition_lookup_requires_base_url() -> None:
    with pytest.raises(
        ValidationError,
        match="nutrition lookup requires nutrition_api_base_url when enabled",
    ):
        Settings(
            _env_file=None,
            nutrition_lookup_enabled=True,
            nutrition_api_base_url=None,
        )


def test_enabled_nutrition_lookup_accepts_base_url() -> None:
    settings = Settings(
        _env_file=None,
        nutrition_lookup_enabled=True,
        nutrition_api_base_url="http://nutrition.test:5900",
    )

    assert str(settings.nutrition_api_base_url).rstrip("/") == (
        "http://nutrition.test:5900"
    )


def test_enabled_previews_accept_complete_beast_configuration() -> None:
    settings = Settings(
        _env_file=None,
        dish_previews_enabled=True,
        beast_base_url="http://beast.test:4900",
        beast_api_key="secret-key",
    )

    assert str(settings.beast_base_url).rstrip("/") == "http://beast.test:4900"
    assert settings.beast_api_key is not None
    assert settings.beast_api_key.get_secret_value() == "secret-key"


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
    assert settings.beast_image_model == "z-image-turbo"
    assert settings.beast_poll_interval_seconds == 2.0


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
        ("beast_poll_interval_seconds", 0),
        ("beast_poll_interval_seconds", -1),
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
        Agent.NUTRITION: Model.GPT_OSS,
        Agent.SPECIALIZED_RECIPE: Model.GPT_OSS,
    }


def test_every_text_agent_has_an_ollama_model() -> None:
    assert set(AGENT_MODELS) == set(Agent)
