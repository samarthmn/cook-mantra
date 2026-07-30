from core.config import PROJECT_ROOT, Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.max_concurrent_jobs == 2
    assert settings.max_concurrent_model_calls == 2
    assert settings.session_ttl_seconds == 21_600
    assert settings.artifact_root == PROJECT_ROOT / "tmp" / "cook-mantra-api"


def test_langsmith_is_optional() -> None:
    settings = Settings(_env_file=None)

    assert settings.langsmith_tracing is False
    assert settings.langsmith_api_key is None
    assert settings.langsmith_project == "cook-mantra"
