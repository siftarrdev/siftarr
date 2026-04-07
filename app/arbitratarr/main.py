"""FastAPI application for Arbitratarr."""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.arbitratarr.config import get_settings
from app.arbitratarr.database import async_session_maker, init_db
from app.arbitratarr.routers import dashboard, rules, settings, staged, webhooks
from app.arbitratarr.services.scheduler_service import SchedulerService

scheduler_service: SchedulerService | None = None


def _ensure_db_directory():
    """Create database directory if it doesn't exist."""
    settings = get_settings()
    parsed = urlparse(settings.database_url)
    if parsed.scheme.startswith("sqlite"):
        # Handle SQLite URL path - strip leading slash for relative paths
        path = parsed.path
        if path.startswith("/."):
            path = path[1:]  # Convert /./data to ./data for relative path
        db_path = Path(path)
        db_dir = db_path.parent
        db_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    global scheduler_service

    # Ensure database directory exists
    _ensure_db_directory()

    # Initialize database tables
    await init_db()

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
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    # Include routers
    app.include_router(dashboard.router)
    app.include_router(webhooks.router)
    app.include_router(rules.router)
    app.include_router(settings.router)
    app.include_router(staged.router)

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
