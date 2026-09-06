"""Shared rule-engine loading and cache integration."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import get_settings
from app.siftarr.models.rule import Rule
from app.siftarr.services.decisions.release_quality import ReleaseQualityPolicy
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
    settings = get_settings()
    engine.quality_policy = ReleaseQualityPolicy(
        media_type=media_type,
        file_count_bonus=getattr(settings, "release_file_count_bonus", 5),
        high_file_count_penalty=getattr(settings, "release_high_file_count_penalty", 10),
        archive_penalty=getattr(settings, "release_archive_penalty", 100),
        rar_title_penalty=getattr(settings, "release_rar_title_penalty", 50),
        low_seed_penalty=getattr(settings, "release_low_seed_penalty", 40),
        marginal_seed_penalty=getattr(settings, "release_marginal_seed_penalty", 10),
        episode_fallback_max_size_bytes=int(
            getattr(settings, "tv_episode_fallback_max_size_gb", 3.0) * 1024**3
        ),
    )
    set_cached_engine(media_type, engine)
    return engine
