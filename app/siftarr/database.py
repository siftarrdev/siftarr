"""Database configuration and session management."""

from collections.abc import AsyncGenerator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.siftarr.config import get_settings

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _get_sync_sqlite_url(database_url: str) -> str:
    """Convert an async SQLite URL into the sync URL Alembic expects."""

    return database_url.replace("+aiosqlite", "")


# Module-level sentinels — initialized lazily by init_engine().
engine: AsyncEngine | None = None
_IS_SQLITE: bool = False
async_session_maker: async_sessionmaker[AsyncSession] | None = None
_engine_initialized: bool = False


def init_engine() -> None:
    """Create the async engine, configure SQLite pragmas, and build the session factory.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global engine, _IS_SQLITE, async_session_maker, _engine_initialized

    if _engine_initialized:
        return

    settings = get_settings()

    engine = create_async_engine(
        settings.database_url,
        echo=False,
    )

    # Enable SQLite WAL mode and busy timeout for better concurrency.
    # WAL allows concurrent reads during writes; busy_timeout makes writers
    # wait for locks instead of immediately raising "database is locked".
    _IS_SQLITE = settings.database_url.startswith("sqlite")

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        """Set SQLite pragmas on every new connection."""
        if not _IS_SQLITE:
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    _engine_initialized = True


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependency that provides a database session."""
    if async_session_maker is None:
        init_engine()
    assert async_session_maker is not None  # Help type checker narrow after lazy init
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_alembic_head_revision() -> str:
    """Return the repository's current Alembic head revision."""

    config = Config(str(ALEMBIC_INI_PATH))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic head revision is not defined")
    return head


def _run_alembic_upgrade_head(sync_url: str) -> None:
    """Apply all pending Alembic migrations to the configured database."""

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(config, "head")


async def init_db() -> None:
    """Ensure database schema is current at startup.

    SQLite deployments use Alembic migrations so production databases are
    upgraded incrementally instead of being stamped over old schemas.
    """
    init_engine()

    database_url = get_settings().database_url
    if not database_url.startswith("sqlite"):
        return

    sync_url = _get_sync_sqlite_url(database_url)
    _run_alembic_upgrade_head(sync_url)
