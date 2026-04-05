"""FastAPI application for Arbitratarr."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.arbitratarr.database import async_session_maker
from app.arbitratarr.routers import dashboard, rules, settings, webhooks
from app.arbitratarr.services.scheduler_service import SchedulerService

scheduler_service: SchedulerService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    global scheduler_service
    scheduler_service = SchedulerService(async_session_maker)
    scheduler_service.start()
    yield
    if scheduler_service:
        scheduler_service.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Arbitratarr",
        description="Media search and download decision middleware",
        version="0.1.0",
    )

    # Include routers
    app.include_router(dashboard.router)
    app.include_router(webhooks.router)
    app.include_router(rules.router)
    app.include_router(settings.router)

    @app.get("/")
    async def root() -> RedirectResponse:
        """Root endpoint redirecting to dashboard."""
        return RedirectResponse(url="/")

    @app.get("/health")
    async def health_check() -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse(content={"status": "ok"})

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Global exception handler for unhandled errors."""
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
