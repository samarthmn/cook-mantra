import pytest

from core.errors import AppError, ErrorCode
from core.runtime_config import get_runtime_snapshot
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ProviderDiscoveryResult,
    ProviderErrorKind,
    ProviderName,
)
from services.model_runtime import ModelRuntimeReadinessService


class Discovery:
    def __init__(self, result: ProviderDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    async def inspect(self) -> ProviderDiscoveryResult:
        self.calls += 1
        return self.result


class MustNotDiscover:
    async def inspect(self) -> ProviderDiscoveryResult:
        raise AssertionError("Disabled optional roles must not trigger discovery.")


class LegacyHealth:
    def __init__(self, result: dict[str, object] | AppError) -> None:
        self.result = result
        self.calls = 0

    async def inspect(self) -> dict[str, object]:
        self.calls += 1
        if isinstance(self.result, AppError):
            raise self.result
        return self.result


class MustNotInspectLegacyHealth:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self) -> dict[str, object]:
        self.calls += 1
        raise AssertionError("Unselected Ollama compatibility health was inspected.")


def _default_ollama_result() -> ProviderDiscoveryResult:
    capabilities = (Capability.STRUCTURED_OUTPUT, Capability.TEXT)
    return ProviderDiscoveryResult(
        provider=ProviderName.OLLAMA,
        available_models=("gpt-oss:20b", "qwen3.5:9b"),
        models={
            "gpt-oss:20b": DiscoveredModel(
                model="gpt-oss:20b",
                available=True,
                capabilities=capabilities,
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


def _required_roles_on(provider_name: ProviderName):
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    providers = dict(config.providers)
    roles = dict(config.roles)
    if provider_name is ProviderName.OPENROUTER:
        providers[provider_name] = config.providers[ProviderName.OLLAMA].model_copy(
            update={
                "endpoint": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
            }
        )
        models = {
            AgentRole.INGREDIENT_EXTRACTOR: "vendor/vision",
            AgentRole.MASTER_CHEF: "vendor/text",
            AgentRole.RECIPE_WRITER: "vendor/text",
        }
    else:
        providers[provider_name] = config.providers[ProviderName.OLLAMA].model_copy(
            update={"endpoint": None, "command": ("codex", "app-server")}
        )
        models = {role: f"codex/{role.value}" for role in AgentRole if role.required}
    for role, model in models.items():
        selection = config.roles[role]
        roles[role] = selection.model_copy(
            update={
                "provider": provider_name,
                "model": model,
                "text_tuning": selection.text_tuning.model_copy(
                    update={"context_window": None, "reasoning_effort": None}
                ),
            }
        )
    return snapshot.model_copy(
        update={
            "config": config.model_copy(update={"providers": providers, "roles": roles})
        }
    )


def _ready_openrouter_result() -> ProviderDiscoveryResult:
    parameters = ("max_tokens", "structured_outputs", "temperature")
    return ProviderDiscoveryResult(
        provider=ProviderName.OPENROUTER,
        available_models=("vendor/text", "vendor/vision"),
        models={
            "vendor/text": DiscoveredModel(
                model="vendor/text",
                available=True,
                capabilities=(Capability.STRUCTURED_OUTPUT, Capability.TEXT),
                supported_parameters=parameters,
                context_window=131072,
            ),
            "vendor/vision": DiscoveredModel(
                model="vendor/vision",
                available=True,
                capabilities=(
                    Capability.STRUCTURED_OUTPUT,
                    Capability.TEXT,
                    Capability.VISION,
                ),
                supported_parameters=parameters,
                context_window=131072,
            ),
        },
    )


def _mixed_snapshot():
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = config.providers[ProviderName.OLLAMA].model_copy(
        update={
            "endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    )
    codex = config.providers[ProviderName.OLLAMA].model_copy(
        update={"endpoint": None, "command": ("codex", "app-server")}
    )
    ingredient = config.roles[AgentRole.INGREDIENT_EXTRACTOR].model_copy(
        update={
            "provider": ProviderName.OPENROUTER,
            "model": "vendor/vision",
            "text_tuning": config.roles[
                AgentRole.INGREDIENT_EXTRACTOR
            ].text_tuning.model_copy(update={"context_window": None}),
        }
    )
    image = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={"provider": ProviderName.CODEX, "model": "image-deferred"}
    )
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: provider,
                        ProviderName.CODEX: codex,
                    },
                    "roles": {
                        **config.roles,
                        AgentRole.INGREDIENT_EXTRACTOR: ingredient,
                        AgentRole.IMAGE_GENERATOR: image,
                    },
                }
            )
        }
    )


@pytest.mark.asyncio
async def test_readiness_intersects_yaml_and_discovered_capabilities_by_provider() -> (
    None
):
    snapshot = _mixed_snapshot()
    ollama = Discovery(
        ProviderDiscoveryResult(
            provider=ProviderName.OLLAMA,
            available_models=("gpt-oss:20b",),
            models={
                "gpt-oss:20b": DiscoveredModel(
                    model="gpt-oss:20b",
                    available=True,
                    capabilities=(Capability.STRUCTURED_OUTPUT, Capability.TEXT),
                    supported_parameters=("reasoning",),
                )
            },
        )
    )
    openrouter = Discovery(
        ProviderDiscoveryResult(
            provider=ProviderName.OPENROUTER,
            available_models=("vendor/vision",),
            models={
                "vendor/vision": DiscoveredModel(
                    model="vendor/vision",
                    available=True,
                    capabilities=(
                        Capability.STRUCTURED_OUTPUT,
                        Capability.TEXT,
                        Capability.VISION,
                    ),
                    supported_parameters=(
                        "reasoning",
                        "structured_outputs",
                        "temperature",
                    ),
                    context_window=131072,
                )
            },
        )
    )
    service = ModelRuntimeReadinessService(
        snapshot,
        discoveries={
            ProviderName.OLLAMA: ollama,
            ProviderName.OPENROUTER: openrouter,
            ProviderName.CODEX: MustNotDiscover(),
        },
    )

    first = await service.inspect()
    first.model_runtime.roles[AgentRole.MASTER_CHEF] = first.model_runtime.roles[
        AgentRole.MASTER_CHEF
    ].model_copy(update={"ready": False, "error": ProviderErrorKind.PROTOCOL_ERROR})
    assert first.ollama is not None
    first.ollama["available_models"] = []
    second = await service.inspect()

    assert first is not second
    assert first.model_runtime.ready is True
    ingredient = first.model_runtime.roles[AgentRole.INGREDIENT_EXTRACTOR]
    assert ingredient.available_capabilities == (
        Capability.STRUCTURED_OUTPUT,
        Capability.VISION,
    )
    assert ollama.calls == 1
    assert openrouter.calls == 1
    assert second.model_runtime.roles[AgentRole.MASTER_CHEF].ready is True
    assert second.ollama is not None
    assert second.ollama["available_models"] == ["gpt-oss:20b"]
    admitted = await service.require(AgentRole.MASTER_CHEF)
    assert admitted.ready is True


@pytest.mark.asyncio
async def test_authentication_discovery_failure_maps_to_typed_admission_error() -> None:
    snapshot = _mixed_snapshot()
    unavailable = Discovery(
        ProviderDiscoveryResult(
            provider=ProviderName.OPENROUTER,
            models={
                "vendor/vision": DiscoveredModel(
                    model="vendor/vision",
                    available=False,
                    error=ProviderErrorKind.AUTHENTICATION_FAILED,
                )
            },
        )
    )
    service = ModelRuntimeReadinessService(
        snapshot,
        discoveries={
            ProviderName.OPENROUTER: unavailable,
            ProviderName.OLLAMA: Discovery(
                ProviderDiscoveryResult(
                    provider=ProviderName.OLLAMA,
                    models={
                        "gpt-oss:20b": DiscoveredModel(
                            model="gpt-oss:20b",
                            available=True,
                            capabilities=(
                                Capability.STRUCTURED_OUTPUT,
                                Capability.TEXT,
                            ),
                        )
                    },
                )
            ),
            ProviderName.CODEX: MustNotDiscover(),
        },
    )

    with pytest.raises(AppError) as raised:
        await service.require(AgentRole.INGREDIENT_EXTRACTOR)

    assert raised.value.code is ErrorCode.PROVIDER_AUTHENTICATION_FAILED
    assert raised.value.status_code == 401
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_native_roles_preserve_deprecated_ollama_inventory() -> None:
    native = Discovery(_default_ollama_result())
    legacy = LegacyHealth(
        {
            "reachable": True,
            "available_models": ["legacy-extra:1", "gpt-oss:20b"],
            "missing": ["qwen3.5:9b"],
        }
    )
    service = ModelRuntimeReadinessService(
        get_runtime_snapshot(),
        legacy,
        discoveries={ProviderName.OLLAMA: native},
    )

    result = await service.inspect()

    assert result.model_runtime.ready is True
    assert result.ollama == {
        "reachable": True,
        "available_models": ["legacy-extra:1", "gpt-oss:20b"],
        "missing": ["qwen3.5:9b"],
    }
    assert native.calls == legacy.calls == 1


@pytest.mark.asyncio
async def test_legacy_health_failure_does_not_override_native_role_readiness() -> None:
    native = Discovery(_default_ollama_result())
    legacy = LegacyHealth(
        AppError(
            code=ErrorCode.OLLAMA_UNAVAILABLE,
            message="Ollama is unavailable.",
            status_code=503,
            retryable=True,
        )
    )
    service = ModelRuntimeReadinessService(
        get_runtime_snapshot(),
        legacy,
        discoveries={ProviderName.OLLAMA: native},
    )

    result = await service.inspect()

    assert result.model_runtime.ready is True
    assert result.ollama == {
        "reachable": True,
        "available_models": ["gpt-oss:20b", "qwen3.5:9b"],
        "missing": [],
    }
    assert native.calls == legacy.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", [ProviderName.OPENROUTER, ProviderName.CODEX])
async def test_unselected_ollama_compatibility_health_is_never_inspected(
    provider_name: ProviderName,
) -> None:
    health = MustNotInspectLegacyHealth()
    snapshot = _required_roles_on(provider_name)
    if provider_name is ProviderName.OPENROUTER:
        discovery = Discovery(_ready_openrouter_result())
    else:
        discovery = Discovery(
            ProviderDiscoveryResult(
                provider=ProviderName.CODEX,
                models={
                    f"codex/{role.value}": DiscoveredModel(
                        model=f"codex/{role.value}",
                        available=False,
                        error=ProviderErrorKind.CAPABILITY_MISSING,
                    )
                    for role in AgentRole
                    if role.required
                },
            )
        )
    service = ModelRuntimeReadinessService(
        snapshot,
        health,
        discoveries={provider_name: discovery},
    )

    inspection = await service.inspect()

    assert health.calls == 0
    if provider_name is ProviderName.OPENROUTER:
        assert inspection.model_runtime.ready is True
        assert (await service.require(AgentRole.INGREDIENT_EXTRACTOR)).ready is True
    else:
        assert inspection.model_runtime.ready is False


@pytest.mark.asyncio
async def test_enabled_image_role_uses_the_separate_image_runtime_registry() -> None:
    snapshot = _required_roles_on(ProviderName.OPENROUTER)
    assert snapshot.config is not None
    config = snapshot.config
    image = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={"enabled": True, "provider": ProviderName.OLLAMA}
    )
    snapshot = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={"roles": {**config.roles, AgentRole.IMAGE_GENERATOR: image}}
            )
        }
    )
    image_runtime = Discovery(
        ProviderDiscoveryResult(
            provider=ProviderName.OLLAMA,
            models={
                image.model: DiscoveredModel(
                    model=image.model,
                    available=False,
                    error=ProviderErrorKind.CAPABILITY_MISSING,
                )
            },
        )
    )
    service = ModelRuntimeReadinessService(
        snapshot,
        discoveries={ProviderName.OPENROUTER: Discovery(_ready_openrouter_result())},
        image_runtimes={ProviderName.OLLAMA: image_runtime},
    )

    first = await service.inspect()
    second = await service.inspect()

    image_status = first.model_runtime.roles[AgentRole.IMAGE_GENERATOR]
    assert image_status.ready is False
    assert image_status.error is ProviderErrorKind.CAPABILITY_MISSING
    assert first.model_runtime.ready is True
    assert second.model_runtime.ready is True
    assert image_runtime.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inspection_provider", "discovered_model", "expected_error"),
    [
        (
            ProviderName.OPENROUTER,
            "x/z-image-turbo:fp8",
            ProviderErrorKind.PROTOCOL_ERROR,
        ),
        (
            ProviderName.OLLAMA,
            "different-image-model",
            ProviderErrorKind.CAPABILITY_MISSING,
        ),
    ],
)
async def test_image_readiness_requires_exact_provider_and_model_identity(
    inspection_provider: ProviderName,
    discovered_model: str,
    expected_error: ProviderErrorKind,
) -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    selection = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={"enabled": True}
    )
    snapshot = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "roles": {
                        **config.roles,
                        AgentRole.IMAGE_GENERATOR: selection,
                    }
                }
            )
        }
    )
    image_runtime = Discovery(
        ProviderDiscoveryResult(
            provider=inspection_provider,
            models={
                selection.model: DiscoveredModel(
                    model=discovered_model,
                    available=True,
                    capabilities=(Capability.IMAGE_OUTPUT,),
                )
            },
        )
    )
    service = ModelRuntimeReadinessService(
        snapshot,
        discoveries={ProviderName.OLLAMA: Discovery(_default_ollama_result())},
        image_runtimes={ProviderName.OLLAMA: image_runtime},
    )

    inspection = await service.inspect()

    status = inspection.model_runtime.roles[AgentRole.IMAGE_GENERATOR]
    assert status.ready is False
    assert status.error is expected_error
    assert inspection.model_runtime.ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_runtime", [False, True])
async def test_image_readiness_missing_runtime_or_model_is_capability_missing(
    missing_runtime: bool,
) -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    selection = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={"enabled": True}
    )
    snapshot = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "roles": {
                        **config.roles,
                        AgentRole.IMAGE_GENERATOR: selection,
                    }
                }
            )
        }
    )
    image_runtimes = (
        {}
        if missing_runtime
        else {
            ProviderName.OLLAMA: Discovery(
                ProviderDiscoveryResult(provider=ProviderName.OLLAMA, models={})
            )
        }
    )
    service = ModelRuntimeReadinessService(
        snapshot,
        discoveries={ProviderName.OLLAMA: Discovery(_default_ollama_result())},
        image_runtimes=image_runtimes,
    )

    inspection = await service.inspect()

    status = inspection.model_runtime.roles[AgentRole.IMAGE_GENERATOR]
    assert status.ready is False
    assert status.error is ProviderErrorKind.CAPABILITY_MISSING
    assert inspection.model_runtime.ready is True


@pytest.mark.asyncio
async def test_disabled_image_role_never_inspects_an_image_runtime() -> None:
    service = ModelRuntimeReadinessService(
        get_runtime_snapshot(),
        discoveries={ProviderName.OLLAMA: Discovery(_default_ollama_result())},
        image_runtimes={ProviderName.OLLAMA: MustNotDiscover()},
    )

    inspection = await service.inspect()

    image_status = inspection.model_runtime.roles[AgentRole.IMAGE_GENERATOR]
    assert image_status.enabled is False
    assert image_status.ready is True
    assert inspection.model_runtime.ready is True


@pytest.mark.asyncio
async def test_enabled_codex_image_role_is_capability_missing_without_runtime() -> None:
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    image = config.roles[AgentRole.IMAGE_GENERATOR].model_copy(
        update={"enabled": True, "provider": ProviderName.CODEX, "model": "image-only"}
    )
    snapshot = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={"roles": {**config.roles, AgentRole.IMAGE_GENERATOR: image}}
            )
        }
    )
    codex_runtime = Discovery(
        ProviderDiscoveryResult(
            provider=ProviderName.CODEX,
            models={
                "image-only": DiscoveredModel(
                    model="image-only",
                    available=True,
                    capabilities=(Capability.IMAGE_OUTPUT,),
                )
            },
        )
    )
    service = ModelRuntimeReadinessService(
        snapshot,
        discoveries={ProviderName.OLLAMA: Discovery(_default_ollama_result())},
        image_runtimes={ProviderName.CODEX: codex_runtime},
    )

    inspection = await service.inspect()

    image_status = inspection.model_runtime.roles[AgentRole.IMAGE_GENERATOR]
    assert image_status.ready is False
    assert image_status.error is ProviderErrorKind.CAPABILITY_MISSING
    assert inspection.model_runtime.ready is True
    assert codex_runtime.calls == 0


def _snapshot_with_ingredient_endpoint(provider: ProviderName, endpoint: str):
    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    base_definition = config.providers.get(
        provider, config.providers[ProviderName.OLLAMA]
    )
    definition_update: dict[str, object] = {"endpoint": endpoint, "command": None}
    if provider is ProviderName.OPENROUTER:
        definition_update["api_key_env"] = "OPENROUTER_API_KEY"
    definition = base_definition.model_copy(update=definition_update)
    ingredient = config.roles[AgentRole.INGREDIENT_EXTRACTOR].model_copy(
        update={"provider": provider}
    )
    return snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {**config.providers, provider: definition},
                    "roles": {
                        **config.roles,
                        AgentRole.INGREDIENT_EXTRACTOR: ingredient,
                    },
                }
            )
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.42.7.9:11434",
        "http://[::1]:11434",
    ],
)
async def test_ollama_ingredient_media_accepts_only_literal_loopback_endpoints(
    endpoint: str,
) -> None:
    discovery = Discovery(_default_ollama_result())
    service = ModelRuntimeReadinessService(
        _snapshot_with_ingredient_endpoint(ProviderName.OLLAMA, endpoint),
        discoveries={ProviderName.OLLAMA: discovery},
    )

    inspection = await service.inspect()

    assert inspection.model_runtime.roles[AgentRole.INGREDIENT_EXTRACTOR].ready is True
    assert discovery.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://ollama.local:11434",
        "http://192.168.1.10:11434",
        "http://[::2]:11434",
    ],
)
async def test_ollama_ingredient_media_rejects_non_loopback_without_dns_resolution(
    endpoint: str,
) -> None:
    service = ModelRuntimeReadinessService(
        _snapshot_with_ingredient_endpoint(ProviderName.OLLAMA, endpoint),
        discoveries={ProviderName.OLLAMA: Discovery(_default_ollama_result())},
    )

    inspection = await service.inspect()

    status = inspection.model_runtime.roles[AgentRole.INGREDIENT_EXTRACTOR]
    assert status.ready is False
    assert status.error is ProviderErrorKind.CAPABILITY_MISSING
    assert inspection.model_runtime.roles[AgentRole.MASTER_CHEF].ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "expected_ready"),
    [
        ("https://openrouter.ai/api/v1", True),
        ("https://openrouter.ai/api/v1/", True),
        ("http://openrouter.ai/api/v1", False),
        ("https://api.openrouter.ai/api/v1", False),
        ("https://openrouter.ai:444/api/v1", False),
        ("https://openrouter.ai/api/v1/extra", False),
    ],
)
async def test_openrouter_ingredient_media_requires_the_canonical_official_base(
    endpoint: str,
    expected_ready: bool,
) -> None:
    discovery = Discovery(
        ProviderDiscoveryResult(
            provider=ProviderName.OPENROUTER,
            models={
                "qwen3.5:9b": DiscoveredModel(
                    model="qwen3.5:9b",
                    available=True,
                    capabilities=(Capability.STRUCTURED_OUTPUT, Capability.VISION),
                    supported_parameters=(
                        "reasoning",
                        "structured_outputs",
                        "temperature",
                    ),
                    context_window=131_072,
                )
            },
        )
    )
    service = ModelRuntimeReadinessService(
        _snapshot_with_ingredient_endpoint(ProviderName.OPENROUTER, endpoint),
        discoveries={
            ProviderName.OPENROUTER: discovery,
            ProviderName.OLLAMA: Discovery(_default_ollama_result()),
        },
    )

    inspection = await service.inspect()

    status = inspection.model_runtime.roles[AgentRole.INGREDIENT_EXTRACTOR]
    assert status.ready is expected_ready
    assert status.error is (
        None if expected_ready else ProviderErrorKind.CAPABILITY_MISSING
    )
    assert inspection.model_runtime.roles[AgentRole.MASTER_CHEF].ready is True
    assert discovery.calls == (1 if expected_ready else 0)
