import json
import re
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.rule import Rule, RuleType, TVTarget


class RuleData(TypedDict):
    """Type definition for rule data dictionary."""

    name: str
    rule_type: RuleType
    media_scope: str
    pattern: str
    score: int
    priority: int
    description: str | None
    is_enabled: bool
    min_size_gb: float | None
    max_size_gb: float | None
    tv_target: TVTarget | None


@dataclass
class RuleImportPreview:
    """Preview returned for a validated JSON rule import."""

    version: int
    rules: list[dict[str, Any]]
    replace_count: int


DEFAULT_RULES: list[RuleData] = [
    {
        "name": "Reject Camera/TS/Screener",
        "rule_type": RuleType.EXCLUSION,
        "media_scope": "both",
        "pattern": r"\b(?:CAM|TS|HDCAM|SCR|TELESYNC)\b",
        "score": 0,
        "priority": 1,
        "description": "Reject low-quality camera recordings and screeners",
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "Require HD Resolution",
        "rule_type": RuleType.REQUIREMENT,
        "media_scope": "both",
        "pattern": r"1080p|2160p|720p|4k",
        "score": 0,
        "priority": 2,
        "description": "Require HD resolution (720p or higher)",
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "Prefer x265/HEVC",
        "rule_type": RuleType.SCORER,
        "media_scope": "both",
        "pattern": r"x265|HEVC|H\.265",
        "score": 100,
        "priority": 3,
        "description": "Prefer HEVC codec for better compression",
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "Prefer MeGusta",
        "rule_type": RuleType.SCORER,
        "media_scope": "tv",
        "pattern": r"MeGusta",
        "score": 100,
        "priority": 4,
        "description": "Preferred release group",
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "Prefer LAMA/SPiCYLAMA",
        "rule_type": RuleType.SCORER,
        "media_scope": "movie",
        "pattern": r"SPiCYLAMA|LAMA",
        "score": 100,
        "priority": 5,
        "description": "Preferred release groups",
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "Movies Size Limit",
        "rule_type": RuleType.SIZE_LIMIT,
        "media_scope": "movie",
        "pattern": "size_limit",
        "score": 0,
        "priority": 6,
        "description": None,
        "is_enabled": True,
        "min_size_gb": 1.0,
        "max_size_gb": 10.0,
        "tv_target": None,
    },
    {
        "name": "Tv Episode Size",
        "rule_type": RuleType.SIZE_LIMIT,
        "media_scope": "tv",
        "pattern": "size_limit",
        "score": 0,
        "priority": 7,
        "description": None,
        "is_enabled": True,
        "min_size_gb": 0.1,
        "max_size_gb": 1.5,
        "tv_target": TVTarget.EPISODE,
    },
    {
        "name": "TV Seasons Size",
        "rule_type": RuleType.SIZE_LIMIT,
        "media_scope": "tv",
        "pattern": "size_limit",
        "score": 0,
        "priority": 8,
        "description": None,
        "is_enabled": True,
        "min_size_gb": 2.0,
        "max_size_gb": 15.0,
        "tv_target": TVTarget.SEASON_PACK,
    },
    {
        "name": "1080p TV",
        "rule_type": RuleType.SCORER,
        "media_scope": "tv",
        "pattern": r"1080P",
        "score": 100,
        "priority": 9,
        "description": None,
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "720p TV",
        "rule_type": RuleType.SCORER,
        "media_scope": "tv",
        "pattern": r"720p",
        "score": 30,
        "priority": 10,
        "description": None,
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "1080p Movie",
        "rule_type": RuleType.SCORER,
        "media_scope": "both",
        "pattern": r"1080p",
        "score": 30,
        "priority": 11,
        "description": None,
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
    {
        "name": "4k Movie",
        "rule_type": RuleType.SCORER,
        "media_scope": "movie",
        "pattern": r"2160p|4k",
        "score": 0,
        "priority": 12,
        "description": None,
        "is_enabled": True,
        "min_size_gb": None,
        "max_size_gb": None,
        "tv_target": None,
    },
]


SIZE_LIMIT_RULE_NAME = "Size Limits"


class RuleService:
    """Service for managing rules in the database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all_rules(self) -> list[Rule]:
        result = await self.db.execute(select(Rule).order_by(Rule.priority, Rule.id))
        return list(result.scalars().all())

    async def get_rules_by_type(self, rule_type: RuleType) -> list[Rule]:
        result = await self.db.execute(
            select(Rule)
            .where(Rule.rule_type == rule_type)
            .where(Rule.is_enabled.is_(True))
            .order_by(Rule.priority)
        )
        return list(result.scalars().all())

    async def get_all_rules_by_type(self, rule_type: RuleType) -> list[Rule]:
        result = await self.db.execute(
            select(Rule).where(Rule.rule_type == rule_type).order_by(Rule.priority)
        )
        return list(result.scalars().all())

    async def get_exclusions(self) -> list[Rule]:
        return await self.get_rules_by_type(RuleType.EXCLUSION)

    async def get_requirements(self) -> list[Rule]:
        return await self.get_rules_by_type(RuleType.REQUIREMENT)

    async def get_scorers(self) -> list[Rule]:
        return await self.get_rules_by_type(RuleType.SCORER)

    async def get_size_limits(self) -> list[Rule]:
        return await self.get_rules_by_type(RuleType.SIZE_LIMIT)

    async def get_rule_by_id(self, rule_id: int) -> Rule | None:
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
        tv_target: TVTarget | None = None,
        priority: int = 0,
        is_enabled: bool = True,
        description: str | None = None,
    ) -> Rule:
        rule = Rule(
            name=name,
            rule_type=rule_type,
            pattern=pattern,
            media_scope=media_scope,
            score=score,
            min_size_gb=min_size_gb,
            max_size_gb=max_size_gb,
            tv_target=tv_target,
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
        tv_target: TVTarget | None = None,
        priority: int | None = None,
        is_enabled: bool | None = None,
        description: str | None = None,
    ) -> Rule | None:
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
        rule.tv_target = tv_target
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
        rule = await self.get_rule_by_id(rule_id)
        if not rule:
            return False
        await self.db.delete(rule)
        await self.db.commit()
        return True

    async def seed_default_rules(self) -> list[Rule]:
        existing = await self.get_all_rules()
        if existing:
            return existing

        rules = []
        for rule_data in DEFAULT_RULES:
            rule = await self.create_rule(**rule_data)
            rules.append(rule)
        return rules

    async def ensure_default_rules(self) -> list[Rule]:
        rules = await self.get_all_rules()
        if not rules:
            return await self.seed_default_rules()
        return rules

    async def toggle_rule(self, rule_id: int) -> Rule | None:
        rule = await self.get_rule_by_id(rule_id)
        if not rule:
            return None
        rule.is_enabled = not rule.is_enabled
        await self.db.commit()
        await self.db.refresh(rule)
        return rule

    async def get_all_size_limit_rules(self) -> list[Rule]:
        return await self.get_all_rules_by_type(RuleType.SIZE_LIMIT)

    async def get_size_limit_rule_by_scope(
        self, media_scope: str, tv_target: TVTarget | None = None
    ) -> Rule | None:
        result = await self.db.execute(
            select(Rule)
            .where(Rule.rule_type == RuleType.SIZE_LIMIT)
            .where(Rule.media_scope == media_scope)
            .where(Rule.tv_target == tv_target)
        )
        return result.scalar_one_or_none()

    async def upsert_size_limit_rule(
        self,
        media_scope: str,
        min_size_gb: float | None,
        max_size_gb: float | None,
        tv_target: TVTarget | None = None,
        is_enabled: bool = True,
    ) -> Rule:
        rule = await self.get_size_limit_rule_by_scope(media_scope, tv_target)
        description_bits = []
        if min_size_gb is not None:
            description_bits.append(f"min {min_size_gb} GB")
        if max_size_gb is not None:
            description_bits.append(f"max {max_size_gb} GB")
        if tv_target == TVTarget.EPISODE:
            description_bits.append("TV episodes only")
        elif tv_target == TVTarget.SEASON_PACK:
            description_bits.append("TV season packs only")
        description = ", ".join(description_bits) if description_bits else "No limits configured"

        if rule:
            rule.name = SIZE_LIMIT_RULE_NAME
            rule.pattern = "size_limit"
            rule.min_size_gb = min_size_gb
            rule.max_size_gb = max_size_gb
            rule.tv_target = tv_target
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
            tv_target=tv_target,
            is_enabled=is_enabled,
            description=description,
        )

    @staticmethod
    def serialize_rule(rule: Rule) -> dict[str, Any]:
        return {
            "name": rule.name,
            "rule_type": rule.rule_type.value,
            "media_scope": rule.media_scope,
            "tv_target": rule.tv_target.value if rule.tv_target else None,
            "pattern": rule.pattern,
            "score": rule.score,
            "min_size_gb": rule.min_size_gb,
            "max_size_gb": rule.max_size_gb,
            "priority": rule.priority,
            "is_enabled": rule.is_enabled,
            "description": rule.description,
        }

    async def export_rules_json(self) -> str:
        rules = await self.get_all_rules()
        payload = {"version": 1, "rules": [self.serialize_rule(rule) for rule in rules]}
        return json.dumps(payload, indent=2, sort_keys=True)

    @staticmethod
    def _require_string(value: object, *, field_name: str, rule_index: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Rule {rule_index} field '{field_name}' must be a string.")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"Rule {rule_index} field '{field_name}' cannot be blank.")
        return normalized

    @staticmethod
    def _require_optional_string(value: object, *, field_name: str, rule_index: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Rule {rule_index} field '{field_name}' must be a string or null.")
        return value.strip() or None

    @staticmethod
    def _require_int(value: object, *, field_name: str, rule_index: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Rule {rule_index} field '{field_name}' must be an integer.")
        return value

    @staticmethod
    def _require_bool(value: object, *, field_name: str, rule_index: int) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"Rule {rule_index} field '{field_name}' must be a boolean.")
        return value

    @staticmethod
    def _require_optional_float(value: object, *, field_name: str, rule_index: int) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"Rule {rule_index} field '{field_name}' must be a number or null.")
        return float(value)

    def preview_import_rules(self, payload: str) -> RuleImportPreview:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc.msg}") from exc

        if not isinstance(data, dict):
            raise ValueError("Import must be a JSON object.")
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise ValueError("Unsupported rule import version.")

        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ValueError("Import must include a non-empty rules array.")

        preview_rules: list[dict[str, Any]] = []
        for index, raw_rule in enumerate(raw_rules, start=1):
            if not isinstance(raw_rule, dict):
                raise ValueError(f"Rule {index} must be an object.")
            raw_rule = cast(dict[str, Any], raw_rule)

            allowed_fields = {
                "name",
                "rule_type",
                "media_scope",
                "tv_target",
                "pattern",
                "score",
                "min_size_gb",
                "max_size_gb",
                "priority",
                "is_enabled",
                "description",
            }
            unknown_fields = sorted(set(raw_rule) - allowed_fields)
            if unknown_fields:
                raise ValueError(
                    f"Rule {index} contains unsupported field(s): {', '.join(unknown_fields)}."
                )

            try:
                rule_type = RuleType(
                    self._require_string(
                        raw_rule["rule_type"], field_name="rule_type", rule_index=index
                    )
                )
                media_scope = self._require_string(
                    raw_rule["media_scope"], field_name="media_scope", rule_index=index
                )
                tv_target_raw = raw_rule.get("tv_target")
                tv_target = (
                    TVTarget(
                        self._require_string(
                            tv_target_raw, field_name="tv_target", rule_index=index
                        )
                    )
                    if tv_target_raw is not None
                    else None
                )
            except KeyError as exc:
                raise ValueError(f"Rule {index} is missing required field: {exc.args[0]}") from exc
            except ValueError as exc:
                if str(exc).startswith(f"Rule {index} field"):
                    raise
                raise ValueError(f"Rule {index} contains an invalid enum value.") from exc

            if media_scope not in {"movie", "tv", "both"}:
                raise ValueError(f"Rule {index} has invalid media_scope.")
            if (
                media_scope in {"tv", "both"}
                and rule_type == RuleType.SIZE_LIMIT
                and tv_target is None
            ):
                raise ValueError(f"Rule {index} must set tv_target when TV is in scope.")
            if media_scope == "movie" and tv_target is not None:
                raise ValueError(f"Rule {index} cannot set tv_target for movie-only rules.")

            name = self._require_string(raw_rule.get("name"), field_name="name", rule_index=index)
            pattern = self._require_string(
                raw_rule.get("pattern"), field_name="pattern", rule_index=index
            )
            score = self._require_int(raw_rule.get("score"), field_name="score", rule_index=index)
            priority = self._require_int(
                raw_rule.get("priority"), field_name="priority", rule_index=index
            )
            is_enabled = self._require_bool(
                raw_rule.get("is_enabled"), field_name="is_enabled", rule_index=index
            )
            min_size_gb = self._require_optional_float(
                raw_rule.get("min_size_gb"), field_name="min_size_gb", rule_index=index
            )
            max_size_gb = self._require_optional_float(
                raw_rule.get("max_size_gb"), field_name="max_size_gb", rule_index=index
            )
            description = self._require_optional_string(
                raw_rule.get("description"), field_name="description", rule_index=index
            )

            if score < 0:
                raise ValueError(f"Rule {index} field 'score' cannot be negative.")
            if priority < 0:
                raise ValueError(f"Rule {index} field 'priority' cannot be negative.")

            if rule_type == RuleType.SIZE_LIMIT:
                if min_size_gb is None and max_size_gb is None:
                    raise ValueError(
                        f"Rule {index} size_limit must set min_size_gb or max_size_gb."
                    )
                if min_size_gb is not None and min_size_gb < 0:
                    raise ValueError(f"Rule {index} field 'min_size_gb' cannot be negative.")
                if max_size_gb is not None and max_size_gb < 0:
                    raise ValueError(f"Rule {index} field 'max_size_gb' cannot be negative.")
                if (
                    min_size_gb is not None
                    and max_size_gb is not None
                    and min_size_gb > max_size_gb
                ):
                    raise ValueError(
                        f"Rule {index} min_size_gb cannot be greater than max_size_gb."
                    )
                if pattern != "size_limit":
                    raise ValueError(
                        f"Rule {index} size_limit rules must use pattern 'size_limit'."
                    )
            else:
                if tv_target is not None:
                    raise ValueError(f"Rule {index} may only set tv_target on size_limit rules.")
                if min_size_gb is not None or max_size_gb is not None:
                    raise ValueError(
                        f"Rule {index} non-size_limit rules cannot set min_size_gb/max_size_gb."
                    )
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"Rule {index} field 'pattern' is not a valid regex: {exc}"
                    ) from exc

            preview_rules.append(
                {
                    "name": name,
                    "rule_type": rule_type,
                    "media_scope": media_scope,
                    "tv_target": tv_target,
                    "pattern": pattern,
                    "score": score,
                    "min_size_gb": min_size_gb,
                    "max_size_gb": max_size_gb,
                    "priority": priority,
                    "is_enabled": is_enabled,
                    "description": description,
                }
            )

        return RuleImportPreview(version=1, rules=preview_rules, replace_count=len(preview_rules))

    async def replace_rules_from_preview(self, preview: RuleImportPreview) -> list[Rule]:
        existing = await self.get_all_rules()
        for rule in existing:
            await self.db.delete(rule)
        await self.db.flush()

        created: list[Rule] = []
        for rule_data in preview.rules:
            rule = Rule(**rule_data)
            self.db.add(rule)
            created.append(rule)

        await self.db.commit()
        for rule in created:
            await self.db.refresh(rule)
        return created
