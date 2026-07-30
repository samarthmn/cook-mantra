"""FastAPI dependency accessors for application-owned services."""

from fastapi import Request

from core.config import Settings
from orchestration.graphs.complete_recipes import CompleteRecipesRunner
from orchestration.graphs.ingredient_extraction import IngredientExtractionRunner
from orchestration.graphs.recipe_options import RecipeOptionsRunner
from orchestration.job_runner import JobRunner
from repositories.job_store import JobStore
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.concurrency import ModelCallLimiter
from services.dish_previews import DishPreviewService
from services.ollama_health import OllamaHealthService
from services.uploads import ImageUploadValidator


def get_settings(request: Request) -> Settings:
    """Return the validated settings owned by this application."""
    return request.app.state.settings


def get_job_store(request: Request) -> JobStore:
    """Return the in-memory job repository."""
    return request.app.state.job_store


def get_session_store(request: Request) -> SessionStore:
    """Return the in-memory session repository."""
    return request.app.state.session_store


def get_artifact_store(request: Request) -> ArtifactStore:
    """Return the temporary artifact store."""
    return request.app.state.artifact_store


def get_job_runner(request: Request) -> JobRunner:
    """Return the in-process background job runner."""
    return request.app.state.job_runner


def get_upload_validator(request: Request) -> ImageUploadValidator:
    """Return the configured ingredient-image validator."""
    return request.app.state.upload_validator


def get_ingredient_extraction_runner(
    request: Request,
) -> IngredientExtractionRunner:
    """Return the extraction runner bound to this application's stores."""
    return request.app.state.ingredient_extraction_runner


def get_recipe_options_runner(request: Request) -> RecipeOptionsRunner:
    """Return the recipe-option runner bound to this application's stores."""
    return request.app.state.recipe_options_runner


def get_complete_recipes_runner(request: Request) -> CompleteRecipesRunner:
    """Return the complete-recipe runner bound to this application's stores."""
    return request.app.state.complete_recipes_runner


def get_dish_preview_service(request: Request) -> DishPreviewService:
    """Return the dish-preview service owned by this application."""
    return request.app.state.dish_preview_service


def get_model_call_limiter(request: Request) -> ModelCallLimiter:
    """Return the shared model-call concurrency limit."""
    return request.app.state.model_call_limiter


def get_ollama_health(request: Request) -> OllamaHealthService:
    """Return the Ollama readiness service."""
    return request.app.state.ollama_health
