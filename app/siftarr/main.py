"""FastAPI application for Siftarr."""

import logging
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.siftarr.config import get_settings
from app.siftarr.database import async_session_maker, init_db
from app.siftarr.routers import dashboard, rules, settings, staged, webhooks
from app.siftarr.services.scheduler_service import SchedulerService
from app.siftarr.version import __version__

scheduler_service: SchedulerService | None = None
INITIAL_MIGRATION_REVISION = "bc9c8cfbe08b"
LATEST_KNOWN_MIGRATION_REVISION = "add_rejection_reason_to_requests"


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


def _repair_missing_alembic_revision() -> None:
    """Reset a stale alembic_version entry to the latest known revision."""
    db_path = _get_sqlite_db_path()
    if db_path is None or not db_path.exists():
        return

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        table_names = {row[0] for row in cursor.fetchall()}
        if "alembic_version" not in table_names:
            return

        version_cursor = connection.execute("SELECT version_num FROM alembic_version")
        versions = [row[0] for row in version_cursor.fetchall() if row[0]]
        if not versions or versions[0] == LATEST_KNOWN_MIGRATION_REVISION:
            return

        connection.execute("DELETE FROM alembic_version")
        connection.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (LATEST_KNOWN_MIGRATION_REVISION,),
        )
        connection.commit()


def _ensure_request_rejection_reason_column() -> None:
    """Add requests.rejection_reason for databases that missed the migration."""
    db_path = _get_sqlite_db_path()
    if db_path is None or not db_path.exists():
        return

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
        )
        if cursor.fetchone() is None:
            return

        columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
        if "rejection_reason" in columns:
            return

        connection.execute("ALTER TABLE requests ADD COLUMN rejection_reason VARCHAR(500)")
        connection.commit()


def _ensure_staged_torrents_selection_source_column() -> None:
    """Add staged_torrents.selection_source for databases that missed the migration."""
    db_path = _get_sqlite_db_path()
    if db_path is None or not db_path.exists():
        return

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'staged_torrents'"
        )
        if cursor.fetchone() is None:
            return

        columns = {row[1] for row in connection.execute("PRAGMA table_info(staged_torrents)")}
        if "selection_source" in columns:
            return

        connection.execute("ALTER TABLE staged_torrents ADD COLUMN selection_source VARCHAR(20)")
        connection.execute(
            "UPDATE staged_torrents SET selection_source = 'rule' WHERE selection_source IS NULL"
        )
        connection.commit()


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

    # Apply migrations before table initialization so the runtime schema stays current
    _prepare_legacy_sqlite_database_for_migrations()
    try:
        subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 255:
            raise
        _repair_missing_alembic_revision()
        subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

    _ensure_request_rejection_reason_column()
    _ensure_staged_torrents_selection_source_column()

    # Initialize database tables
    await init_db()

    scheduler_service = SchedulerService(async_session_maker, logger=logger)
    scheduler_service.start()
    yield
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
