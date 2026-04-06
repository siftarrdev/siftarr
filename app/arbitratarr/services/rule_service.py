from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arbitratarr.models.rule import Rule, RuleType


class RuleData(TypedDict):
    """Type definition for rule data dictionary."""

    name: str
    rule_type: RuleType
    pattern: str
    score: int
    priority: int
    description: str


DEFAULT_RULES: list[RuleData] = [
    {
        "name": "Reject Camera/TS/Screener",
        "rule_type": RuleType.EXCLUSION,
        "pattern": r"CAM|TS|HDCAM|SCR",
        "score": 0,
        "priority": 1,
        "description": "Reject low-quality camera recordings and screeners",
    },
    {
        "name": "Require HD Resolution",
        "rule_type": RuleType.REQUIREMENT,
        "pattern": r"1080p|2160p|720p",
        "score": 0,
        "priority": 2,
        "description": "Require HD resolution (720p or higher)",
    },
    {
        "name": "Prefer x265/HEVC",
        "rule_type": RuleType.SCORER,
        "pattern": r"x265|HEVC|H\.265",
        "score": 50,
        "priority": 3,
        "description": "Prefer HEVC codec for better compression",
    },
    {
        "name": "Prefer MeGusta",
        "rule_type": RuleType.SCORER,
        "pattern": r"MeGusta",
        "score": 100,
        "priority": 4,
        "description": "Preferred release group",
    },
    {
        "name": "Prefer LAMA/SPiCYLAMA",
        "rule_type": RuleType.SCORER,
        "pattern": r"SPiCYLAMA|LAMA",
        "score": 100,
        "priority": 5,
        "description": "Preferred release groups",
    },
]


class RuleService:
    """Service for managing rules in the database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all_rules(self) -> list[Rule]:
        """Get all rules ordered by priority."""
        result = await self.db.execute(select(Rule).order_by(Rule.priority, Rule.id))
        return list(result.scalars().all())

    async def get_rules_by_type(self, rule_type: RuleType) -> list[Rule]:
        """Get rules filtered by type."""
        result = await self.db.execute(
            select(Rule)
            .where(Rule.rule_type == rule_type)
            .where(Rule.is_enabled.is_(True))
            .order_by(Rule.priority)
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

    async def get_rule_by_id(self, rule_id: int) -> Rule | None:
        """Get a single rule by ID."""
        result = await self.db.execute(select(Rule).where(Rule.id == rule_id))
        return result.scalar_one_or_none()

    async def create_rule(
        self,
        name: str,
        rule_type: RuleType,
        pattern: str,
        score: int = 0,
        priority: int = 0,
        is_enabled: bool = True,
        description: str | None = None,
    ) -> Rule:
        """Create a new rule."""
        rule = Rule(
            name=name,
            rule_type=rule_type,
            pattern=pattern,
            score=score,
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
        pattern: str | None = None,
        score: int | None = None,
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
        if pattern is not None:
            rule.pattern = pattern
        if score is not None:
            rule.score = score
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

    async def toggle_rule(self, rule_id: int) -> Rule | None:
        """Toggle a rule's enabled status."""
        rule = await self.get_rule_by_id(rule_id)
        if not rule:
            return None

        rule.is_enabled = not rule.is_enabled
        await self.db.commit()
        await self.db.refresh(rule)
        return rule
