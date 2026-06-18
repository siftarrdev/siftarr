"""Shared rule-engine loading and cache integration."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.rule import Rule
from app.siftarr.services.decisions.rule_engine import (
    RuleEngine,
    get_cached_engine,
    set_cached_engine,
)


async def get_rule_engine(db: AsyncSession, media_type: str) -> RuleEngine:
    """Return a cached rule engine, loading DB rules when the cache is stale."""
    cached = get_cached_engine(media_type)
    if cached is not None:
        return cached

    result = await db.execute(select(Rule))
    rules = list(result.scalars().all())
    engine = RuleEngine.from_db_rules(rules=rules, media_type=media_type)
    set_cached_engine(media_type, engine)
    return engine
