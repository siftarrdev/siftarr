"""FastAPI application for Siftarr."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.siftarr.config import get_settings
from app.siftarr.database import async_session_maker, init_db
from app.siftarr.routers import (
    dashboard,
    dashboard_actions,
    dashboard_api,
    rules,
    settings,
    staged,
    webhooks,
)
from app.siftarr.services.http_client import close_shared_client
from app.siftarr.services.scheduler_service import SchedulerService
from app.siftarr.version import __version__

scheduler_service: SchedulerService | None = None


def _configure_logging() -> None:
    """Configure application logging with structured output."""
    log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(log_format, date_format))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


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

    logger = logging.getLogger(__name__)

    logger.info("Starting Siftarr v%s", __version__)

    settings = get_settings()
    if not settings.prowlarr_url:
        logger.warning(
            "Prowlarr URL not configured. Set PROWLARR_URL environment variable. "
            "Torrent search functionality will not work."
        )
    if not settings.prowlarr_api_key:
        logger.warning(
            "Prowlarr API key not configured. Set PROWLARR_API_KEY environment variable. "
            "Torrent search functionality will not work."
        )
    if not settings.overseerr_url:
        logger.warning(
            "Overseerr URL not configured. Set OVERSEERR_URL environment variable. "
            "Webhook functionality may be limited."
        )
    if not settings.overseerr_api_key:
        logger.warning(
            "Overseerr API key not configured. Set OVERSEERR_API_KEY environment variable. "
            "Webhook functionality may be limited."
        )
    if not settings.qbittorrent_url:
        logger.warning(
            "qBittorrent URL not configured. Set QBITTORRENT_URL environment variable. "
            "Download functionality will not work."
        )

    if settings.staging_mode_enabled:
        logger.info("Staging mode is ENABLED - torrents will be held for approval")
    else:
        logger.info("Staging mode is DISABLED - torrents will be sent directly to qBittorrent")

    # Ensure database directory exists
    _ensure_db_directory()

    # Verify database readiness before starting background work.
    await init_db()

    scheduler_service = SchedulerService(async_session_maker, logger=logger)
    scheduler_service.start()
    yield
    await close_shared_client()
    if scheduler_service:
        scheduler_service.stop()
        logger.info("Siftarr shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Siftarr",
        description="Media search and download decision middleware",
        version=__version__,
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    # Include routers
    app.include_router(dashboard.router)
    app.include_router(dashboard_api.router)
    app.include_router(dashboard_actions.router)
    app.include_router(webhooks.router)
    app.include_router(rules.router)
    app.include_router(settings.router)
    app.include_router(staged.router)

    @app.get("/")
    async def root() -> RedirectResponse:
        """Root endpoint redirecting to dashboard."""
        return RedirectResponse(url="/dashboard")

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
