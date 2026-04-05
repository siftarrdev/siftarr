"""Database configuration and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.arbitratarr.config import get_settings

settings = get_settings()

# Create async engine
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

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
    """Initialize the database by creating all tables."""
    from app.arbitratarr.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
