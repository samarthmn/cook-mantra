"""FastAPI application construction and lifecycle wiring."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.ingredient_extraction import IngredientExtractor, OllamaIngredientExtractor
from agents.master_chef import MasterChef, OllamaMasterChef
from agents.nutrition import NutritionAgent, OllamaNutritionAgent
from agents.specialized_recipe import (
    OllamaSpecializedRecipeAgent,
    SpecializedRecipeAgent,
)
from api.middleware import (
    RequestBodyLimitMiddleware,
    RequestIdMiddleware,
    UnexpectedErrorMiddleware,
)
from api.routes import artifacts, health, jobs, recipe_options, recipes, sessions
from core.config import Model, Settings, get_settings
from core.errors import install_error_handlers
from core.logging import configure_logging
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
from services.image_generation import ImageGenerator, OllamaImageGenerator
from services.ollama_health import OllamaHealthService
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
                owned_image_generator = app.state.owned_image_generator
                if owned_image_generator is not None:
                    await owned_image_generator.aclose()
            finally:
                await app.state.artifact_store.shutdown()


def create_app(
    settings: Settings | None = None,
    ollama_health: OllamaHealthService | None = None,
    ingredient_extractor: IngredientExtractor | None = None,
    master_chef: MasterChef | None = None,
    nutrition_agent: NutritionAgent | None = None,
    dish_previews: DishPreviewService | None = None,
    image_generator: ImageGenerator | None = None,
    image_client: httpx.AsyncClient | None = None,
    specialized_recipe_agent: SpecializedRecipeAgent | None = None,
    tracing_service: TracingService | None = None,
) -> FastAPI:
    """Construct an application with replaceable external-service boundaries."""
    resolved_settings = settings or get_settings()
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
    resolved_ingredient_extractor = ingredient_extractor or OllamaIngredientExtractor(
        settings=resolved_settings,
        tracing=resolved_tracing_service,
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
        else OllamaMasterChef(
            settings=resolved_settings,
            tracing=resolved_tracing_service,
        )
    )
    resolved_nutrition_agent = (
        nutrition_agent
        if nutrition_agent is not None
        else OllamaNutritionAgent(
            settings=resolved_settings,
            tracing=resolved_tracing_service,
        )
    )
    resolved_image_generator = image_generator
    owned_image_generator: OllamaImageGenerator | None = None
    if dish_previews is None:
        if resolved_image_generator is None:
            owned_image_generator = OllamaImageGenerator(
                base_url=str(resolved_settings.image_base_url()),
                model=Model.Z_IMAGE,
                client=image_client,
                timeout_seconds=resolved_settings.image_timeout_seconds,
            )
            resolved_image_generator = owned_image_generator
        resolved_dish_previews = DishPreviewService(
            resolved_image_generator,
            artifact_store,
            resolved_settings,
        )
    else:
        resolved_dish_previews = dish_previews
    recipe_options_dependencies = RecipeOptionDependencies(
        master_chef=resolved_master_chef,
        nutrition_agent=resolved_nutrition_agent,
        dish_previews=resolved_dish_previews,
        session_store=session_store,
        model_call_limiter=model_call_limiter,
        dish_previews_enabled=resolved_settings.dish_previews_enabled,
    )
    recipe_options_runner = build_recipe_options_runner(
        recipe_options_dependencies,
    )
    resolved_specialized_recipe_agent = (
        specialized_recipe_agent
        if specialized_recipe_agent is not None
        else OllamaSpecializedRecipeAgent(
            settings=resolved_settings,
            tracing=resolved_tracing_service,
        )
    )
    complete_recipes_dependencies = CompleteRecipeDependencies(
        agent=resolved_specialized_recipe_agent,
        session_store=session_store,
        model_call_limiter=model_call_limiter,
    )
    complete_recipes_runner = build_complete_recipes_runner(
        complete_recipes_dependencies
    )
    resolved_ollama_health = ollama_health or OllamaHealthService(
        str(resolved_settings.ollama_base_url),
        image_base_url=str(resolved_settings.image_base_url()),
        timeout_seconds=resolved_settings.llm_timeout_seconds,
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
    app.state.ingredient_extractor = resolved_ingredient_extractor
    app.state.ingredient_extraction_runner = ingredient_extraction_runner
    app.state.image_generator = resolved_image_generator
    app.state.owned_image_generator = owned_image_generator
    app.state.dish_preview_service = resolved_dish_previews
    app.state.recipe_options_dependencies = recipe_options_dependencies
    app.state.recipe_options_runner = recipe_options_runner
    app.state.specialized_recipe_agent = resolved_specialized_recipe_agent
    app.state.complete_recipes_dependencies = complete_recipes_dependencies
    app.state.complete_recipes_runner = complete_recipes_runner
    app.state.ollama_health = resolved_ollama_health

    install_error_handlers(app)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_upload_bytes=resolved_settings.max_upload_bytes,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(recipe_options.router, prefix="/api/v1")
    app.include_router(recipes.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")
    return app
