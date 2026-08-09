from pathlib import Path

import pytest
from pydantic import SecretStr

import api.app as app_module
from api.app import create_app
from core.config import Settings
from core.runtime_config import ProviderConfiguration, get_runtime_snapshot
from domain.model_runtime import AgentRole, Capability, ProviderName
from services.image_generation import RuntimeImageGenerator


class FakeImageRuntime:
    async def inspect(self):  # pragma: no cover - composition must not inspect
        raise AssertionError("Application construction must not perform discovery.")

    async def generate_image(self, prompt, *, tuning):  # pragma: no cover
        raise AssertionError("Application construction must not generate an image.")


def _settings(artifact_root: Path, **updates: object) -> Settings:
    return Settings(_env_file=None, artifact_root=artifact_root, **updates)


def _image_snapshot(
    provider: ProviderName,
    *,
    enabled: bool = True,
    provider_tag: str | None = None,
):
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    providers = dict(config.providers)
    if provider is ProviderName.OPENROUTER:
        providers[provider] = ProviderConfiguration(
            endpoint="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        )
    elif provider is ProviderName.CODEX:
        providers[provider] = ProviderConfiguration(command=("codex", "app-server"))
    selection = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={
            "enabled": enabled,
            "provider": provider,
            "provider_tag": provider_tag,
        }
    )
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": providers,
                    "roles": {
                        **config.roles,
                        AgentRole.IMAGE_GENERATOR: selection,
                    },
                }
            )
        }
    )


def test_disabled_image_role_constructs_no_provider_runtime(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_construct(**_kwargs: object) -> FakeImageRuntime:
        raise AssertionError("A disabled image role must construct no runtime.")

    monkeypatch.setattr(app_module, "OllamaImageRuntime", must_not_construct)

    app = create_app(settings=_settings(project_tmp_path))

    assert app.state.image_runtimes == {}
    assert isinstance(app.state.image_generator, RuntimeImageGenerator)
    assert not hasattr(app.state, "owned_image_generator")


def test_enabled_ollama_image_role_composes_the_exact_selection(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeImageRuntime()
    captured: dict[str, object] = {}

    def build_runtime(**kwargs: object) -> FakeImageRuntime:
        captured.update(kwargs)
        return runtime

    monkeypatch.setattr(app_module, "OllamaImageRuntime", build_runtime)
    snapshot = _image_snapshot(ProviderName.OLLAMA)
    assert snapshot.config is not None
    selection = snapshot.config.roles[AgentRole.IMAGE_GENERATOR]

    app = create_app(
        settings=_settings(project_tmp_path),
        runtime_snapshot=snapshot,
    )

    assert app.state.image_runtimes == {ProviderName.OLLAMA: runtime}
    assert captured == {
        "endpoint": str(snapshot.config.providers[ProviderName.OLLAMA].endpoint),
        "model": selection.model,
        "tuning": selection.image_tuning,
        "configured_capabilities": frozenset({Capability.IMAGE_OUTPUT}),
    }


def test_enabled_openrouter_image_role_resolves_only_the_named_secret(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeImageRuntime()
    captured: dict[str, object] = {}

    def build_runtime(**kwargs: object) -> FakeImageRuntime:
        captured.update(kwargs)
        return runtime

    monkeypatch.setattr(app_module, "OpenRouterImageRuntime", build_runtime)
    snapshot = _image_snapshot(
        ProviderName.OPENROUTER,
        provider_tag="upstream/exact",
    )
    assert snapshot.config is not None
    selection = snapshot.config.roles[AgentRole.IMAGE_GENERATOR]

    app = create_app(
        settings=_settings(project_tmp_path, openrouter_api_key="test-secret"),
        runtime_snapshot=snapshot,
    )

    assert app.state.image_runtimes == {ProviderName.OPENROUTER: runtime}
    api_key = captured.pop("api_key")
    assert isinstance(api_key, SecretStr)
    assert api_key.get_secret_value() == "test-secret"
    assert captured == {
        "endpoint": str(snapshot.config.providers[ProviderName.OPENROUTER].endpoint),
        "model": selection.model,
        "provider_tag": "upstream/exact",
        "tuning": selection.image_tuning,
        "configured_capabilities": frozenset({Capability.IMAGE_OUTPUT}),
    }


def test_missing_openrouter_image_secret_still_composes_a_fail_closed_runtime(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeImageRuntime()
    captured: dict[str, object] = {}

    def build_runtime(**kwargs: object) -> FakeImageRuntime:
        captured.update(kwargs)
        return runtime

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "OpenRouterImageRuntime", build_runtime)

    app = create_app(
        settings=_settings(project_tmp_path),
        runtime_snapshot=_image_snapshot(
            ProviderName.OPENROUTER,
            provider_tag="upstream/exact",
        ),
    )

    assert app.state.image_runtimes == {ProviderName.OPENROUTER: runtime}
    api_key = captured["api_key"]
    assert isinstance(api_key, SecretStr)
    assert api_key.get_secret_value() == ""


def test_image_only_codex_selection_creates_no_owner_or_image_invoker(
    project_tmp_path: Path,
) -> None:
    codex_runtime = FakeImageRuntime()
    app = create_app(
        settings=_settings(project_tmp_path),
        runtime_snapshot=_image_snapshot(ProviderName.CODEX),
        codex_process_factory=object(),  # type: ignore[arg-type]
        image_runtimes={ProviderName.CODEX: codex_runtime},
    )

    assert app.state.codex_app_server_client is None
    assert app.state.image_runtimes == {}
    assert isinstance(app.state.image_generator, RuntimeImageGenerator)
