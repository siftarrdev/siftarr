"""Expanded tests for RuleEngine."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.rule import Rule, RuleType, TVTarget
from app.siftarr.services.decisions.rule_engine import (
    RuleEngine,
    SizeLimitRule,
    clear_engine_caches,
    increment_rule_version,
)
from app.siftarr.services.decisions.rule_engine_provider import get_rule_engine
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease


class _ScalarResult:
    def __init__(self, rules: list[object]) -> None:
        self._rules = rules

    def all(self) -> list[object]:
        return self._rules


class _ExecuteResult:
    def __init__(self, rules: list[object]) -> None:
        self._rules = rules

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._rules)


class _Db:
    def __init__(self, rules: list[object]) -> None:
        self.execute = AsyncMock(return_value=_ExecuteResult(rules))


@pytest.mark.asyncio
async def test_rule_engine_provider_reuses_cached_engine(monkeypatch):
    rules = [object()]
    db = _Db(rules)
    session = cast(AsyncSession, db)
    built: list[tuple[list[object], str | None]] = []

    def fake_from_db_rules(*, rules: list[object] | None = None, media_type: str | None = None):
        built.append((list(rules or []), media_type))
        return RuleEngine(scorer_patterns=[(len(built), "score", "x265", 10)])

    monkeypatch.setattr(RuleEngine, "from_db_rules", fake_from_db_rules)

    first = await get_rule_engine(session, "movie")
    second = await get_rule_engine(session, "movie")

    assert second is first
    assert built == [(rules, "movie")]
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_rule_engine_provider_rebuilds_after_rule_version_increment(monkeypatch):
    rules = [object()]
    db = _Db(rules)
    session = cast(AsyncSession, db)
    built = 0

    def fake_from_db_rules(*, rules: list[object] | None = None, media_type: str | None = None):
        nonlocal built
        built += 1
        return RuleEngine(scorer_patterns=[(built, "score", "x265", 10)])

    monkeypatch.setattr(RuleEngine, "from_db_rules", fake_from_db_rules)

    first = await get_rule_engine(session, "tv")
    increment_rule_version()
    second = await get_rule_engine(session, "tv")
    clear_engine_caches()

    assert second is not first
    assert built == 2
    assert db.execute.await_count == 2


GB = 1024**3


def _release(title: str = "Movie.2024.1080p.x264", size: float = 1024, **kwargs) -> ProwlarrRelease:
    return ProwlarrRelease(
        title=title,
        size=int(size),
        seeders=10,
        leechers=2,
        download_url="http://example.com",
        indexer="test",
        **kwargs,
    )


def _db_rule(rule_type, name="rule", pattern="x", score=0, scope="both", **extra):
    rule = MagicMock(spec=Rule)
    rule.is_enabled = extra.pop("is_enabled", True)
    rule.rule_type = rule_type
    rule.media_scope = scope
    rule.id = extra.pop("id", 1)
    rule.name = name
    rule.pattern = pattern
    rule.score = score
    for key, value in extra.items():
        setattr(rule, key, value)
    return rule


class TestFromDbRules:
    def test_filters_by_enablement_and_media_scope_and_converts_sizes(self):
        """Disabled and out-of-scope rules are dropped. Rule type values may be raw strings."""
        rules = [
            _db_rule("scorer", "Movie x265", "x265", 50, scope="movie", id=1),
            _db_rule("requirement", "Require 1080p", "1080p", scope="both", id=2),
            _db_rule("exclusion", "TV only", "HDTV", scope="tv", id=3),
            _db_rule(RuleType.SCORER, "Disabled", "x264", 5, is_enabled=False, id=4),
            _db_rule(
                RuleType.SIZE_LIMIT,
                "Movie Size",
                "size_limit",
                min_size_gb=1,
                max_size_gb=10,
                tv_target=TVTarget.SEASON_PACK,
                id=5,
            ),
        ]

        engine = RuleEngine.from_db_rules(rules=rules, media_type="movie")

        assert engine.scorer_patterns == [(1, "Movie x265", "x265", 50)]
        assert engine.requirement_patterns == [(2, "Require 1080p", "1080p")]
        assert engine.exclusion_patterns == []
        assert len(engine.size_limit_rules) == 1
        size_rule = engine.size_limit_rules[0]
        assert (size_rule.min_size_bytes, size_rule.max_size_bytes) == (1 * GB, 10 * GB)
        assert size_rule.tv_target == TVTarget.SEASON_PACK

    def test_tv_engine_accepts_raw_string_values_in_any_case(self):
        """Raw DB strings for type, scope, media type, and tv_target drive evaluation."""
        rules = [
            _db_rule("SCORER", "Prefer 1080p", "1080p", 25, scope="TV", id=1),
            _db_rule(RuleType.SCORER, "Prefer x265", "x265", 50, scope="BoTh", id=2),
            _db_rule(
                "SIZE_LIMIT",
                "TV Pack Size",
                "size_limit",
                scope="TV",
                min_size_gb=5,
                max_size_gb=None,
                tv_target="SEASON_PACK",
                id=3,
            ),
            _db_rule(
                RuleType.SIZE_LIMIT,
                "TV Episode Size",
                "size_limit",
                scope="tv",
                min_size_gb=2,
                max_size_gb=None,
                tv_target="Episode",
                id=4,
            ),
        ]
        engine = RuleEngine.from_db_rules(rules=rules, media_type="TV")

        small_pack = engine.evaluate(_release("Show.S01.1080p.x265", 4 * GB))
        big_pack = engine.evaluate(_release("Show.S01.1080p.x265", 6 * GB))
        small_episode = engine.evaluate(_release("Show.S01E01.1080p", 1 * GB))
        good_episode = engine.evaluate(_release("Show.S01E01.1080p.x265", 3 * GB))

        assert small_pack.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"
        assert small_episode.rejection_reason == "Size 1.00 GB below minimum 2.00 GB"
        assert big_pack.passed is True
        assert big_pack.total_score == 75
        assert good_episode.passed is True
        assert good_episode.total_score == 75


class TestRuleEngine:
    """Test cases for RuleEngine."""

    @pytest.mark.parametrize(
        ("pattern", "title", "score"),
        [
            (r"x265|HEVC|H[ ._-]?265", "Silo.S03E10.1080p.x264-GROUP", 0),
            (r"x265|HEVC|H[ ._-]?265", "Silo.S03E10.1080p.H 265-GROUP", 40),
            (r"x265|HEVC|H[ ._-]?265", "Silo.S03E10.1080p.H.265-GROUP", 40),
            (r"(1080p x265)", "Silo.S03E10.1080p.WEB.x264-GROUP", 0),
            (r"^1080p x265$", "x265 1080p", 0),
            ("1080p x265", "Silo.S03E10.x265.WEB.1080p-GROUP", 40),
            ("1080p x265", "Elsbeth.S03E01.1080p.WEB-DL.x265-GROUP", 40),
        ],
    )
    def test_phrase_fallback_preserves_regex_syntax(self, pattern, title, score):
        engine = RuleEngine(scorer_patterns=[(1, "Codec preference", pattern, 40)])
        result = engine.evaluate(_release(title, GB))
        assert result.passed is True
        assert result.total_score == score

    @pytest.mark.parametrize(
        ("tv_target", "scope", "title", "size_gb", "min_gb", "max_gb", "reason"),
        [
            pytest.param(
                None,
                "both",
                "Movie.2024.1080p",
                0.5,
                1,
                None,
                "Size 0.50 GB below minimum 1.00 GB",
                id="movie-below-min",
            ),
            pytest.param(
                None,
                "both",
                "Movie.2024.1080p",
                20,
                None,
                10,
                "Size 20.00 GB above maximum 10.00 GB",
                id="movie-above-max",
            ),
            pytest.param(
                None,
                "both",
                "Movie.2024.1080p",
                4,
                5,
                None,
                "Size 4.00 GB below minimum 5.00 GB",
                id="movie-no-target",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "both",
                "Show.S01-S03.1080p",
                4,
                5,
                20,
                "Size 1.33 GB below minimum 5.00 GB",
                id="multi-season-pack-uses-per-season-size",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "both",
                "Show.S01-S06.480p",
                50,
                None,
                15,
                None,
                id="multi-season-pack-passes-per-season-max",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "both",
                "Show.Complete.Series.1080p",
                4,
                5,
                None,
                "Size 4.00 GB below minimum 5.00 GB",
                id="pack-target-applies-to-complete-series",
            ),
            pytest.param(
                TVTarget.EPISODE,
                "tv",
                "Show.S01E01.1080p",
                1,
                2,
                None,
                "Size 1.00 GB below minimum 2.00 GB",
                id="episode-target-uses-raw-size",
            ),
            pytest.param(
                TVTarget.EPISODE,
                "tv",
                "Show.S01.1080p",
                4,
                5,
                None,
                None,
                id="episode-target-skips-pack",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "tv",
                "Show.S01E01.1080p",
                1,
                2,
                None,
                None,
                id="pack-target-skips-episode",
            ),
            pytest.param(
                TVTarget.EPISODE,
                "both",
                "Movie.2024.1080p",
                4,
                5,
                None,
                "Size 4.00 GB below minimum 5.00 GB",
                id="both-episode-target-checks-movie",
            ),
            pytest.param(
                TVTarget.EPISODE,
                "both",
                "Show.S01E01.1080p",
                4,
                5,
                None,
                "Size 4.00 GB below minimum 5.00 GB",
                id="both-episode-target-checks-episode",
            ),
            pytest.param(
                TVTarget.EPISODE,
                "both",
                "Show.S01.1080p",
                4,
                5,
                None,
                None,
                id="both-episode-target-skips-pack",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "both",
                "Movie.2024.1080p",
                4,
                5,
                None,
                "Size 4.00 GB below minimum 5.00 GB",
                id="both-pack-target-checks-movie",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "both",
                "Show.S01-S03.1080p",
                4,
                5,
                None,
                "Size 1.33 GB below minimum 5.00 GB",
                id="both-pack-target-checks-pack",
            ),
            pytest.param(
                TVTarget.SEASON_PACK,
                "both",
                "Show.S01E01.1080p",
                4,
                5,
                None,
                None,
                id="both-pack-target-skips-episode",
            ),
        ],
    )
    def test_evaluate_size_limit_by_target_and_release_shape(
        self, tv_target, scope, title, size_gb, min_gb, max_gb, reason
    ):
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="Size",
                    min_size_bytes=None if min_gb is None else int(min_gb * GB),
                    max_size_bytes=None if max_gb is None else int(max_gb * GB),
                    tv_target=tv_target,
                    media_scope=scope,
                )
            ]
        )

        result = engine.evaluate(_release(title, size_gb * GB))

        assert result.rejection_reason == reason
        assert result.passed is (reason is None)

    def test_evaluate_no_rules(self):
        result = RuleEngine().evaluate(_release())
        assert result.passed is True
        assert result.total_score == 0

    def test_evaluate_exclusion_rejection(self):
        engine = RuleEngine(exclusion_patterns=[(1, "CAM rejection", r"CAM|TS|SCR|HDCAM")])

        result = engine.evaluate(_release("Movie.2024.HDCAM.x264-RLSGRP"))

        assert result.passed is False
        assert result.rejection_reason == "Matched exclusion pattern: CAM rejection"

    def test_evaluate_invalid_regex(self):
        """An invalid regex is ignored instead of raising."""
        engine = RuleEngine(exclusion_patterns=[(1, "Bad regex", r"[invalid")])

        assert engine.evaluate(_release("Test.Movie.2024.x264-RLSGRP")).passed is True

    def test_cam_ts_rule_does_not_match_normal_words(self):
        engine = RuleEngine(
            exclusion_patterns=[
                (1, "Reject Camera/TS/Screener", r"\b(?:CAM|TS|HDCAM|SCR|TELESYNC)\b"),
            ],
        )

        assert engine.evaluate(_release("What a Girl Wants 2003 1080p WEBRip x264")).passed is True

    @pytest.mark.parametrize(
        ("requirement", "title", "passed"),
        [
            (r"1080p|720p", "Movie.2024.1080p.x264-RLSGRP", True),
            (r"1080p|720p", "Movie.2024.480p.x264-RLSGRP", False),
        ],
        ids=["requirement-met", "requirement-missing"],
    )
    def test_evaluate_requirement(self, requirement, title, passed):
        engine = RuleEngine(requirement_patterns=[(1, "HD required", requirement)])

        result = engine.evaluate(_release(title))

        assert result.passed is passed
        if not passed:
            assert result.rejection_reason == "No requirement patterns matched"

    def test_evaluate_scorer_sums_matching_rules(self):
        engine = RuleEngine(
            scorer_patterns=[
                (1, "x265 bonus", r"x265|HEVC", 50),
                (2, "MeGusta bonus", r"MeGusta", 100),
                (3, "No match", r"AV1", 7),
            ],
        )

        result = engine.evaluate(_release("Movie.2024.1080p.x265-MeGusta-RLSGRP"))

        assert result.passed is True
        assert result.total_score == 150

    @pytest.mark.parametrize(
        ("kind", "pattern", "fields", "passed", "score"),
        [
            ("exclusion", "BadGroup", {"release_group": "BadGroup"}, False, 0),
            (
                "exclusion",
                "BadUploader",
                {"release_group": "G", "uploaded_by": "BadUploader"},
                False,
                0,
            ),
            ("requirement", "MeGusta|LAMA", {"release_group": "MeGusta"}, True, 0),
            (
                "requirement",
                "MeGusta",
                {"release_group": "Other", "uploaded_by": "Other"},
                False,
                0,
            ),
            ("scorer", "LAMA", {"release_group": "LAMA"}, True, 100),
            (
                "scorer",
                "TopUploader",
                {"release_group": "G", "uploaded_by": "TopUploader"},
                True,
                100,
            ),
        ],
        ids=[
            "exclusion-release-group",
            "exclusion-uploaded-by",
            "requirement-release-group",
            "requirement-no-field-matches",
            "scorer-release-group",
            "scorer-uploaded-by",
        ],
    )
    def test_patterns_also_match_release_group_and_uploader(
        self, kind, pattern, fields, passed, score
    ):
        """The title does not match. Release group or uploader decides the result."""
        engine = RuleEngine(
            exclusion_patterns=[(1, "rule", pattern)] if kind == "exclusion" else [],
            requirement_patterns=[(1, "rule", pattern)] if kind == "requirement" else [],
            scorer_patterns=[(1, "rule", pattern, 100)] if kind == "scorer" else [],
        )

        result = engine.evaluate(_release("Movie.2024.1080p.x264", **fields))

        assert result.passed is passed
        assert result.total_score == score

    def test_evaluate_exposes_matches_rejection_and_score_per_release(self):
        """Evaluation results carry UI-ready matched rules and decisions per title."""
        engine = RuleEngine(
            size_limit_rules=[(1, "Movie Size", 1 * GB, 2 * GB)],
            exclusion_patterns=[(2, "Reject CAM", r"CAM")],
            scorer_patterns=[(3, "x265 bonus", r"x265|HEVC", 50)],
        )

        good = engine.evaluate(_release("Movie.2024.1080p.x265-RLSGRP", 1.5 * GB))
        rejected = engine.evaluate(_release("Movie.2024.HDCAM.x264-RLSGRP", 1.5 * GB))

        assert good.release.title == "Movie.2024.1080p.x265-RLSGRP"
        assert good.passed is True
        assert good.rejection_reason is None
        assert good.total_score == 50
        assert [(m.rule_name, m.score_delta) for m in good.matches if m.matched] == [
            ("Movie Size", 0),
            ("x265 bonus", 50),
        ]
        assert rejected.passed is False
        assert rejected.rejection_reason == "Matched exclusion pattern: Reject CAM"
        assert rejected.total_score == 0
        assert [m.rule_name for m in rejected.matches if m.matched] == ["Movie Size", "Reject CAM"]
        assert [(m.rule_name, m.rule_type, m.effect) for m in good.matches] == [
            ("Movie Size", "size_limit", "size_limit"),
            ("Reject CAM", "exclusion", "disallow"),
            ("x265 bonus", "scorer", "allow"),
        ]
