import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.siftarr.models.rule import TVTarget
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    is_exact_single_episode_release,
)

# ── Rule version (cache invalidation) ─────────────────────────────────

_rule_version: int = 0

# Centralised rule engine cache shared by all services.
# Key is media_type ("movie" | "tv"), value is (engine, version_at_cache_time).
_engine_cache: dict[str, tuple["RuleEngine", int]] = {}


def get_rule_version() -> int:
    """Return the current rule version.

    Incrementing this version signals that in-memory :class:`RuleEngine`
    caches should be rebuilt.
    """
    return _rule_version


def increment_rule_version() -> None:
    """Mark the rule set as changed so cached engines are invalidated."""
    global _rule_version  # noqa: PLW0603
    _rule_version += 1


def get_cached_engine(media_type: str) -> "RuleEngine | None":
    """Return a cached :class:`RuleEngine` for *media_type* if still fresh, or *None*."""
    entry = _engine_cache.get(media_type)
    if entry is not None and entry[1] == _rule_version:
        return entry[0]
    return None


def set_cached_engine(media_type: str, engine: "RuleEngine") -> None:
    """Store a :class:`RuleEngine` for *media_type* at the current rule version."""
    _engine_cache[media_type] = (engine, _rule_version)


def clear_engine_caches() -> None:
    """Purge all cached rule engines (used in tests)."""
    _engine_cache.clear()


@dataclass
class SizeLimitRule:
    rule_id: int
    rule_name: str
    min_size_bytes: int | None
    max_size_bytes: int | None
    tv_target: TVTarget | None = None
    media_scope: str = "both"


@dataclass
class RuleMatch:
    """Result of matching a release against a rule."""

    rule_id: int
    rule_name: str
    matched: bool
    score_delta: int = 0


@dataclass
class ReleaseEvaluation:
    """Result of evaluating a release against all rules."""

    release: ProwlarrRelease
    passed: bool
    total_score: int
    matches: list[RuleMatch]
    rejection_reason: str | None = None


class RuleEngine:
    """
    Rule engine for filtering and scoring releases.

    Rules are processed in order:
    1. Size limits (min/max) - reject if outside bounds
    2. Exclusion patterns - reject if any match
    3. Requirement patterns - reject if none match
    4. Scorer patterns - add points for each match
    """

    def __init__(
        self,
        size_limit_rules: Sequence[
            tuple[int, str, int | None, int | None]
            | tuple[int, str, int | None, int | None, TVTarget | None]
            | SizeLimitRule
        ]
        | None = None,
        exclusion_patterns: list[tuple[int, str, str]] | None = None,  # (id, name, pattern)
        requirement_patterns: list[tuple[int, str, str]] | None = None,  # (id, name, pattern)
        scorer_patterns: list[tuple[int, str, str, int]]
        | None = None,  # (id, name, pattern, score)
    ):
        self.size_limit_rules = [
            rule if isinstance(rule, SizeLimitRule) else SizeLimitRule(*rule)
            for rule in (size_limit_rules or [])
        ]
        self.exclusion_patterns = exclusion_patterns or []
        self.requirement_patterns = requirement_patterns or []
        self.scorer_patterns = scorer_patterns or []

        self._compiled_exclusion: list[tuple[int, str, re.Pattern[str]]] = []
        self._compiled_requirement: list[tuple[int, str, re.Pattern[str]]] = []
        self._compiled_scorer: list[tuple[int, str, re.Pattern[str], int]] = []

        for rule_id, rule_name, pattern in self.exclusion_patterns:
            try:
                self._compiled_exclusion.append(
                    (rule_id, rule_name, re.compile(pattern, re.IGNORECASE))
                )
            except re.error:
                continue

        for rule_id, rule_name, pattern in self.requirement_patterns:
            try:
                self._compiled_requirement.append(
                    (rule_id, rule_name, re.compile(pattern, re.IGNORECASE))
                )
            except re.error:
                continue

        for rule_id, rule_name, pattern, score in self.scorer_patterns:
            try:
                self._compiled_scorer.append(
                    (rule_id, rule_name, re.compile(pattern, re.IGNORECASE), score)
                )
            except re.error:
                continue

    @staticmethod
    def _matches_any_field(compiled: re.Pattern, release: ProwlarrRelease) -> bool:
        """Check if compiled pattern matches any of the release's relevant fields."""
        return bool(
            compiled.search(release.title)
            or (release.release_group and compiled.search(release.release_group))
            or (release.uploaded_by and compiled.search(release.uploaded_by))
        )

    @staticmethod
    def _scope_matches(rule_scope: str, media_type: str | None) -> bool:
        rule_scope = RuleEngine._enum_or_string_value(rule_scope).lower()
        if not rule_scope or rule_scope == "both" or media_type is None:
            return True
        return rule_scope == media_type.lower()

    @staticmethod
    def _enum_or_string_value(value: object, default: str = "") -> str:
        if value is None:
            return default
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
        if isinstance(value, str):
            return value
        return default

    @staticmethod
    def _size_rule_applies_to_release(rule: SizeLimitRule, release: ProwlarrRelease) -> bool:
        coverage = cached_parse_release_coverage(release.title)
        is_tv_release = bool(
            coverage.season_numbers
            or coverage.is_complete_series
            or coverage.episode_number is not None
        )
        if (rule.media_scope == "movie" and is_tv_release) or (
            rule.media_scope == "tv" and not is_tv_release
        ):
            return False
        if not is_tv_release or rule.tv_target is None:
            return True

        is_single_episode = (
            coverage.season_number is not None
            and coverage.episode_number is not None
            and is_exact_single_episode_release(
                release.title,
                coverage.season_number,
                coverage.episode_number,
            )
        )
        if rule.tv_target == TVTarget.EPISODE:
            return is_single_episode
        if rule.tv_target == TVTarget.SEASON_PACK:
            return not is_single_episode and bool(
                coverage.season_numbers or coverage.is_complete_series
            )
        return True

    @classmethod
    def from_db_rules(
        cls,
        rules: list | None = None,
        media_type: str | None = None,
    ) -> "RuleEngine":
        """Create RuleEngine from database rules."""
        size_limit_rules: list[SizeLimitRule] = []
        exclusions = []
        requirements = []
        scorers = []

        if rules:
            for rule in rules:
                if not rule.is_enabled:
                    continue
                media_scope = cls._enum_or_string_value(
                    getattr(rule, "media_scope", "both"),
                    "both",
                )
                if not cls._scope_matches(media_scope, media_type):
                    continue
                pattern = rule.pattern
                rule_type = cls._enum_or_string_value(getattr(rule, "rule_type", None)).lower()
                if rule_type == "size_limit":
                    min_bytes = (
                        int(rule.min_size_gb * 1024 * 1024 * 1024)
                        if getattr(rule, "min_size_gb", None) is not None
                        else None
                    )
                    max_bytes = (
                        int(rule.max_size_gb * 1024 * 1024 * 1024)
                        if getattr(rule, "max_size_gb", None) is not None
                        else None
                    )
                    size_limit_rules.append(
                        SizeLimitRule(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            min_size_bytes=min_bytes,
                            max_size_bytes=max_bytes,
                            tv_target=getattr(rule, "tv_target", None),
                            media_scope=media_scope,
                        )
                    )
                elif rule_type == "exclusion":
                    exclusions.append((rule.id, rule.name, pattern))
                elif rule_type == "requirement":
                    requirements.append((rule.id, rule.name, pattern))
                elif rule_type == "scorer":
                    scorers.append((rule.id, rule.name, pattern, rule.score))

        return cls(
            size_limit_rules=size_limit_rules,
            exclusion_patterns=exclusions,
            requirement_patterns=requirements,
            scorer_patterns=scorers,
        )

    def _to_bytes(self, size_str: str) -> int | None:
        """Convert size string like '5GB' to bytes."""
        size_str = size_str.strip().upper()
        multipliers = {
            "B": 1,
            "KB": 1024,
            "MB": 1024**2,
            "GB": 1024**3,
            "TB": 1024**4,
        }
        for suffix, mult in multipliers.items():
            if size_str.endswith(suffix):
                try:
                    num = float(size_str[: -len(suffix)])
                    return int(num * mult)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _format_size_gb(size_bytes: int) -> str:
        """Format bytes using the dashboard's 2-decimal GiB display."""
        gib = size_bytes / 1024 / 1024 / 1024
        return f"{gib:.2f} GB"

    def evaluate(self, release: ProwlarrRelease) -> ReleaseEvaluation:
        """
        Evaluate a single release against all rules.

        Returns:
            ReleaseEvaluation with pass/fail status, score, and match details.
        """
        matches: list[RuleMatch] = []
        total_score = 0
        passed = True
        rejection_reason: str | None = None

        # Check size limits
        for rule in self.size_limit_rules:
            if not self._size_rule_applies_to_release(rule, release):
                continue

            min_size_bytes = rule.min_size_bytes
            max_size_bytes = rule.max_size_bytes
            if min_size_bytes is not None and release.size < min_size_bytes:
                passed = False
                rejection_reason = (
                    f"Size {self._format_size_gb(release.size)} below minimum "
                    f"{self._format_size_gb(min_size_bytes)}"
                )
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        matched=False,
                    )
                )
                break
            if max_size_bytes is not None and release.size > max_size_bytes:
                passed = False
                rejection_reason = (
                    f"Size {self._format_size_gb(release.size)} above maximum "
                    f"{self._format_size_gb(max_size_bytes)}"
                )
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        matched=False,
                    )
                )
                break
            matches.append(
                RuleMatch(
                    rule_id=rule.rule_id,
                    rule_name=rule.rule_name,
                    matched=True,
                )
            )

        # Check exclusion patterns (reject immediately)
        for rule_id, rule_name, compiled in self._compiled_exclusion:
            if self._matches_any_field(compiled, release):
                passed = False
                rejection_reason = f"Matched exclusion pattern: {rule_name}"
                matches.append(
                    RuleMatch(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        matched=True,
                    )
                )
                break
            else:
                matches.append(
                    RuleMatch(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        matched=False,
                    )
                )

        # Check requirement patterns (all must match at least one)
        if passed and self._compiled_requirement:
            any_matched = False
            for rule_id, rule_name, compiled in self._compiled_requirement:
                if self._matches_any_field(compiled, release):
                    any_matched = True
                    matches.append(
                        RuleMatch(
                            rule_id=rule_id,
                            rule_name=rule_name,
                            matched=True,
                        )
                    )
                else:
                    matches.append(
                        RuleMatch(
                            rule_id=rule_id,
                            rule_name=rule_name,
                            matched=False,
                        )
                    )

            if not any_matched:
                passed = False
                rejection_reason = "No requirement patterns matched"

        # Calculate score for scorer patterns
        for rule_id, rule_name, compiled, score in self._compiled_scorer:
            if self._matches_any_field(compiled, release):
                total_score += score
                matches.append(
                    RuleMatch(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        matched=True,
                        score_delta=score,
                    )
                )
            else:
                matches.append(
                    RuleMatch(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        matched=False,
                    )
                )

        return ReleaseEvaluation(
            release=release,
            passed=passed,
            total_score=total_score,
            matches=matches,
            rejection_reason=rejection_reason,
        )

    def evaluate_batch(self, releases: list[ProwlarrRelease]) -> list[ReleaseEvaluation]:
        """
        Evaluate multiple releases and return sorted by score (highest first).

        Only returns releases that passed all filters.
        """
        results = [self.evaluate(r) for r in releases]

        # Filter to only passed releases
        passed = [r for r in results if r.passed]

        # Sort by score descending
        passed.sort(key=lambda x: x.total_score, reverse=True)

        return passed

    def get_best_release(self, releases: list[ProwlarrRelease]) -> ReleaseEvaluation | None:
        """
        Get the best release from a list.

        Returns the highest-scoring release that passes all rules, or None if none pass.
        """
        evaluated = self.evaluate_batch(releases)
        return evaluated[0] if evaluated else None
