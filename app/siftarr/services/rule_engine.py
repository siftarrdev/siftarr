import re
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass

from app.siftarr.models.rule import TVTarget
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.release_parser import parse_release_coverage


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
            with suppress(re.error):
                self._compiled_exclusion.append(
                    (rule_id, rule_name, re.compile(pattern, re.IGNORECASE))
                )

        for rule_id, rule_name, pattern in self.requirement_patterns:
            with suppress(re.error):
                self._compiled_requirement.append(
                    (rule_id, rule_name, re.compile(pattern, re.IGNORECASE))
                )

        for rule_id, rule_name, pattern, score in self.scorer_patterns:
            with suppress(re.error):
                self._compiled_scorer.append(
                    (rule_id, rule_name, re.compile(pattern, re.IGNORECASE), score)
                )

    @staticmethod
    def _scope_matches(rule_scope: str, media_type: str | None) -> bool:
        if not rule_scope or rule_scope == "both" or media_type is None:
            return True
        return rule_scope == media_type

    @staticmethod
    def _release_matches_tv_target(release: ProwlarrRelease, tv_target: TVTarget | None) -> bool:
        if tv_target is None:
            return False

        coverage = parse_release_coverage(release.title)
        is_episode = coverage.episode_number is not None
        is_pack = (
            coverage.is_complete_series or len(coverage.season_numbers) >= 1 and not is_episode
        )

        if tv_target == TVTarget.EPISODE:
            return is_episode
        if tv_target == TVTarget.SEASON_PACK:
            return is_pack
        return False

    @classmethod
    def _size_rule_applies_to_release(cls, rule: SizeLimitRule, release: ProwlarrRelease) -> bool:
        coverage = parse_release_coverage(release.title)
        is_tv_release = bool(
            coverage.season_numbers
            or coverage.is_complete_series
            or coverage.episode_number is not None
        )

        if rule.media_scope == "movie" and is_tv_release:
            return False
        if rule.media_scope == "tv" and not is_tv_release:
            return False

        if rule.tv_target is not None and is_tv_release:
            return cls._release_matches_tv_target(release, rule.tv_target)

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
                if not cls._scope_matches(getattr(rule, "media_scope", "both"), media_type):
                    continue
                pattern = rule.pattern
                if rule.rule_type.value == "size_limit":
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
                            media_scope=getattr(rule, "media_scope", "both"),
                        )
                    )
                elif rule.rule_type.value == "exclusion":
                    exclusions.append((rule.id, rule.name, pattern))
                elif rule.rule_type.value == "requirement":
                    requirements.append((rule.id, rule.name, pattern))
                elif rule.rule_type.value == "scorer":
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

    def evaluate_per_season_size(self, size_bytes: int) -> bool | None:
        """
        Evaluate a per-season size against SEASON_PACK size-limit rules.

        Applicable rules are those where:
          - tv_target == TVTarget.SEASON_PACK, OR
          - tv_target is None AND media_scope in ("tv", "both")

        Returns False on first violation, True if all applicable rules pass,
        None if no applicable rules found.
        """
        applicable = [
            rule
            for rule in self.size_limit_rules
            if rule.tv_target == TVTarget.SEASON_PACK
            or (rule.tv_target is None and rule.media_scope in ("tv", "both"))
        ]

        if not applicable:
            return None

        for rule in applicable:
            if rule.min_size_bytes is not None and size_bytes < rule.min_size_bytes:
                return False
            if rule.max_size_bytes is not None and size_bytes > rule.max_size_bytes:
                return False

        return True

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
            if compiled.search(release.title):
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
                if compiled.search(release.title):
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
            if compiled.search(release.title):
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
