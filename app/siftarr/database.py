"""Database configuration and session management."""

import sqlite3
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.siftarr.config import get_settings
from app.siftarr.models import Base

CURRENT_ALEMBIC_REVISION = "057a73d850e6"
ALEMBIC_VERSION_TABLE = "alembic_version"
EXPECTED_SCHEMA_TABLES = frozenset(Base.metadata.tables)


class DatabaseRepairAction(StrEnum):
    """Approved repair actions for mounted SQLite databases."""

    NOOP = "noop"
    UPGRADE = "upgrade"
    STAMP_HEAD = "stamp_head"
    RESET = "reset"


@dataclass(frozen=True)
class DatabaseRepairPlan:
    """Decision produced by the SQLite repair audit."""

    action: DatabaseRepairAction
    reason: str


@dataclass(frozen=True)
class SQLiteTableSignature:
    """Normalized SQLite table structure used for drift detection."""

    columns: tuple[tuple[str, str, bool, int], ...]
    indexes: frozenset[tuple[bool, tuple[str, ...]]]
    foreign_keys: frozenset[tuple[tuple[str, ...], str, tuple[str, ...]]]


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

    with sqlite3.connect(db_path) as connection:
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


def _get_user_table_names(table_names: set[str] | frozenset[str]) -> set[str]:
    """Return non-system, non-Alembic table names."""

    return {
        table_name
        for table_name in table_names
        if table_name != ALEMBIC_VERSION_TABLE and not table_name.startswith("sqlite_")
    }


def _quote_sqlite_identifier(identifier: str) -> str:
    """Quote a SQLite identifier for pragma statements."""

    return f'"{identifier.replace('"', '""')}"'


def _normalize_sqlite_type(type_name: str | None) -> str:
    """Normalize SQLite type strings for structure comparison."""

    if not type_name:
        return ""
    return " ".join(type_name.upper().split())


def _collect_sqlite_table_signature(
    connection: sqlite3.Connection,
    table_name: str,
) -> SQLiteTableSignature:
    """Collect normalized schema details for a single SQLite table."""

    quoted_table_name = _quote_sqlite_identifier(table_name)
    column_rows = connection.execute(f"PRAGMA table_info({quoted_table_name})").fetchall()
    columns = tuple(
        sorted(
            (str(row[1]), _normalize_sqlite_type(row[2]), bool(row[3]), int(row[5]))
            for row in column_rows
        )
    )

    indexes: set[tuple[bool, tuple[str, ...]]] = set()
    index_rows = connection.execute(f"PRAGMA index_list({quoted_table_name})").fetchall()
    for index_row in index_rows:
        index_name = str(index_row[1])
        unique = bool(index_row[2])
        quoted_index_name = _quote_sqlite_identifier(index_name)
        index_columns = connection.execute(f"PRAGMA index_info({quoted_index_name})").fetchall()
        indexes.add((unique, tuple(str(column_row[2]) for column_row in index_columns)))

    foreign_keys_by_id: dict[int, list[sqlite3.Row | tuple[object, ...]]] = {}
    foreign_key_rows = connection.execute(
        f"PRAGMA foreign_key_list({quoted_table_name})"
    ).fetchall()
    for foreign_key_row in foreign_key_rows:
        foreign_keys_by_id.setdefault(int(foreign_key_row[0]), []).append(foreign_key_row)

    foreign_keys = frozenset(
        (
            tuple(str(row[3]) for row in sorted(rows, key=lambda row: int(row[1]))),
            str(rows[0][2]),
            tuple(str(row[4]) for row in sorted(rows, key=lambda row: int(row[1]))),
        )
        for rows in foreign_keys_by_id.values()
    )

    return SQLiteTableSignature(
        columns=columns,
        indexes=frozenset(indexes),
        foreign_keys=foreign_keys,
    )


def _collect_sqlite_schema_signature(
    connection: sqlite3.Connection,
    *,
    table_names: set[str] | None = None,
) -> frozenset[tuple[str, SQLiteTableSignature]]:
    """Collect normalized schema details for all user tables."""

    resolved_table_names = table_names
    if resolved_table_names is None:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        resolved_table_names = {str(row[0]) for row in table_rows}

    user_tables = sorted(_get_user_table_names(resolved_table_names))
    return frozenset(
        (table_name, _collect_sqlite_table_signature(connection, table_name))
        for table_name in user_tables
    )


@lru_cache(maxsize=1)
def _get_expected_sqlite_schema_signature() -> frozenset[tuple[str, SQLiteTableSignature]]:
    """Build the normalized SQLite schema expected from current models."""

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            raw_connection = connection.connection.driver_connection
            if not isinstance(raw_connection, sqlite3.Connection):
                raise TypeError("expected sqlite3.Connection driver connection")
            return _collect_sqlite_schema_signature(raw_connection)
    finally:
        engine.dispose()


def _sqlite_schema_matches_expected(
    db_path: Path,
    table_names: set[str] | frozenset[str],
) -> bool:
    """Return whether the on-disk SQLite schema matches current models."""

    with sqlite3.connect(db_path) as connection:
        actual_signature = _collect_sqlite_schema_signature(
            connection, table_names=set(table_names)
        )
    return actual_signature == _get_expected_sqlite_schema_signature()


def _build_alembic_config(database_url: str) -> Config:
    """Build an Alembic config pointed at the requested SQLite database."""

    repo_root = Path(__file__).resolve().parents[2]
    config_path_candidates = (repo_root / "alembic.ini", repo_root / "db" / "alembic.ini")
    script_location_candidates = (repo_root / "alembic", repo_root / "db" / "alembic")

    config_path = next(
        (path for path in config_path_candidates if path.exists()), config_path_candidates[0]
    )
    script_location = next(
        (path for path in script_location_candidates if path.exists()),
        script_location_candidates[0],
    )

    config = Config(str(config_path))
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", _get_sync_sqlite_url(database_url))
    return config


def _delete_sqlite_database_files(db_path: Path) -> None:
    """Delete the SQLite database and sidecar files if present."""

    for path in (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ):
        if path.exists():
            path.unlink()


def _run_alembic_upgrade(database_url: str) -> None:
    """Run Alembic upgrade head for the configured database."""

    command.upgrade(_build_alembic_config(database_url), "head")


def _run_alembic_stamp_head(database_url: str) -> None:
    """Stamp the configured database to the current Alembic head."""

    command.stamp(_build_alembic_config(database_url), "head", purge=True)


def prepare_sqlite_database_for_startup(database_url: str | None = None) -> DatabaseRepairPlan:
    """Repair or migrate the SQLite database before app startup."""

    resolved_database_url = database_url or get_settings().database_url
    db_path = _get_sqlite_db_path(resolved_database_url)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        table_names, alembic_revision = _inspect_sqlite_database(db_path)
        schema_matches_expected: bool | None = None
        if _get_user_table_names(table_names) == EXPECTED_SCHEMA_TABLES:
            schema_matches_expected = _sqlite_schema_matches_expected(db_path, table_names)
        plan = determine_sqlite_repair_plan(
            table_names=table_names,
            alembic_revision=alembic_revision,
            schema_matches_expected=schema_matches_expected,
        )
    except sqlite3.DatabaseError as exc:
        plan = DatabaseRepairPlan(
            action=DatabaseRepairAction.RESET,
            reason=f"database file is unreadable or corrupt: {exc}",
        )

    log_prefix = f"[siftarr-db] path={db_path}"

    if plan.action == DatabaseRepairAction.NOOP:
        print(f"{log_prefix} action=noop reason={plan.reason}", flush=True)
        return plan

    if plan.action == DatabaseRepairAction.UPGRADE:
        _run_alembic_upgrade(resolved_database_url)
        print(f"{log_prefix} action=migrated reason={plan.reason}", flush=True)
        return plan

    if plan.action == DatabaseRepairAction.STAMP_HEAD:
        _run_alembic_stamp_head(resolved_database_url)
        print(f"{log_prefix} action=stamped reason={plan.reason}", flush=True)
        return plan

    print(f"{log_prefix} action=reset reason={plan.reason}", flush=True)
    _delete_sqlite_database_files(db_path)
    _run_alembic_upgrade(resolved_database_url)
    print(
        f"{log_prefix} action=migrated reason=database recreated at current schema",
        flush=True,
    )
    return plan


def determine_sqlite_repair_plan(
    *,
    table_names: set[str] | frozenset[str],
    alembic_revision: str | None,
    schema_matches_expected: bool | None = None,
) -> DatabaseRepairPlan:
    """Classify the minimum safe repair path for a SQLite database."""

    user_tables = _get_user_table_names(table_names)

    if not user_tables:
        if alembic_revision == CURRENT_ALEMBIC_REVISION:
            return DatabaseRepairPlan(
                action=DatabaseRepairAction.RESET,
                reason="revision claims head but required tables are missing",
            )
        if alembic_revision is not None:
            return DatabaseRepairPlan(
                action=DatabaseRepairAction.RESET,
                reason="alembic history exists without schema tables; reset broken migration state",
            )
        return DatabaseRepairPlan(
            action=DatabaseRepairAction.UPGRADE,
            reason="fresh database should run the single init migration",
        )

    if user_tables == EXPECTED_SCHEMA_TABLES:
        if schema_matches_expected is False:
            return DatabaseRepairPlan(
                action=DatabaseRepairAction.RESET,
                reason="schema tables exist but definitions drift from the current init schema",
            )
        if alembic_revision == CURRENT_ALEMBIC_REVISION:
            return DatabaseRepairPlan(
                action=DatabaseRepairAction.NOOP,
                reason="schema already matches the current single init revision",
            )
        return DatabaseRepairPlan(
            action=DatabaseRepairAction.STAMP_HEAD,
            reason="schema matches head but alembic history is missing or stale",
        )

    return DatabaseRepairPlan(
        action=DatabaseRepairAction.RESET,
        reason="partial or unknown schema drift is not safe to repair in place",
    )


settings = get_settings()

# Create async engine
engine: AsyncEngine = create_async_engine(
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


# Create async session factory
async_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Verify database readiness before startup work begins."""

    database_url = get_settings().database_url
    if not database_url.startswith("sqlite"):
        return

    db_path = _get_sqlite_db_path(database_url)

    try:
        table_names, alembic_revision = _inspect_sqlite_database(db_path)
        schema_matches_expected: bool | None = None
        if _get_user_table_names(table_names) == EXPECTED_SCHEMA_TABLES:
            schema_matches_expected = _sqlite_schema_matches_expected(db_path, table_names)
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(
            f"Database startup verification failed: database file is unreadable or corrupt: {exc}"
        ) from exc

    plan = determine_sqlite_repair_plan(
        table_names=table_names,
        alembic_revision=alembic_revision,
        schema_matches_expected=schema_matches_expected,
    )
    if plan.action in {
        DatabaseRepairAction.NOOP,
        DatabaseRepairAction.STAMP_HEAD,
    }:
        return

    raise RuntimeError(
        f"Database startup verification failed: action={plan.action.value} reason={plan.reason}"
    )
