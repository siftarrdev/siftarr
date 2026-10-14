from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.siftarr.models import Base


def pytest_report_teststatus(report, config):
    if report.when == "call" and report.passed:
        return "passed", "", "PASSED"


@pytest.fixture(autouse=True)
def _clear_rule_engine_caches() -> None:
    """Clear rule engine caches before each test to avoid cross-test pollution."""
    from app.siftarr.services.decisions.rule_engine import clear_engine_caches

    clear_engine_caches()


@pytest.fixture
async def session_maker():
    """In-memory SQLite with the full schema; one shared connection across sessions."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(session_maker) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session
