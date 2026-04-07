"""FastAPI application for Arbitratarr."""

import sqlite3
import subprocess
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
INITIAL_MIGRATION_REVISION = "bc9c8cfbe08b"


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


def _get_sqlite_db_path() -> Path | None:
    """Return the SQLite database path when using SQLite."""
    settings = get_settings()
    parsed = urlparse(settings.database_url)
    if not parsed.scheme.startswith("sqlite"):
        return None

    path = parsed.path
    if path.startswith("/."):
        path = path[1:]
    return Path(path)


def _prepare_legacy_sqlite_database_for_migrations() -> None:
    """Stamp legacy SQLite databases so Alembic can upgrade them safely."""
    db_path = _get_sqlite_db_path()
    if db_path is None or not db_path.exists():
        return

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        table_names = {row[0] for row in cursor.fetchall()}
        has_alembic_version_table = "alembic_version" in table_names
        alembic_versions: list[str] = []
        if has_alembic_version_table:
            version_cursor = connection.execute("SELECT version_num FROM alembic_version")
            alembic_versions = [row[0] for row in version_cursor.fetchall() if row[0]]

    if not table_names:
        return

    app_tables = {
        "pending_queue",
        "releases",
        "requests",
        "rules",
        "settings",
        "staged_torrents",
    }
    if not table_names.intersection(app_tables):
        return

    if alembic_versions and alembic_versions[0] == INITIAL_MIGRATION_REVISION:
        return

    if alembic_versions:
        return

    subprocess.run(
        ["uv", "run", "alembic", "stamp", INITIAL_MIGRATION_REVISION],
        check=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    global scheduler_service

    # Ensure database directory exists
    _ensure_db_directory()

    # Apply migrations before table initialization so the runtime schema stays current
    _prepare_legacy_sqlite_database_for_migrations()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

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
