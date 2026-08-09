"""FastAPI application construction and lifecycle wiring."""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import SecretStr

from agents.ingredient_extraction import IngredientExtractionAgent, IngredientExtractor
from agents.master_chef import MasterChef, MasterChefAgent
from agents.specialized_recipe import (
    RecipeWriterAgent,
    SpecializedRecipeAgent,
)
from api.middleware import (
    RequestBodyLimitMiddleware,
    RequestIdMiddleware,
    RuntimeRevisionMiddleware,
    UnexpectedErrorMiddleware,
)
from api.routes import artifacts, health, jobs, recipe_options, recipes, sessions
from core.config import Settings, get_settings
from core.errors import install_error_handlers
from core.logging import configure_logging
from core.runtime_config import (
    RuntimeConfigurationSnapshot,
    get_runtime_snapshot,
)
from domain.model_runtime import (
    IMAGE_PROVIDER_NAMES,
    AgentRole,
    ImageTuning,
    ProviderErrorKind,
    ProviderName,
)
from orchestration.graphs.complete_recipes import (
    CompleteRecipeDependencies,
    build_complete_recipes_runner,
)
from orchestration.graphs.ingredient_extraction import (
    ExtractionDependencies,
    build_ingredient_extraction_runner,
)
from orchestration.graphs.recipe_options import (
    RecipeOptionDependencies,
    build_recipe_options_runner,
)
from orchestration.job_runner import JobRunner
from repositories.job_store import JobStore
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.cleanup import CleanupSupervisor
from services.concurrency import ModelCallLimiter
from services.dish_previews import DishPreviewService
from services.image_generation import (
    ImageGenerator,
    ImageProviderRuntime,
    RuntimeImageGenerator,
)
from services.model_runtime import (
    InProcessModelUsageJournal,
    ModelRuntimeReadinessService,
    ProviderDiscovery,
    RuntimeStructuredModelFactory,
    StructuredModelFactory,
    UnavailableModelDiscovery,
    media_endpoint_supported,
)
from services.ollama_health import OllamaHealthService
from services.providers.codex import CodexOwner
from services.providers.codex_app_server import (
    AppServerProcessFactory,
    CodexAppServerClient,
)
from services.providers.ollama import OllamaModelDiscovery
from services.providers.ollama_images import OllamaImageRuntime
from services.providers.openrouter import OpenRouterModelDiscovery
from services.providers.openrouter_images import OpenRouterImageRuntime
from services.tracing import TracingService
from services.uploads import ImageUploadValidator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and reliably release runtime-owned resources."""
    try:
        await app.state.artifact_store.startup()
        await app.state.cleanup_supervisor.startup()
        app.state.cleanup_task = getattr(app.state.cleanup_supervisor, "task", None)
    except BaseException:
        await _drain_runtime_shutdown(app)
        raise

    try:
        yield
    finally:
        await _drain_runtime_shutdown(app)


async def _drain_runtime_shutdown(app: FastAPI) -> None:
    """Finish every owned shutdown step before propagating cancellation."""
    shutdown_task = asyncio.create_task(_shutdown_runtime_resources(app))
    cancellation: asyncio.CancelledError | None = None
    while not shutdown_task.done():
        try:
            await asyncio.shield(shutdown_task)
        except asyncio.CancelledError as error:
            cancellation = error
            continue
    shutdown_task.result()
    if cancellation is not None:
        raise cancellation


async def _shutdown_runtime_resources(app: FastAPI) -> None:
    """Release runtime resources in dependency order."""
    try:
        await app.state.cleanup_supervisor.shutdown()
    finally:
        try:
            await app.state.job_runner.shutdown()
        finally:
            try:
                codex_client = app.state.codex_app_server_client
                if codex_client is not None:
                    await codex_client.aclose()
            finally:
                await app.state.artifact_store.shutdown()


def create_app(
    settings: Settings | None = None,
    ollama_health: OllamaHealthService | None = None,
    ingredient_extractor: IngredientExtractor | None = None,
    master_chef: MasterChef | None = None,
    dish_previews: DishPreviewService | None = None,
    image_generator: ImageGenerator | None = None,
    image_runtimes: Mapping[ProviderName, ImageProviderRuntime] | None = None,
    specialized_recipe_agent: SpecializedRecipeAgent | None = None,
    tracing_service: TracingService | None = None,
    runtime_snapshot: RuntimeConfigurationSnapshot | None = None,
    structured_model_factory: StructuredModelFactory | None = None,
    provider_discoveries: Mapping[ProviderName, ProviderDiscovery] | None = None,
    codex_process_factory: AppServerProcessFactory | None = None,
    codex_owner: CodexOwner | None = None,
) -> FastAPI:
    """Construct an application with replaceable external-service boundaries."""
    resolved_settings = settings or get_settings()
    resolved_runtime_snapshot = runtime_snapshot or get_runtime_snapshot()
    runtime_config = resolved_runtime_snapshot.config
    image_selection = (
        runtime_config.roles[AgentRole.IMAGE_GENERATOR]
        if runtime_config is not None
        else None
    )
    configure_logging(resolved_settings.log_level)
    job_store = JobStore(ttl_seconds=resolved_settings.session_ttl_seconds)
    session_store = SessionStore(ttl_seconds=resolved_settings.session_ttl_seconds)
    artifact_store = ArtifactStore(
        resolved_settings.artifact_root,
        ttl_seconds=resolved_settings.session_ttl_seconds,
    )
    cleanup_supervisor = CleanupSupervisor(
        session_store,
        job_store,
        artifact_store,
        interval_seconds=resolved_settings.cleanup_interval_seconds,
    )
    job_runner = JobRunner(
        job_store,
        max_concurrent_jobs=resolved_settings.max_concurrent_jobs,
        max_queued_jobs=resolved_settings.max_queued_jobs,
    )
    model_call_limiter = ModelCallLimiter(resolved_settings.max_concurrent_model_calls)
    upload_validator = ImageUploadValidator(resolved_settings.max_upload_bytes)
    resolved_tracing_service = (
        tracing_service
        if tracing_service is not None
        else TracingService(resolved_settings)
    )
    runtime_environ = dict(os.environ)
    if (
        runtime_config is not None
        and ProviderName.OPENROUTER in runtime_config.providers
        and resolved_settings.openrouter_api_key is not None
    ):
        secret_name = runtime_config.providers[ProviderName.OPENROUTER].api_key_env
        if secret_name == "OPENROUTER_API_KEY":
            runtime_environ.setdefault(
                secret_name,
                resolved_settings.openrouter_api_key.get_secret_value(),
            )
    resolved_discoveries = dict(provider_discoveries or {})
    codex_models = (
        {
            selection.model
            for role, selection in runtime_config.roles.items()
            if role is not AgentRole.IMAGE_GENERATOR
            and selection.enabled
            and selection.provider is ProviderName.CODEX
        }
        if runtime_config is not None
        else set()
    )
    codex_app_server_client: CodexOwner | None = None
    if codex_models:
        if codex_owner is not None:
            codex_app_server_client = codex_owner
        else:
            codex_definition = runtime_config.providers[ProviderName.CODEX]
            assert codex_definition.command is not None
            codex_app_server_client = CodexAppServerClient(
                command=codex_definition.command,
                configured_models=codex_models,
                sandbox_root=resolved_settings.artifact_root / "codex-app-server",
                process_factory=codex_process_factory,
                allow_unverified_tool_boundary=(
                    codex_definition.allow_unverified_tool_boundary
                ),
            )
        resolved_discoveries[ProviderName.CODEX] = codex_app_server_client
    model_usage_journal = InProcessModelUsageJournal()
    resolved_image_runtimes = {
        provider: runtime
        for provider, runtime in (image_runtimes or {}).items()
        if provider in IMAGE_PROVIDER_NAMES
    }
    if (
        image_runtimes is None
        and image_selection is not None
        and image_selection.enabled
        and image_selection.image_tuning is not None
    ):
        image_definition = runtime_config.providers[image_selection.provider]
        configured_capabilities = image_selection.capabilities or frozenset()
        if (
            image_selection.provider is ProviderName.OLLAMA
            and image_definition.endpoint is not None
        ):
            resolved_image_runtimes[ProviderName.OLLAMA] = OllamaImageRuntime(
                endpoint=str(image_definition.endpoint),
                model=image_selection.model,
                tuning=image_selection.image_tuning,
                configured_capabilities=configured_capabilities,
            )
        elif (
            image_selection.provider is ProviderName.OPENROUTER
            and image_definition.endpoint is not None
        ):
            api_key = SecretStr(
                runtime_environ.get(image_definition.api_key_env, "")
                if image_definition.api_key_env is not None
                else ""
            )
            resolved_image_runtimes[ProviderName.OPENROUTER] = OpenRouterImageRuntime(
                endpoint=str(image_definition.endpoint),
                api_key=api_key,
                model=image_selection.model,
                provider_tag=image_selection.provider_tag,
                tuning=image_selection.image_tuning,
                configured_capabilities=configured_capabilities,
            )
    resolved_model_factory = structured_model_factory or RuntimeStructuredModelFactory(
        resolved_runtime_snapshot,
        environ=runtime_environ,
        discoveries=resolved_discoveries,
        usage_journal=model_usage_journal,
        codex_client=codex_app_server_client,
    )
    resolved_ingredient_extractor = ingredient_extractor or IngredientExtractionAgent(
        settings=resolved_settings,
        tracing=resolved_tracing_service,
        editable_instruction=(
            runtime_config.roles[AgentRole.INGREDIENT_EXTRACTOR].editable_instruction
            if runtime_config is not None
            else ""
        ),
        model_factory=resolved_model_factory,
    )
    ingredient_extraction_runner = build_ingredient_extraction_runner(
        ExtractionDependencies(
            resolved_ingredient_extractor,
            session_store,
            artifact_store,
            model_call_limiter,
        )
    )
    resolved_master_chef = (
        master_chef
        if master_chef is not None
        else MasterChefAgent(
            settings=resolved_settings,
            tracing=resolved_tracing_service,
            editable_instruction=(
                runtime_config.roles[AgentRole.MASTER_CHEF].editable_instruction
                if runtime_config is not None
                else ""
            ),
            model_factory=resolved_model_factory,
        )
    )
    resolved_image_generator = (
        image_generator
        if image_generator is not None
        else RuntimeImageGenerator(
            resolved_runtime_snapshot,
            image_runtimes=resolved_image_runtimes,
            usage_journal=model_usage_journal,
        )
    )
    if dish_previews is None:
        resolved_dish_previews = DishPreviewService(
            resolved_image_generator,
            artifact_store,
            (
                image_selection.image_tuning
                if image_selection is not None
                and image_selection.image_tuning is not None
                else ImageTuning()
            ),
            editable_instruction=(
                image_selection.editable_instruction
                if image_selection is not None
                else ""
            ),
        )
    else:
        resolved_dish_previews = dish_previews
    recipe_options_dependencies = RecipeOptionDependencies(
        master_chef=resolved_master_chef,
        session_store=session_store,
        model_call_limiter=model_call_limiter,
    )
    recipe_options_runner = build_recipe_options_runner(
        recipe_options_dependencies,
    )
    resolved_specialized_recipe_agent = (
        specialized_recipe_agent
        if specialized_recipe_agent is not None
        else RecipeWriterAgent(
            settings=resolved_settings,
            tracing=resolved_tracing_service,
            editable_instruction=(
                runtime_config.roles[AgentRole.RECIPE_WRITER].editable_instruction
                if runtime_config is not None
                else ""
            ),
            model_factory=resolved_model_factory,
        )
    )
    complete_recipes_dependencies = CompleteRecipeDependencies(
        agent=resolved_specialized_recipe_agent,
        session_store=session_store,
        model_call_limiter=model_call_limiter,
        dish_previews=resolved_dish_previews,
        dish_previews_enabled=(
            image_selection is not None
            and image_selection.enabled
            and image_selection.provider in IMAGE_PROVIDER_NAMES
        ),
    )
    complete_recipes_runner = build_complete_recipes_runner(
        complete_recipes_dependencies
    )
    ollama_definition = (
        runtime_config.providers.get(ProviderName.OLLAMA)
        if runtime_config is not None
        else None
    )
    ollama_endpoint = (
        str(ollama_definition.endpoint)
        if ollama_definition is not None and ollama_definition.endpoint is not None
        else "http://127.0.0.1:11434"
    )
    resolved_ollama_health = ollama_health or OllamaHealthService(
        ollama_endpoint,
        timeout_seconds=resolved_settings.llm_timeout_seconds,
    )
    if runtime_config is not None:
        configured_models: dict[ProviderName, set[str]] = {}
        for role, selection in runtime_config.roles.items():
            if (
                selection.enabled
                and role is not AgentRole.IMAGE_GENERATOR
                and media_endpoint_supported(runtime_config, role, selection)
            ):
                configured_models.setdefault(selection.provider, set()).add(
                    selection.model
                )
        ollama_models = configured_models.get(ProviderName.OLLAMA)
        if (
            ollama_models
            and ProviderName.OLLAMA not in resolved_discoveries
            and ollama_health is None
            and ollama_definition is not None
            and ollama_definition.endpoint is not None
        ):
            resolved_discoveries[ProviderName.OLLAMA] = OllamaModelDiscovery(
                endpoint=str(ollama_definition.endpoint),
                configured_models=ollama_models,
                timeout_seconds=resolved_settings.llm_timeout_seconds,
                media_bearing=(
                    runtime_config.roles[AgentRole.INGREDIENT_EXTRACTOR].provider
                    is ProviderName.OLLAMA
                ),
            )
        openrouter_models = configured_models.get(ProviderName.OPENROUTER)
        if openrouter_models and ProviderName.OPENROUTER not in resolved_discoveries:
            definition = runtime_config.providers[ProviderName.OPENROUTER]
            api_key = (
                runtime_environ.get(definition.api_key_env, "")
                if definition.api_key_env is not None
                else ""
            )
            if api_key and definition.endpoint is not None:
                resolved_discoveries[ProviderName.OPENROUTER] = (
                    OpenRouterModelDiscovery(
                        endpoint=str(definition.endpoint),
                        api_key=api_key,
                        configured_models=openrouter_models,
                        timeout_seconds=resolved_settings.llm_timeout_seconds,
                        media_bearing=(
                            runtime_config.roles[
                                AgentRole.INGREDIENT_EXTRACTOR
                            ].provider
                            is ProviderName.OPENROUTER
                        ),
                    )
                )
            else:
                resolved_discoveries[ProviderName.OPENROUTER] = (
                    UnavailableModelDiscovery(
                        ProviderName.OPENROUTER,
                        openrouter_models,
                        ProviderErrorKind.AUTHENTICATION_FAILED,
                    )
                )
    model_runtime_readiness = ModelRuntimeReadinessService(
        resolved_runtime_snapshot,
        resolved_ollama_health,
        discoveries=resolved_discoveries,
        image_runtimes=resolved_image_runtimes,
    )

    app = FastAPI(
        title="Cook Mantra API",
        description="Local backend for Cook Mantra's image-to-recipe workflow.",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.job_store = job_store
    app.state.session_store = session_store
    app.state.artifact_store = artifact_store
    app.state.cleanup_supervisor = cleanup_supervisor
    app.state.cleanup_task = None
    app.state.job_runner = job_runner
    app.state.model_call_limiter = model_call_limiter
    app.state.upload_validator = upload_validator
    app.state.tracing_service = resolved_tracing_service
    app.state.structured_model_factory = resolved_model_factory
    app.state.model_usage_journal = model_usage_journal
    app.state.codex_app_server_client = codex_app_server_client
    app.state.ingredient_extractor = resolved_ingredient_extractor
    app.state.ingredient_extraction_runner = ingredient_extraction_runner
    app.state.image_generator = resolved_image_generator
    app.state.image_runtimes = resolved_image_runtimes
    app.state.dish_preview_service = resolved_dish_previews
    app.state.recipe_options_dependencies = recipe_options_dependencies
    app.state.recipe_options_runner = recipe_options_runner
    app.state.specialized_recipe_agent = resolved_specialized_recipe_agent
    app.state.complete_recipes_dependencies = complete_recipes_dependencies
    app.state.complete_recipes_runner = complete_recipes_runner
    app.state.ollama_health = resolved_ollama_health
    app.state.runtime_config_snapshot = resolved_runtime_snapshot
    app.state.model_runtime_readiness = model_runtime_readiness
    app.state.runtime_revision = secrets.token_urlsafe(32)

    install_error_handlers(app)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_upload_bytes=resolved_settings.max_upload_bytes,
    )
    app.add_middleware(
        RuntimeRevisionMiddleware,
        runtime_revision=app.state.runtime_revision,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=[
            "Content-Type",
            "X-Request-ID",
            "X-Cook-Mantra-Runtime-Revision",
        ],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(recipe_options.router, prefix="/api/v1")
    app.include_router(recipes.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")
    return app
