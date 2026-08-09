import json

import httpx

import api.app as app_module
from agents import ingredient_extraction, master_chef, specialized_recipe
from api.app import create_app
from core.config import Settings
from domain.ingredients import ExtractionResult
from domain.model_runtime import (
    AgentRole,
    Capability,
    DiscoveredModel,
    ModelMessage,
    ProviderDiscoveryResult,
    ProviderName,
)
from services.providers.ollama import OllamaModelDiscovery


def test_provider_neutral_agent_names_are_primary() -> None:
    assert callable(getattr(ingredient_extraction, "IngredientExtractionAgent", None))
    assert callable(getattr(master_chef, "MasterChefAgent", None))
    assert callable(getattr(specialized_recipe, "RecipeWriterAgent", None))


class CapturingModel:
    def __init__(self) -> None:
        self.messages: object | None = None

    async def ainvoke(
        self,
        messages: object,
        *,
        config: dict[str, object] | None = None,
    ) -> ExtractionResult:
        self.messages = messages
        return ExtractionResult(detected=[{"name": "Tomato", "confidence": 0.9}])


class StaticDiscovery:
    def __init__(self, result: ProviderDiscoveryResult) -> None:
        self.result = result

    async def inspect(self) -> ProviderDiscoveryResult:
        return self.result.model_copy(deep=True)


async def test_ingredient_agent_passes_private_media_in_canonical_message() -> None:
    model = CapturingModel()
    agent = ingredient_extraction.IngredientExtractionAgent(
        model=model,
        editable_instruction="Use canonical names.",
    )

    result = await agent.extract(b"private-image-canary", "image/png")

    assert result.detected[0].name == "Tomato"
    assert isinstance(model.messages, list)
    assert len(model.messages) == 1
    message = model.messages[0]
    assert isinstance(message, ModelMessage)
    assert message.image == b"private-image-canary"
    assert message.media_type == "image/png"
    assert "private-image-canary" not in repr(message)


def test_application_composes_all_agents_with_one_role_model_factory(
    project_tmp_path,
) -> None:
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
        ollama_health=type(
            "ReadyOllama",
            (),
            {
                "inspect": lambda self: _ready_ollama(),
            },
        )(),
    )

    factory = app.state.structured_model_factory
    assert app.state.ingredient_extractor._model_factory is factory
    assert app.state.recipe_options_dependencies.master_chef._model_factory is factory
    assert app.state.complete_recipes_dependencies.agent._model_factory is factory


async def _ready_ollama() -> dict[str, object]:
    return {
        "reachable": True,
        "available_models": ["qwen3.5:9b", "gpt-oss:20b"],
        "missing": [],
    }


def test_default_application_uses_native_ollama_discovery(project_tmp_path) -> None:
    app = create_app(
        settings=Settings(_env_file=None, artifact_root=project_tmp_path),
    )
    assert isinstance(
        app.state.model_runtime_readiness._discoveries[ProviderName.OLLAMA],
        OllamaModelDiscovery,
    )
    assert (
        app.state.model_runtime_readiness._discoveries[
            ProviderName.OLLAMA
        ]._media_bearing
        is True
    )


def test_application_constructs_no_remote_discovery_for_rejected_media_only_endpoint(
    project_tmp_path,
    monkeypatch,
) -> None:
    from core.runtime_config import get_runtime_snapshot

    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = config.providers[ProviderName.OLLAMA].model_copy(
        update={
            "endpoint": "https://unapproved.example/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    )
    ingredient = config.roles[AgentRole.INGREDIENT_EXTRACTOR].model_copy(
        update={"provider": ProviderName.OPENROUTER, "model": "vendor/vision"}
    )
    custom = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: provider,
                    },
                    "roles": {
                        **config.roles,
                        AgentRole.INGREDIENT_EXTRACTOR: ingredient,
                    },
                }
            )
        }
    )

    def must_not_construct(**_kwargs: object) -> object:
        raise AssertionError("Rejected media endpoints must not receive discovery.")

    monkeypatch.setattr(app_module, "OpenRouterModelDiscovery", must_not_construct)

    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path,
            openrouter_api_key="secret-must-not-leave",
        ),
        runtime_snapshot=custom,
    )

    assert ProviderName.OPENROUTER not in app.state.model_runtime_readiness._discoveries


async def test_application_passes_settings_openrouter_secret_to_role_factory(
    project_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from core.runtime_config import get_runtime_snapshot

    snapshot = get_runtime_snapshot()
    assert snapshot.config is not None
    config = snapshot.config
    provider = config.providers[ProviderName.OLLAMA].model_copy(
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
            ].text_tuning.model_copy(
                update={
                    "context_window": None,
                    "reasoning_effort": None,
                }
            ),
        }
    )
    custom = snapshot.model_copy(
        update={
            "config": config.model_copy(
                update={
                    "providers": {
                        **config.providers,
                        ProviderName.OPENROUTER: provider,
                    },
                    "roles": {
                        **config.roles,
                        AgentRole.INGREDIENT_EXTRACTOR: selection,
                    },
                }
            )
        }
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"detected":[],"warnings":[]}'}}]
            },
        )

    app = create_app(
        settings=Settings(
            _env_file=None,
            artifact_root=project_tmp_path,
            openrouter_api_key="settings-secret-canary",
        ),
        runtime_snapshot=custom,
        ollama_health=type("Ready", (), {"inspect": lambda self: _ready_ollama()})(),
        provider_discoveries={
            ProviderName.OPENROUTER: StaticDiscovery(
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
                                "structured_outputs",
                                "temperature",
                            ),
                            context_window=131072,
                        )
                    },
                )
            )
        },
    )
    app.state.structured_model_factory._transports[ProviderName.OPENROUTER] = (
        httpx.MockTransport(respond)
    )

    await app.state.structured_model_factory.build(
        AgentRole.INGREDIENT_EXTRACTOR,
        ExtractionResult,
    ).ainvoke([ModelMessage(role="user", content="Inspect.")])

    assert requests[0].headers["authorization"] == "Bearer settings-secret-canary"
    assert json.loads(requests[0].content)["model"] == "vendor/vision"
