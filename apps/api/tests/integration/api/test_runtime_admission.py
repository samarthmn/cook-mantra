from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from api.app import create_app
from core.config import Settings
from core.runtime_config import RuntimeConfigurationSnapshot, get_runtime_snapshot
from domain.model_runtime import (
    AgentRole,
    DiscoveredModel,
    ProviderDiscoveryResult,
    ProviderErrorKind,
    ProviderName,
)


class MustNotInspectProvider:
    async def inspect(self) -> dict[str, object]:
        raise AssertionError("Invalid configuration must reject before discovery.")


class InstalledModels:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": ["qwen3.5:9b", "gpt-oss:20b"],
            "missing": [],
        }


class AuthenticationFailedDiscovery:
    async def inspect(self) -> ProviderDiscoveryResult:
        return ProviderDiscoveryResult(
            provider=ProviderName.OPENROUTER,
            models={
                "vendor/vision": DiscoveredModel(
                    model="vendor/vision",
                    available=False,
                    error=ProviderErrorKind.AUTHENTICATION_FAILED,
                )
            },
        )


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(output, format="PNG")
    return output.getvalue()


def test_invalid_runtime_rejects_upload_before_session_or_job_creation(
    project_tmp_path: Path,
) -> None:
    snapshot = RuntimeConfigurationSnapshot(
        source_path=project_tmp_path / "broken.yaml",
        diagnostics=("Runtime configuration is unavailable (ValidationError).",),
    )
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=MustNotInspectProvider(),
        runtime_snapshot=snapshot,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", _png(), "image/png")},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_configuration_invalid"
    assert app.state.session_store._sessions == {}
    assert app.state.job_store._jobs == {}


def test_absent_yaml_selected_ollama_model_rejects_before_session_creation(
    project_tmp_path: Path,
) -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    role = AgentRole.INGREDIENT_EXTRACTOR
    custom_selection = config.roles[role].model_copy(
        update={"model": "custom-vision:model"}
    )
    custom_snapshot = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={"roles": {**config.roles, role: custom_selection}}
            )
        }
    )
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=InstalledModels(),
        runtime_snapshot=custom_snapshot,
    )

    with TestClient(app) as client:
        ready_response = client.get("/api/v1/ready")
        upload_response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", _png(), "image/png")},
        )

    assert ready_response.status_code == 503
    assert ready_response.json()["error"]["code"] == "model_capability_missing"
    assert upload_response.status_code == 503
    assert upload_response.json()["error"]["code"] == "model_capability_missing"
    assert app.state.session_store._sessions == {}
    assert app.state.job_store._jobs == {}


def test_openrouter_auth_preflight_rejects_before_session_or_job_creation(
    project_tmp_path: Path,
) -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    openrouter = config.providers[ProviderName.OLLAMA].model_copy(
        update={
            "endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    )
    selection = config.roles[AgentRole.INGREDIENT_EXTRACTOR].model_copy(
        update={
            "provider": ProviderName.OPENROUTER,
            "model": "vendor/vision",
            "text_tuning": config.roles[
                AgentRole.INGREDIENT_EXTRACTOR
            ].text_tuning.model_copy(update={"context_window": None}),
        }
    )
    custom = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: openrouter,
                    },
                    "roles": {
                        **config.roles,
                        AgentRole.INGREDIENT_EXTRACTOR: selection,
                    },
                }
            )
        }
    )
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=InstalledModels(),
        runtime_snapshot=custom,
        provider_discoveries={ProviderName.OPENROUTER: AuthenticationFailedDiscovery()},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            files={"image": ("ingredients.png", _png(), "image/png")},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "provider_authentication_failed"
    assert app.state.session_store._sessions == {}
    assert app.state.job_store._jobs == {}
