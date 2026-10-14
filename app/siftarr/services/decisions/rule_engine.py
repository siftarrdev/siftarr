import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.siftarr.models.rule import TVTarget
from app.siftarr.services.decisions.release_quality import ReleaseQualityPolicy, quality_signals
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_parser import (
    cached_parse_release_coverage,
    is_exact_single_episode_release,
)

# ── Rule version (cache invalidation) ─────────────────────────────────

_rule_version: int = 0

# Centralised rule engine cache shared by all services.
# Key is media_type ("movie" | "tv"), value is (engine, version_at_cache_time).
_engine_cache: dict[str, tuple[RuleEngine, int]] = {}


def increment_rule_version() -> None:
    """Mark the rule set as changed so cached engines are invalidated."""
    global _rule_version  # noqa: PLW0603
    _rule_version += 1


def get_cached_engine(media_type: str) -> RuleEngine | None:
    """Return a cached :class:`RuleEngine` for *media_type* if still fresh, or *None*."""
    entry = _engine_cache.get(media_type)
    if entry is not None and entry[1] == _rule_version:
        return entry[0]
    return None


def set_cached_engine(media_type: str, engine: RuleEngine) -> None:
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
    tv_target: TVTarget | str | None = None
    media_scope: str = "both"


@dataclass
class RuleMatch:
    """Result of matching a release against a rule."""

    rule_id: int | None
    rule_name: str
    matched: bool
    score_delta: int = 0
    rule_type: str | None = None
    effect: str | None = None


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
        quality_policy: ReleaseQualityPolicy | None = None,
    ):
        self.size_limit_rules = [
            rule if isinstance(rule, SizeLimitRule) else SizeLimitRule(*rule)
            for rule in (size_limit_rules or [])
        ]
        self.exclusion_patterns = exclusion_patterns or []
        self.requirement_patterns = requirement_patterns or []
        self.scorer_patterns = scorer_patterns or []
        self.quality_policy = quality_policy

        self._compiled_exclusion: list[tuple[int, str, re.Pattern[str]]] = []
        self._compiled_requirement: list[tuple[int, str, re.Pattern[str]]] = []
        self._compiled_scorer: list[tuple[int, str, re.Pattern[str], int]] = []

        for rule_id, rule_name, pattern in self.exclusion_patterns:
            if (compiled := self._compile_pattern(pattern)) is not None:
                self._compiled_exclusion.append((rule_id, rule_name, compiled))

        for rule_id, rule_name, pattern in self.requirement_patterns:
            if (compiled := self._compile_pattern(pattern)) is not None:
                self._compiled_requirement.append((rule_id, rule_name, compiled))

        for rule_id, rule_name, pattern, score in self.scorer_patterns:
            if (compiled := self._compile_pattern(pattern)) is not None:
                self._compiled_scorer.append((rule_id, rule_name, compiled, score))

    @staticmethod
    def _compile_pattern(pattern: str) -> re.Pattern[str] | None:
        """Compile a rule pattern case-insensitively, or return None if it is invalid."""
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error:
            return None

    @staticmethod
    def _matches_any_field(compiled: re.Pattern, release: ProwlarrRelease) -> bool:
        """Check if compiled pattern matches any of the release's relevant fields."""
        fields = [release.title]
        if release.release_group:
            fields.append(release.release_group)
        if release.uploaded_by:
            fields.append(release.uploaded_by)
        return any(
            compiled.search(field)
            or compiled.search(RuleEngine._normalize_match_text(field))
            or RuleEngine._matches_all_phrase_terms(compiled, field)
            for field in fields
        )

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        """Normalize common release separators so literal phrase rules still match."""
        return re.sub(r"[.\-_()[\]{}]+", " ", value)

    @staticmethod
    def _matches_all_phrase_terms(compiled: re.Pattern, value: str) -> bool:
        pattern = compiled.pattern.strip()
        if not re.search(r"\s", pattern):
            return False
        # Only literal phrases support the out-of-order term fallback. Splitting
        # regex syntax can break character classes or change groups/alternation.
        if any(character in pattern for character in r"\.^$*+?{}[]|()"):
            return False
        normalized = RuleEngine._normalize_match_text(value)
        terms = [term for term in re.split(r"\s+", pattern) if term]
        return bool(terms) and all(
            re.search(re.escape(term), normalized, compiled.flags) for term in terms
        )

    @staticmethod
    def _scope_matches(rule_scope: str, media_type: str | None) -> bool:
        rule_scope = RuleEngine._normalized_identifier(rule_scope, "both")
        if not rule_scope or rule_scope == "both" or media_type is None:
            return True
        return rule_scope == RuleEngine._normalized_identifier(media_type)

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
    def _normalized_identifier(value: object, default: str = "") -> str:
        raw = RuleEngine._enum_or_string_value(value, default).strip().lower()
        if "." in raw:
            raw = raw.rsplit(".", 1)[-1]
        return raw.replace("-", "_")

    @staticmethod
    def _normalize_tv_target(value: object) -> TVTarget | None:
        target = RuleEngine._normalized_identifier(value)
        if target == TVTarget.EPISODE.value:
            return TVTarget.EPISODE
        if target in {TVTarget.SEASON_PACK.value, "seasonpack"}:
            return TVTarget.SEASON_PACK
        return None

    @staticmethod
    def _size_rule_applies_to_release(rule: SizeLimitRule, release: ProwlarrRelease) -> bool:
        coverage = cached_parse_release_coverage(release.title)
        is_tv_release = bool(
            coverage.season_numbers
            or coverage.is_complete_series
            or coverage.episode_number is not None
        )
        media_scope = RuleEngine._normalized_identifier(rule.media_scope, "both")
        if (media_scope == "movie" and is_tv_release) or (
            media_scope == "tv" and not is_tv_release
        ):
            return False
        tv_target = RuleEngine._normalize_tv_target(rule.tv_target)
        if not is_tv_release or tv_target is None:
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
        if tv_target == TVTarget.EPISODE:
            return is_single_episode
        return not is_single_episode and bool(
            coverage.season_numbers or coverage.is_complete_series
        )

    @staticmethod
    def _size_for_rule(rule: SizeLimitRule, release: ProwlarrRelease) -> int:
        """Normalize multi-season pack sizes to a per-season value."""
        if RuleEngine._normalize_tv_target(rule.tv_target) != TVTarget.SEASON_PACK:
            return release.size
        season_count = len(cached_parse_release_coverage(release.title).season_numbers)
        return int(round(release.size / season_count)) if season_count > 1 else release.size

    @classmethod
    def from_db_rules(
        cls,
        rules: list | None = None,
        media_type: str | None = None,
    ) -> RuleEngine:
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
                media_scope = cls._normalized_identifier(media_scope, "both")
                if not cls._scope_matches(media_scope, media_type):
                    continue
                pattern = rule.pattern
                rule_type = cls._normalized_identifier(getattr(rule, "rule_type", None))
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
                            tv_target=cls._normalize_tv_target(getattr(rule, "tv_target", None)),
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

    @staticmethod
    def _format_size_gb(size_bytes: int) -> str:
        """Format bytes using the dashboard's 2-decimal GiB display."""
        gib = size_bytes / 1024 / 1024 / 1024
        return f"{gib:.2f} GB"

    def evaluate(
        self, release: ProwlarrRelease, *, allow_episode_size_fallback: bool = False
    ) -> ReleaseEvaluation:
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
            comparable_size = self._size_for_rule(rule, release)
            if min_size_bytes is not None and comparable_size < min_size_bytes:
                passed = False
                rejection_reason = (
                    f"Size {self._format_size_gb(comparable_size)} below minimum "
                    f"{self._format_size_gb(min_size_bytes)}"
                )
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        matched=False,
                        rule_type="size_limit",
                        effect="size_limit",
                    )
                )
                break
            if max_size_bytes is not None and comparable_size > max_size_bytes:
                fallback_limit = getattr(self.quality_policy, "episode_fallback_max_size_bytes", 0)
                is_episode_rule = self._normalize_tv_target(rule.tv_target) == TVTarget.EPISODE
                if (
                    allow_episode_size_fallback
                    and is_episode_rule
                    and comparable_size <= fallback_limit
                ):
                    matches.append(
                        RuleMatch(
                            rule_id=rule.rule_id,
                            rule_name=f"{rule.rule_name} (episode fallback)",
                            matched=True,
                            rule_type="size_limit",
                            effect="size_fallback",
                        )
                    )
                    continue
                passed = False
                rejection_reason = (
                    f"Size {self._format_size_gb(comparable_size)} above maximum "
                    f"{self._format_size_gb(max_size_bytes)}"
                )
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        matched=False,
                        rule_type="size_limit",
                        effect="size_limit",
                    )
                )
                break
            matches.append(
                RuleMatch(
                    rule_id=rule.rule_id,
                    rule_name=rule.rule_name,
                    matched=True,
                    rule_type="size_limit",
                    effect="size_limit",
                )
            )

        # Check exclusion patterns (reject immediately)
        for rule_id, rule_name, compiled in self._compiled_exclusion:
            matched = self._matches_any_field(compiled, release)
            matches.append(
                RuleMatch(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    matched=matched,
                    rule_type="exclusion",
                    effect="disallow",
                )
            )
            if matched:
                passed = False
                rejection_reason = f"Matched exclusion pattern: {rule_name}"
                break

        # Check requirement patterns (all must match at least one)
        if passed and self._compiled_requirement:
            any_matched = False
            for rule_id, rule_name, compiled in self._compiled_requirement:
                matched = self._matches_any_field(compiled, release)
                any_matched = any_matched or matched
                matches.append(
                    RuleMatch(
                        rule_id=rule_id,
                        rule_name=rule_name,
                        matched=matched,
                        rule_type="requirement",
                        effect="allow",
                    )
                )

            if not any_matched:
                passed = False
                rejection_reason = "No requirement patterns matched"

        # Calculate score for scorer patterns
        for rule_id, rule_name, compiled, score in self._compiled_scorer:
            matched = self._matches_any_field(compiled, release)
            if matched:
                total_score += score
            matches.append(
                RuleMatch(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    matched=matched,
                    score_delta=score if matched else 0,
                    rule_type="scorer",
                    effect="allow",
                )
            )

        if self.quality_policy is not None:
            for signal in quality_signals(release, self.quality_policy):
                total_score += signal.score_delta
                matches.append(
                    RuleMatch(
                        rule_id=None,
                        rule_name=signal.name,
                        matched=True,
                        score_delta=signal.score_delta,
                        rule_type="quality",
                        effect="score",
                    )
                )

        return ReleaseEvaluation(
            release=release,
            passed=passed,
            total_score=total_score,
            matches=matches,
            rejection_reason=rejection_reason,
        )
