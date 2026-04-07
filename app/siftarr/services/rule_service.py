from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.rule import Rule, RuleType


class RuleData(TypedDict):
    """Type definition for rule data dictionary."""

    name: str
    rule_type: RuleType
    media_scope: str
    pattern: str
    score: int
    priority: int
    description: str


DEFAULT_RULES: list[RuleData] = [
    {
        "name": "Reject Camera/TS/Screener",
        "rule_type": RuleType.EXCLUSION,
        "media_scope": "both",
        "pattern": r"CAM|TS|HDCAM|SCR",
        "score": 0,
        "priority": 1,
        "description": "Reject low-quality camera recordings and screeners",
    },
    {
        "name": "Require HD Resolution",
        "rule_type": RuleType.REQUIREMENT,
        "media_scope": "both",
        "pattern": r"1080p|2160p|720p",
        "score": 0,
        "priority": 2,
        "description": "Require HD resolution (720p or higher)",
    },
    {
        "name": "Prefer x265/HEVC",
        "rule_type": RuleType.SCORER,
        "media_scope": "both",
        "pattern": r"x265|HEVC|H\.265",
        "score": 50,
        "priority": 3,
        "description": "Prefer HEVC codec for better compression",
    },
    {
        "name": "Prefer MeGusta",
        "rule_type": RuleType.SCORER,
        "media_scope": "tv",
        "pattern": r"MeGusta",
        "score": 100,
        "priority": 4,
        "description": "Preferred release group",
    },
    {
        "name": "Prefer LAMA/SPiCYLAMA",
        "rule_type": RuleType.SCORER,
        "media_scope": "movie",
        "pattern": r"SPiCYLAMA|LAMA",
        "score": 100,
        "priority": 5,
        "description": "Preferred release groups",
    },
]


SIZE_LIMIT_RULE_NAME = "Size Limits"


class RuleService:
    """Service for managing rules in the database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all_rules(self) -> list[Rule]:
        """Get all rules ordered by priority."""
        result = await self.db.execute(select(Rule).order_by(Rule.priority, Rule.id))
        return list(result.scalars().all())

    async def get_rules_by_type(self, rule_type: RuleType) -> list[Rule]:
        """Get enabled rules filtered by type."""
        result = await self.db.execute(
            select(Rule)
            .where(Rule.rule_type == rule_type)
            .where(Rule.is_enabled.is_(True))
            .order_by(Rule.priority)
        )
        return list(result.scalars().all())

    async def get_all_rules_by_type(self, rule_type: RuleType) -> list[Rule]:
        """Get all rules (including disabled) filtered by type."""
        result = await self.db.execute(
            select(Rule).where(Rule.rule_type == rule_type).order_by(Rule.priority)
        )
        return list(result.scalars().all())

    async def get_exclusions(self) -> list[Rule]:
        """Get all enabled exclusion rules."""
        return await self.get_rules_by_type(RuleType.EXCLUSION)

    async def get_requirements(self) -> list[Rule]:
        """Get all enabled requirement rules."""
        return await self.get_rules_by_type(RuleType.REQUIREMENT)

    async def get_scorers(self) -> list[Rule]:
        """Get all enabled scorer rules."""
        return await self.get_rules_by_type(RuleType.SCORER)

    async def get_size_limits(self) -> list[Rule]:
        """Get all enabled size limit rules."""
        return await self.get_rules_by_type(RuleType.SIZE_LIMIT)

    async def get_rule_by_id(self, rule_id: int) -> Rule | None:
        """Get a single rule by ID."""
        result = await self.db.execute(select(Rule).where(Rule.id == rule_id))
        return result.scalar_one_or_none()

    async def create_rule(
        self,
        name: str,
        rule_type: RuleType,
        pattern: str,
        media_scope: str = "both",
        score: int = 0,
        min_size_gb: float | None = None,
        max_size_gb: float | None = None,
        priority: int = 0,
        is_enabled: bool = True,
        description: str | None = None,
    ) -> Rule:
        """Create a new rule."""
        rule = Rule(
            name=name,
            rule_type=rule_type,
            pattern=pattern,
            media_scope=media_scope,
            score=score,
            min_size_gb=min_size_gb,
            max_size_gb=max_size_gb,
            priority=priority,
            is_enabled=is_enabled,
            description=description,
        )
        self.db.add(rule)
        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def update_rule(
        self,
        rule_id: int,
        name: str | None = None,
        media_scope: str | None = None,
        pattern: str | None = None,
        score: int | None = None,
        min_size_gb: float | None = None,
        max_size_gb: float | None = None,
        priority: int | None = None,
        is_enabled: bool | None = None,
        description: str | None = None,
    ) -> Rule | None:
        """Update an existing rule."""
        rule = await self.get_rule_by_id(rule_id)
        if not rule:
            return None

        if name is not None:
            rule.name = name
        if media_scope is not None:
            rule.media_scope = media_scope
        if pattern is not None:
            rule.pattern = pattern
        if score is not None:
            rule.score = score
        rule.min_size_gb = min_size_gb
        rule.max_size_gb = max_size_gb
        if priority is not None:
            rule.priority = priority
        if is_enabled is not None:
            rule.is_enabled = is_enabled
        if description is not None:
            rule.description = description

        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def delete_rule(self, rule_id: int) -> bool:
        """Delete a rule."""
        rule = await self.get_rule_by_id(rule_id)
        if not rule:
            return False

        await self.db.delete(rule)
        await self.db.commit()
        return True

    async def seed_default_rules(self) -> list[Rule]:
        """Seed default rules if no rules exist."""
        existing = await self.get_all_rules()
        if existing:
            return existing

        rules = []
        for rule_data in DEFAULT_RULES:
            rule = await self.create_rule(**rule_data)
            rules.append(rule)

        return rules

    async def ensure_default_rules(self) -> list[Rule]:
        """Ensure default rules exist and backfill media scope on legacy rows."""
        rules = await self.get_all_rules()
        if not rules:
            return await self.seed_default_rules()

        changed = False
        for rule in rules:
            if not getattr(rule, "media_scope", None):
                rule.media_scope = "both"
                changed = True

        if changed:
            await self.db.commit()
            for rule in rules:
                await self.db.refresh(rule)

        return rules

    async def toggle_rule(self, rule_id: int) -> Rule | None:
        """Toggle a rule's enabled status."""
        rule = await self.get_rule_by_id(rule_id)
        if not rule:
            return None

        rule.is_enabled = not rule.is_enabled
        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def get_all_size_limit_rules(self) -> list[Rule]:
        """Get all size limit rules, including disabled ones."""
        return await self.get_all_rules_by_type(RuleType.SIZE_LIMIT)

    async def get_size_limit_rule_by_scope(self, media_scope: str) -> Rule | None:
        """Get a size limit rule for the given media scope."""
        result = await self.db.execute(
            select(Rule)
            .where(Rule.rule_type == RuleType.SIZE_LIMIT)
            .where(Rule.media_scope == media_scope)
        )
        return result.scalar_one_or_none()

    async def upsert_size_limit_rule(
        self,
        media_scope: str,
        min_size_gb: float | None,
        max_size_gb: float | None,
        is_enabled: bool = True,
    ) -> Rule:
        """Create or update a size limit rule for a media scope."""
        rule = await self.get_size_limit_rule_by_scope(media_scope)
        description_bits = []
        if min_size_gb is not None:
            description_bits.append(f"min {min_size_gb} GB")
        if max_size_gb is not None:
            description_bits.append(f"max {max_size_gb} GB")
        description = ", ".join(description_bits) if description_bits else "No limits configured"

        if rule:
            rule.name = SIZE_LIMIT_RULE_NAME
            rule.pattern = "size_limit"
            rule.min_size_gb = min_size_gb
            rule.max_size_gb = max_size_gb
            rule.is_enabled = is_enabled
            rule.description = description
            await self.db.commit()
            await self.db.refresh(rule)
            return rule

        return await self.create_rule(
            name=SIZE_LIMIT_RULE_NAME,
            rule_type=RuleType.SIZE_LIMIT,
            pattern="size_limit",
            media_scope=media_scope,
            score=0,
            min_size_gb=min_size_gb,
            max_size_gb=max_size_gb,
            is_enabled=is_enabled,
            description=description,
        )
