"""FastAPI application construction and lifecycle wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.middleware import RequestIdMiddleware
from api.routes import health, jobs
from core.config import Settings, get_settings
from core.errors import install_error_handlers
from orchestration.job_runner import JobRunner
from repositories.job_store import JobStore
from repositories.session_store import SessionStore
from services.artifacts import ArtifactStore
from services.concurrency import ModelCallLimiter
from services.ollama_health import OllamaHealthService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and reliably release runtime-owned resources."""
    await app.state.artifact_store.startup()
    try:
        yield
    finally:
        try:
            await app.state.job_runner.shutdown()
        finally:
            await app.state.artifact_store.shutdown()


def create_app(
    settings: Settings | None = None,
    ollama_health: OllamaHealthService | None = None,
) -> FastAPI:
    """Construct an application with replaceable external-service boundaries."""
    resolved_settings = settings or get_settings()
    job_store = JobStore(ttl_seconds=resolved_settings.session_ttl_seconds)
    session_store = SessionStore(ttl_seconds=resolved_settings.session_ttl_seconds)
    artifact_store = ArtifactStore(
        resolved_settings.artifact_root,
        ttl_seconds=resolved_settings.session_ttl_seconds,
    )
    job_runner = JobRunner(
        job_store,
        max_concurrent_jobs=resolved_settings.max_concurrent_jobs,
    )
    model_call_limiter = ModelCallLimiter(resolved_settings.max_concurrent_model_calls)
    resolved_ollama_health = ollama_health or OllamaHealthService(
        str(resolved_settings.ollama_base_url),
        timeout_seconds=resolved_settings.llm_timeout_seconds,
    )

    app = FastAPI(title="Cook Mantra API", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.job_store = job_store
    app.state.session_store = session_store
    app.state.artifact_store = artifact_store
    app.state.job_runner = job_runner
    app.state.model_call_limiter = model_call_limiter
    app.state.ollama_health = resolved_ollama_health

    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    return app
