import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import api.app as app_module
from agents.ingredient_extraction import OllamaIngredientExtractor
from agents.master_chef import OllamaMasterChef
from agents.specialized_recipe import OllamaSpecializedRecipeAgent
from api.app import create_app
from core.config import Settings
from core.runtime_config import RuntimeConfigurationSnapshot, get_runtime_snapshot
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ProviderDiscoveryResult,
    ProviderErrorKind,
    ProviderName,
)
from services.image_generation import RuntimeImageGenerator
from services.ollama_health import OllamaHealthService
from tests.tracing_support import enabled_tracing


class ReadyOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": ["qwen3.5:9b", "gpt-oss:20b"],
            "missing": [],
        }


class ExplodingOllama:
    async def inspect(self) -> dict[str, object]:
        raise AssertionError("The process health route must not inspect Ollama.")


class MissingModelOllama:
    async def inspect(self) -> dict[str, object]:
        return {
            "reachable": True,
            "available_models": ["qwen3.5:9b"],
            "missing": ["gpt-oss:20b"],
        }


class CountingOllama(ReadyOllama):
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self) -> dict[str, object]:
        self.calls += 1
        return await super().inspect()


class CountingDiscovery:
    def __init__(self, result: ProviderDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    async def inspect(self) -> ProviderDiscoveryResult:
        self.calls += 1
        return self.result


class MustNotDiscover:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self) -> ProviderDiscoveryResult:
        self.calls += 1
        raise AssertionError("A disabled image role must not trigger discovery.")


class FalseyTracingService:
    def __bool__(self) -> bool:
        return False


class RecordingArtifactStore:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._shutdown_error = shutdown_error

    async def startup(self) -> None:
        self._events.append("artifact.startup")

    async def shutdown(self) -> None:
        self._events.append("artifact.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error


class RecordingJobRunner:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._shutdown_error = shutdown_error

    async def shutdown(self) -> None:
        self._events.append("runner.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error


class RecordingCleanupSupervisor:
    def __init__(
        self,
        events: list[str],
        *,
        shutdown_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._shutdown_error = shutdown_error

    async def startup(self) -> None:
        self._events.append("cleanup.startup")

    async def shutdown(self) -> None:
        self._events.append("cleanup.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error


def settings_for(artifact_root: Path) -> Settings:
    return Settings(_env_file=None, artifact_root=artifact_root)


def runtime_snapshot_with_image_enabled(enabled: bool) -> RuntimeConfigurationSnapshot:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    role = AgentRole.IMAGE_GENERATOR
    image_selection = config.roles[role].model_copy(update={"enabled": enabled})
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={"roles": {**config.roles, role: image_selection}}
            )
        }
    )


def ollama_discovery_with_ingredient_error(
    error: ProviderErrorKind,
) -> ProviderDiscoveryResult:
    return ProviderDiscoveryResult(
        provider=ProviderName.OLLAMA,
        available_models=("gpt-oss:20b",),
        models={
            "qwen3.5:9b": DiscoveredModel(
                model="qwen3.5:9b",
                available=False,
                error=error,
            ),
            "gpt-oss:20b": DiscoveredModel(
                model="gpt-oss:20b",
                available=True,
                capabilities=(Capability.STRUCTURED_OUTPUT, Capability.TEXT),
                supported_parameters=("reasoning",),
            ),
        },
    )


def test_health_is_process_only(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ExplodingOllama(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_runtime_status_is_safe_cache_free_and_process_cached(
    project_tmp_path: Path,
) -> None:
    ollama = CountingOllama()
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ollama,
    )

    with TestClient(app) as client:
        first = client.get("/api/v1/runtime-status")
        second = client.get("/api/v1/runtime-status")

    assert first.status_code == second.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    assert second.headers["Cache-Control"] == "no-store"
    assert first.json() == second.json()
    assert ollama.calls == 1

    body = first.json()
    assert set(body) == {"status", "runtime_revision", "model_runtime"}
    assert body["status"] == "ok"
    assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", body["runtime_revision"])
    assert set(body["model_runtime"]) == {"ready", "roles"}
    assert body["model_runtime"]["ready"] is True
    assert list(body["model_runtime"]["roles"]) == [role.value for role in AgentRole]
    allowed_role_fields = {
        "role",
        "provider",
        "model",
        "enabled",
        "ready",
        "required_capabilities",
        "available_capabilities",
        "error",
    }
    assert all(
        set(role_status) == allowed_role_fields
        for role_status in body["model_runtime"]["roles"].values()
    )
    forbidden_fields = {
        "endpoint",
        "command",
        "source_path",
        "api_key_env",
        "secret",
        "provider_tag",
        "diagnostics",
        "version",
        "ollama",
        "available_models",
        "beast",
    }
    for field in forbidden_fields:
        assert f'"{field}":' not in first.text.lower()


def test_runtime_revision_is_stable_per_app_and_unique_between_apps(
    project_tmp_path: Path,
) -> None:
    first_app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )
    second_app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )

    with TestClient(first_app) as client:
        first_revision = client.get("/api/v1/runtime-status").json()["runtime_revision"]
        repeated_revision = client.get("/api/v1/runtime-status").json()[
            "runtime_revision"
        ]
    with TestClient(second_app) as client:
        second_revision = client.get("/api/v1/runtime-status").json()[
            "runtime_revision"
        ]

    assert first_revision == repeated_revision
    assert first_revision != second_revision


def test_malformed_runtime_status_is_attention_without_leaking_diagnostics(
    project_tmp_path: Path,
) -> None:
    snapshot = RuntimeConfigurationSnapshot(
        source_path=project_tmp_path / "private-config-location.yaml",
        diagnostics=(
            "OPENROUTER_API_KEY=do-not-return endpoint=https://private.invalid",
        ),
    )
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ExplodingOllama(),
        runtime_snapshot=snapshot,
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/runtime-status")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["status"] == "attention"
    assert body["model_runtime"]["ready"] is False
    assert all(
        role_status["error"] == "protocol_error"
        for role_status in body["model_runtime"]["roles"].values()
    )
    assert "private-config-location" not in response.text
    assert "OPENROUTER_API_KEY" not in response.text
    assert "do-not-return" not in response.text
    assert "private.invalid" not in response.text


@pytest.mark.parametrize(
    "error",
    [
        ProviderErrorKind.AUTHENTICATION_FAILED,
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.CAPABILITY_MISSING,
    ],
)
def test_runtime_status_normalizes_required_role_failures_as_safe_attention(
    project_tmp_path: Path,
    error: ProviderErrorKind,
) -> None:
    discovery = CountingDiscovery(ollama_discovery_with_ingredient_error(error))
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        provider_discoveries={ProviderName.OLLAMA: discovery},
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/runtime-status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "attention"
    assert body["model_runtime"]["ready"] is False
    ingredient = body["model_runtime"]["roles"]["ingredient_extractor"]
    assert ingredient["ready"] is False
    assert ingredient["error"] == error.value
    assert discovery.calls == 1


def test_optional_image_failure_needs_attention_but_does_not_block_readiness(
    project_tmp_path: Path,
) -> None:
    snapshot = runtime_snapshot_with_image_enabled(True)
    assert snapshot.config is not None
    image_model = snapshot.config.roles[AgentRole.IMAGE_GENERATOR].model
    image_runtime = CountingDiscovery(
        ProviderDiscoveryResult(
            provider=ProviderName.OLLAMA,
            models={
                image_model: DiscoveredModel(
                    model=image_model,
                    available=False,
                    error=ProviderErrorKind.RATE_LIMITED,
                )
            },
        )
    )
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        runtime_snapshot=snapshot,
        image_runtimes={ProviderName.OLLAMA: image_runtime},
    )

    with TestClient(app) as client:
        status_response = client.get("/api/v1/runtime-status")
        ready_response = client.get("/api/v1/ready")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "attention"
    assert status_response.json()["model_runtime"]["ready"] is True
    assert status_response.json()["model_runtime"]["roles"]["image_generator"] == {
        "role": "image_generator",
        "provider": "ollama",
        "model": image_model,
        "enabled": True,
        "ready": False,
        "required_capabilities": ["image_output"],
        "available_capabilities": [],
        "error": "rate_limited",
    }
    assert ready_response.status_code == 200
    assert image_runtime.calls == 1


def test_disabled_image_role_keeps_runtime_ok_without_image_discovery(
    project_tmp_path: Path,
) -> None:
    image_runtime = MustNotDiscover()
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        image_runtimes={ProviderName.OLLAMA: image_runtime},
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/runtime-status")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert (
        response.json()["model_runtime"]["roles"]["image_generator"]["enabled"] is False
    )
    assert image_runtime.calls == 0


def test_app_starts_with_previews_disabled_and_no_image_runtime(
    project_tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, artifact_root=project_tmp_path)
    app = create_app(settings=settings, ollama_health=ReadyOllama())

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert isinstance(app.state.image_generator, RuntimeImageGenerator)
    assert app.state.image_runtimes == {}
    assert not hasattr(app.state, "owned_image_generator")


def test_disabled_yaml_image_role_stays_out_of_the_recipe_option_graph(
    project_tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, artifact_root=project_tmp_path)
    app = create_app(settings=settings, ollama_health=ReadyOllama())

    assert isinstance(app.state.image_generator, RuntimeImageGenerator)
    assert app.state.image_runtimes == {}
    assert not hasattr(app.state.recipe_options_dependencies, "dish_previews")
    assert not hasattr(app.state.recipe_options_dependencies, "dish_previews_enabled")


def test_enabled_yaml_image_role_does_not_join_the_recipe_option_graph(
    project_tmp_path: Path,
) -> None:
    generator = object()
    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path,
        ),
        ollama_health=ReadyOllama(),
        image_generator=generator,  # type: ignore[arg-type]
        runtime_snapshot=runtime_snapshot_with_image_enabled(True),
    )

    assert app.state.image_generator is generator
    assert not hasattr(app.state.recipe_options_dependencies, "dish_previews")
    assert not hasattr(app.state.recipe_options_dependencies, "dish_previews_enabled")


def test_app_shares_one_tracing_service_across_all_real_agents(
    project_tmp_path: Path,
) -> None:
    tracing, _ = enabled_tracing()

    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        tracing_service=tracing,
    )

    assert app.state.tracing_service is tracing
    assert isinstance(app.state.ingredient_extractor, OllamaIngredientExtractor)
    assert app.state.ingredient_extractor._tracing is tracing
    assert isinstance(
        app.state.recipe_options_dependencies.master_chef,
        OllamaMasterChef,
    )
    assert app.state.recipe_options_dependencies.master_chef._tracing is tracing
    assert isinstance(
        app.state.complete_recipes_dependencies.agent,
        OllamaSpecializedRecipeAgent,
    )
    assert app.state.complete_recipes_dependencies.agent._tracing is tracing


def test_app_composes_a_bounded_process_local_usage_journal(
    project_tmp_path: Path,
) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )

    assert app.state.model_usage_journal is not None
    assert app.state.structured_model_factory._usage_journal is (
        app.state.model_usage_journal
    )


def test_app_preserves_a_falsey_injected_tracing_double(
    project_tmp_path: Path,
) -> None:
    tracing = FalseyTracingService()

    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
        tracing_service=tracing,
    )

    assert app.state.tracing_service is tracing
    assert app.state.ingredient_extractor._tracing is tracing


def test_ready_reports_model_inventory(project_tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_runtime": {
            "ready": True,
            "roles": {
                "ingredient_extractor": {
                    "role": "ingredient_extractor",
                    "provider": "ollama",
                    "model": "qwen3.5:9b",
                    "enabled": True,
                    "ready": True,
                    "required_capabilities": ["structured_output", "vision"],
                    "available_capabilities": ["structured_output", "vision"],
                },
                "master_chef": {
                    "role": "master_chef",
                    "provider": "ollama",
                    "model": "gpt-oss:20b",
                    "enabled": True,
                    "ready": True,
                    "required_capabilities": ["structured_output", "text"],
                    "available_capabilities": ["structured_output", "text"],
                },
                "recipe_writer": {
                    "role": "recipe_writer",
                    "provider": "ollama",
                    "model": "gpt-oss:20b",
                    "enabled": True,
                    "ready": True,
                    "required_capabilities": ["structured_output", "text"],
                    "available_capabilities": ["structured_output", "text"],
                },
                "image_generator": {
                    "role": "image_generator",
                    "provider": "ollama",
                    "model": "x/z-image-turbo:fp8",
                    "enabled": False,
                    "ready": True,
                    "required_capabilities": ["image_output"],
                    "available_capabilities": ["image_output"],
                },
            },
        },
        "ollama": {
            "reachable": True,
            "available_models": ["qwen3.5:9b", "gpt-oss:20b"],
            "missing": [],
        },
    }


def test_default_composition_merges_native_roles_with_legacy_ollama_inventory(
    project_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NativeDiscovery:
        async def inspect(self) -> ProviderDiscoveryResult:
            return ProviderDiscoveryResult(
                provider=ProviderName.OLLAMA,
                available_models=("gpt-oss:20b", "qwen3.5:9b"),
                models={
                    "gpt-oss:20b": DiscoveredModel(
                        model="gpt-oss:20b",
                        available=True,
                        capabilities=(Capability.STRUCTURED_OUTPUT, Capability.TEXT),
                        supported_parameters=("reasoning",),
                    ),
                    "qwen3.5:9b": DiscoveredModel(
                        model="qwen3.5:9b",
                        available=True,
                        capabilities=(
                            Capability.STRUCTURED_OUTPUT,
                            Capability.TEXT,
                            Capability.VISION,
                        ),
                    ),
                },
            )

    class LegacyHealth:
        async def inspect(self) -> dict[str, object]:
            return {
                "reachable": True,
                "available_models": ["legacy-extra:1"],
                "missing": ["gpt-oss:20b"],
            }

    monkeypatch.setattr(
        app_module,
        "OllamaModelDiscovery",
        lambda **_kwargs: NativeDiscovery(),
    )
    monkeypatch.setattr(
        app_module,
        "OllamaHealthService",
        lambda *_args, **_kwargs: LegacyHealth(),
    )
    app = create_app(settings=settings_for(project_tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["model_runtime"]["ready"] is True
    assert body["ollama"] == {
        "reachable": True,
        "available_models": ["legacy-extra:1"],
        "missing": ["gpt-oss:20b"],
    }
    assert "beast" not in body


def test_invalid_runtime_configuration_keeps_health_live_and_readiness_unavailable(
    project_tmp_path: Path,
) -> None:
    snapshot = RuntimeConfigurationSnapshot(
        source_path=project_tmp_path / "broken.yaml",
        diagnostics=("Runtime configuration is unavailable (ParserError).",),
    )
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ExplodingOllama(),
        runtime_snapshot=snapshot,
    )

    with TestClient(app) as client:
        health_response = client.get("/api/v1/health")
        ready_response = client.get(
            "/api/v1/ready",
            headers={"X-Request-ID": "req-invalid-config"},
        )

    assert health_response.status_code == 200
    assert ready_response.status_code == 503
    assert ready_response.json()["error"] == {
        "code": "model_configuration_invalid",
        "message": "The model runtime configuration is invalid.",
        "details": {"diagnostics": list(snapshot.diagnostics)},
        "retryable": False,
        "request_id": "req-invalid-config",
        "session_id": None,
        "job_id": None,
    }


def test_ready_maps_missing_selected_model_to_generic_capability_error(
    project_tmp_path: Path,
) -> None:
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=MissingModelOllama(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ready",
            headers={"X-Request-ID": "req-missing-model"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "model_capability_missing",
            "message": "A selected model lacks a required capability.",
            "details": {"role": "master_chef"},
            "retryable": False,
            "request_id": "req-missing-model",
            "session_id": None,
            "job_id": None,
        }
    }


def test_ready_caches_provider_discovery_for_the_process(
    project_tmp_path: Path,
) -> None:
    ollama = CountingOllama()
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ollama,
    )

    with TestClient(app) as client:
        first = client.get("/api/v1/ready")
        second = client.get("/api/v1/ready")

    assert first.status_code == second.status_code == 200
    assert ollama.calls == 1


def test_ready_hides_ollama_connection_details(project_tmp_path: Path) -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "connection refused at private-host:11434",
            request=request,
        )

    ollama_health = OllamaHealthService(
        "http://ollama.local:11434",
        timeout_seconds=1,
        transport=httpx.MockTransport(fail),
    )
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ollama_health,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ready",
            headers={"X-Request-ID": "req-ollama-down"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "provider_unavailable",
            "message": "A selected model provider is unavailable.",
            "details": {"role": "ingredient_extractor"},
            "retryable": True,
            "request_id": "req-ollama-down",
            "session_id": None,
            "job_id": None,
        }
    }
    assert "private-host" not in response.text


def test_lifespan_releases_artifacts_when_job_shutdown_fails(
    project_tmp_path: Path,
) -> None:
    events: list[str] = []
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )
    app.state.artifact_store = RecordingArtifactStore(events)
    app.state.job_runner = RecordingJobRunner(
        events,
        shutdown_error=RuntimeError("runner shutdown failed"),
    )
    app.state.cleanup_supervisor = RecordingCleanupSupervisor(events)

    with (
        pytest.raises(RuntimeError, match="runner shutdown failed"),
        TestClient(app),
    ):
        pass

    assert events == [
        "artifact.startup",
        "cleanup.startup",
        "cleanup.shutdown",
        "runner.shutdown",
        "artifact.shutdown",
    ]


def test_lifespan_releases_runtime_resources_when_cleanup_shutdown_fails(
    project_tmp_path: Path,
) -> None:
    events: list[str] = []
    app = create_app(
        settings=settings_for(project_tmp_path),
        ollama_health=ReadyOllama(),
    )
    app.state.artifact_store = RecordingArtifactStore(events)
    app.state.job_runner = RecordingJobRunner(events)
    app.state.cleanup_supervisor = RecordingCleanupSupervisor(
        events,
        shutdown_error=RuntimeError("cleanup shutdown failed"),
    )

    with (
        pytest.raises(RuntimeError, match="cleanup shutdown failed"),
        TestClient(app),
    ):
        pass

    assert events == [
        "artifact.startup",
        "cleanup.startup",
        "cleanup.shutdown",
        "runner.shutdown",
        "artifact.shutdown",
    ]
