"""Database configuration and session management."""

import sqlite3
from collections.abc import AsyncGenerator
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.siftarr.config import get_settings
from app.siftarr.models import Base

CURRENT_ALEMBIC_REVISION = "f03b57417775"
ALEMBIC_VERSION_TABLE = "alembic_version"


def _get_sync_sqlite_url(database_url: str) -> str:
    """Convert an async SQLite URL into the sync URL Alembic expects."""

    return database_url.replace("+aiosqlite", "")


def _get_sqlite_db_path(database_url: str) -> Path:
    """Resolve the SQLite database path from a SQLAlchemy URL."""

    sync_url = _get_sync_sqlite_url(database_url)
    prefix = "sqlite:///"
    if not sync_url.startswith(prefix):
        raise ValueError(f"unsupported SQLite URL: {database_url}")
    return Path(sync_url.removeprefix(prefix))


def _inspect_sqlite_database(db_path: Path) -> tuple[set[str], str | None]:
    """Read current table names and Alembic revision from a SQLite file."""

    if not db_path.exists():
        return set(), None

    with closing(sqlite3.connect(db_path)) as connection:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row[0] for row in table_rows}

        alembic_revision: str | None = None
        if ALEMBIC_VERSION_TABLE in table_names:
            revision_row = connection.execute(
                f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE} LIMIT 1"
            ).fetchone()
            if revision_row is not None:
                alembic_revision = str(revision_row[0])

    return table_names, alembic_revision


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
        future=True,
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
        finally:
            await session.close()


def _stamp_alembic_head(sync_url: str) -> None:
    """Write the current Alembic revision directly into the version table.

    Uses raw SQL to avoid triggering Alembic's ``fileConfig()`` which
    destroys the root logger configuration (and breaks caplog in tests).
    The ``alembic_version`` table is an Alembic internal table that is
    not part of ``Base.metadata``, so it must be created explicitly.
    """
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {ALEMBIC_VERSION_TABLE} "
                    f"(version_num VARCHAR(32) NOT NULL)"
                )
            )
            conn.execute(text(f"DELETE FROM {ALEMBIC_VERSION_TABLE}"))
            conn.execute(
                text(f"INSERT INTO {ALEMBIC_VERSION_TABLE} (version_num) VALUES (:rev)"),
                {"rev": CURRENT_ALEMBIC_REVISION},
            )
    finally:
        engine.dispose()


async def init_db() -> None:
    """Ensure database schema is current at startup.

    Uses ``Base.metadata.create_all()`` which is idempotent — it creates
    missing tables and leaves existing ones untouched. After the schema is
    current the Alembic version table is stamped so future migrations can
    detect the starting point.
    """
    init_engine()

    database_url = get_settings().database_url
    if not database_url.startswith("sqlite"):
        return

    sync_url = _get_sync_sqlite_url(database_url)
    sync_engine = create_engine(sync_url)
    try:
        with sync_engine.begin() as conn:
            Base.metadata.create_all(conn)

        db_path = _get_sqlite_db_path(database_url)
        table_names, alembic_revision = _inspect_sqlite_database(db_path)

        if alembic_revision != CURRENT_ALEMBIC_REVISION:
            _stamp_alembic_head(sync_url)
    finally:
        sync_engine.dispose()
