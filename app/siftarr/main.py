"""FastAPI application for Siftarr."""

import asyncio
import inspect
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.siftarr import database as db_mod
from app.siftarr.config import get_settings
from app.siftarr.routers import (
    auth_router,
    dashboard,
    dashboard_actions,
    dashboard_api,
    rules,
    search_sse,
    settings,
    staged,
    stats,
    webhooks,
)
from app.siftarr.services.admin.scheduler_service import SchedulerService
from app.siftarr.services.admin.settings_service import SettingsStore
from app.siftarr.services.auth_service import (
    BrowserAuthRequired,
    InitialPlexSyncRequired,
    build_initial_plex_sync_redirect_url,
    build_login_redirect_url,
    require_auth,
)
from app.siftarr.services.decisions.rule_service import RuleService
from app.siftarr.services.utils.http_client import close_shared_client
from app.siftarr.version import __version__

scheduler_service: SchedulerService | None = None


def _launch_startup_catchup_syncs(service: SchedulerService, logger: logging.Logger) -> None:
    """Launch startup catch-up syncs when the service returns an awaitable."""
    startup_syncs = service.run_startup_catchup_syncs()
    if inspect.isawaitable(startup_syncs):
        asyncio.create_task(startup_syncs)
    else:
        logger.debug("Startup catch-up sync launch skipped; service returned no awaitable")


def _configure_logging() -> None:
    """Configure application logging with structured output."""
    log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(log_format, date_format))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if any(existing.get_name() == "siftarr" for existing in root_logger.handlers):
        return
    handler.set_name("siftarr")
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
    await db_mod.init_db()

    assert db_mod.async_session_maker is not None
    async with db_mod.async_session_maker() as session, session.begin():
        await SettingsStore(session).ensure_runtime_api_key()

    async with db_mod.async_session_maker() as session:
        await RuleService(session).ensure_default_rules()

    scheduler_service = SchedulerService(db_mod.async_session_maker, logger=logger)
    scheduler_service.start()
    _launch_startup_catchup_syncs(scheduler_service, logger)
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

    # Session middleware for Plex SSO login
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_settings().secret_key,
        max_age=86400 * 30,  # 30 days
        same_site="lax",
        https_only=False,  # Set to True in production behind HTTPS
    )

    # Auth router is included BEFORE the auth dependency so its endpoints
    # (login, plex auth, logout, me) are accessible without authentication.
    app.include_router(auth_router.router, tags=["auth"])

    # Authentication dependency applied to all other routers.
    # The root "/" and "/health" endpoints defined directly on the app are excluded.
    auth = [Depends(require_auth)]

    # Include routers with authentication
    app.include_router(dashboard.router, dependencies=auth)
    app.include_router(dashboard_api.router, dependencies=auth)
    app.include_router(dashboard_actions.router, dependencies=auth)
    app.include_router(webhooks.router, dependencies=auth)
    app.include_router(rules.router, dependencies=auth)
    app.include_router(search_sse.router, dependencies=auth)
    app.include_router(settings.router, dependencies=auth)
    app.include_router(stats.router, dependencies=auth)
    app.include_router(staged.router, dependencies=auth)

    @app.get("/", dependencies=auth)
    async def root() -> RedirectResponse:
        """Root endpoint redirecting to dashboard."""
        return RedirectResponse(url="/dashboard")

    @app.get("/login")
    async def login_redirect() -> RedirectResponse:
        """Redirect to the login page."""
        return RedirectResponse(url="/auth/login")

    @app.get("/health")
    async def health_check() -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse(content={"status": "ok"})

    @app.exception_handler(BrowserAuthRequired)
    async def browser_auth_redirect_handler(
        request: Request, exc: BrowserAuthRequired
    ) -> RedirectResponse:
        """Redirect unauthenticated browser requests to Plex login."""
        del exc
        return RedirectResponse(url=build_login_redirect_url(request), status_code=303)

    @app.exception_handler(InitialPlexSyncRequired)
    async def initial_plex_sync_redirect_handler(
        request: Request, exc: InitialPlexSyncRequired
    ) -> RedirectResponse:
        """Redirect gated first-claim browser requests to initial Plex sync."""
        del exc
        return RedirectResponse(url=build_initial_plex_sync_redirect_url(request), status_code=303)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Global exception handler for unhandled errors."""
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


_configure_logging()
app = create_app()
