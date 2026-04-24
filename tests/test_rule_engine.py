"""Expanded tests for RuleEngine."""

from unittest.mock import MagicMock

from app.siftarr.models.rule import Rule, RuleType, TVTarget
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine, RuleMatch, SizeLimitRule


class TestRuleEngine:
    """Test cases for RuleEngine."""

    def test_from_db_rules(self):
        """Test creating RuleEngine from database rules."""
        mock_rule1 = MagicMock(spec=Rule)
        mock_rule1.is_enabled = True
        mock_rule1.rule_type = RuleType.EXCLUSION
        mock_rule1.id = 1
        mock_rule1.name = "CAM rejection"
        mock_rule1.pattern = "CAM|TS"
        mock_rule1.score = 0

        mock_rule2 = MagicMock(spec=Rule)
        mock_rule2.is_enabled = True
        mock_rule2.rule_type = RuleType.SCORER
        mock_rule2.id = 2
        mock_rule2.name = "x265 bonus"
        mock_rule2.pattern = "x265"
        mock_rule2.score = 50

        engine = RuleEngine.from_db_rules(rules=[mock_rule1, mock_rule2])

        assert len(engine.exclusion_patterns) == 1
        assert len(engine.scorer_patterns) == 1

    def test_from_db_rules_disabled_rules(self):
        """Test that disabled rules are excluded."""
        mock_rule = MagicMock(spec=Rule)
        mock_rule.is_enabled = False
        mock_rule.rule_type = RuleType.SCORER
        mock_rule.id = 1
        mock_rule.name = "Disabled"
        mock_rule.pattern = "x265"
        mock_rule.score = 50

        engine = RuleEngine.from_db_rules(rules=[mock_rule])

        assert len(engine.scorer_patterns) == 0

    def test_from_db_rules_size_limits(self):
        """Test size limit conversion from DB rules to bytes."""
        mock_rule = MagicMock(spec=Rule)
        mock_rule.is_enabled = True
        mock_rule.rule_type = RuleType.SIZE_LIMIT
        mock_rule.id = 3
        mock_rule.name = "Movie Size Limits"
        mock_rule.pattern = "size_limit"
        mock_rule.min_size_gb = 1
        mock_rule.max_size_gb = 10
        mock_rule.tv_target = TVTarget.SEASON_PACK

        engine = RuleEngine.from_db_rules(rules=[mock_rule])

        assert len(engine.size_limit_rules) == 1
        assert engine.size_limit_rules[0].min_size_bytes == 1 * 1024 * 1024 * 1024
        assert engine.size_limit_rules[0].max_size_bytes == 10 * 1024 * 1024 * 1024
        assert engine.size_limit_rules[0].tv_target == TVTarget.SEASON_PACK

    def test_evaluate_no_rules(self):
        """Test evaluating with no rules."""
        engine = RuleEngine()

        release = ProwlarrRelease(
            title="Test.Movie.2024.1080p.x264-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is True
        assert result.total_score == 0

    def test_evaluate_min_size_rejection(self):
        """Test minimum size rejection."""
        engine = RuleEngine(size_limit_rules=[(1, "Min Size", 1024 * 1024 * 1024, None)])

        release = ProwlarrRelease(
            title="Test.Movie.2024.1080p.x264-RLSGRP",
            size=500 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason is not None
        assert "below minimum" in result.rejection_reason

    def test_evaluate_max_size_rejection(self):
        """Test maximum size rejection."""
        engine = RuleEngine(size_limit_rules=[(1, "Max Size", None, 10 * 1024 * 1024)])

        release = ProwlarrRelease(
            title="Test.Movie.2024.1080p.x264-RLSGRP",
            size=20 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason is not None
        assert "above maximum" in result.rejection_reason

    def test_evaluate_tv_targeted_season_pack_rule_uses_raw_release_size(self):
        """Season-pack-targeted size rules should still compare the raw release size."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="TV Pack Size",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=20 * 1024 * 1024 * 1024,
                    tv_target=TVTarget.SEASON_PACK,
                )
            ]
        )

        release = ProwlarrRelease(
            title="Show.S01-S03.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"

    def test_evaluate_tv_targeted_episode_rule_uses_raw_release_size(self):
        """Episode-targeted size rules should still compare the raw release size."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="TV Episode Size",
                    min_size_bytes=2 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.EPISODE,
                )
            ]
        )

        release = ProwlarrRelease(
            title="Show.S01E01.1080p",
            size=1 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason == "Size 1.00 GB below minimum 2.00 GB"

    def test_evaluate_episode_target_rule_does_not_skip_season_pack(self):
        """Episode-target metadata should not bypass raw size checks for season packs."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="TV Episode Size",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.EPISODE,
                )
            ]
        )

        release = ProwlarrRelease(
            title="Show.S01.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"

    def test_evaluate_season_pack_target_rule_does_not_skip_episode_release(self):
        """Season-pack-target metadata should not bypass raw size checks for episodes."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="TV Pack Size",
                    min_size_bytes=2 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.SEASON_PACK,
                )
            ]
        )

        release = ProwlarrRelease(
            title="Show.S01E01.1080p",
            size=1 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason == "Size 1.00 GB below minimum 2.00 GB"

    def test_evaluate_movie_size_rule_without_tv_target(self):
        """Movie size rules should still work without a TV target."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="Movie Size",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=None,
                )
            ]
        )

        release = ProwlarrRelease(
            title="Movie.2024.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"

    def test_evaluate_both_scope_episode_target_applies_to_movies_and_tv(self):
        """Both-scoped episode rules should apply to movies and single episodes."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="Both Episode Target",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.EPISODE,
                    media_scope="both",
                )
            ]
        )

        movie_release = ProwlarrRelease(
            title="Movie.2024.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )
        tv_episode_release = ProwlarrRelease(
            title="Show.S01E01.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        movie_result = engine.evaluate(movie_release)
        tv_result = engine.evaluate(tv_episode_release)

        assert movie_result.passed is False
        assert movie_result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"
        assert tv_result.passed is False

    def test_evaluate_both_scope_season_target_applies_to_movies_and_tv(self):
        """Both-scoped season-pack rules should apply to movies and TV packs."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="Both Pack Target",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.SEASON_PACK,
                    media_scope="both",
                )
            ]
        )

        movie_release = ProwlarrRelease(
            title="Movie.2024.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )
        tv_pack_release = ProwlarrRelease(
            title="Show.S01-S03.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        movie_result = engine.evaluate(movie_release)
        tv_result = engine.evaluate(tv_pack_release)

        assert movie_result.passed is False
        assert movie_result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"
        assert tv_result.passed is False

    def test_evaluate_both_episode_target_rule_also_applies_to_tv_packs(self):
        """Both-scoped episode-target rules should also size-check TV packs."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="Both Episode Target",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.EPISODE,
                    media_scope="both",
                )
            ]
        )

        movie_release = ProwlarrRelease(
            title="Movie.2024.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )
        tv_pack_release = ProwlarrRelease(
            title="Show.S01.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        movie_result = engine.evaluate(movie_release)
        tv_pack_result = engine.evaluate(tv_pack_release)

        assert movie_result.passed is False
        assert movie_result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"
        assert tv_pack_result.passed is False
        assert tv_pack_result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"

    def test_evaluate_both_season_pack_target_rule_also_applies_to_tv_episodes(self):
        """Both-scoped season-pack-target rules should also size-check TV episodes."""
        engine = RuleEngine(
            size_limit_rules=[
                SizeLimitRule(
                    rule_id=1,
                    rule_name="Both Pack Target",
                    min_size_bytes=5 * 1024 * 1024 * 1024,
                    max_size_bytes=None,
                    tv_target=TVTarget.SEASON_PACK,
                    media_scope="both",
                )
            ]
        )

        movie_release = ProwlarrRelease(
            title="Movie.2024.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )
        tv_episode_release = ProwlarrRelease(
            title="Show.S01E01.1080p",
            size=4 * 1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        movie_result = engine.evaluate(movie_release)
        tv_episode_result = engine.evaluate(tv_episode_release)

        assert movie_result.passed is False
        assert movie_result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"
        assert tv_episode_result.passed is False
        assert tv_episode_result.rejection_reason == "Size 4.00 GB below minimum 5.00 GB"

    def test_evaluate_exclusion_rejection(self):
        """Test exclusion pattern rejection."""
        engine = RuleEngine(
            exclusion_patterns=[(1, "CAM rejection", r"CAM|TS|SCR|HDCAM")],
        )

        release = ProwlarrRelease(
            title="Movie.2024.HDCAM.x264-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason is not None
        assert "exclusion" in result.rejection_reason.lower()

    def test_evaluate_invalid_regex(self):
        """Test handling of invalid regex pattern."""
        engine = RuleEngine(
            exclusion_patterns=[(1, "Bad regex", r"[invalid")],
        )

        release = ProwlarrRelease(
            title="Test.Movie.2024.x264-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is True

    def test_evaluate_requirement_match(self):
        """Test requirement pattern match."""
        engine = RuleEngine(
            requirement_patterns=[(1, "HD required", r"1080p|720p")],
        )

        release = ProwlarrRelease(
            title="Movie.2024.1080p.x264-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is True

    def test_evaluate_requirement_no_match(self):
        """Test requirement pattern no match."""
        engine = RuleEngine(
            requirement_patterns=[(1, "HD required", r"1080p|720p")],
        )

        release = ProwlarrRelease(
            title="Movie.2024.480p.x264-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False
        assert result.rejection_reason is not None
        assert "requirement" in result.rejection_reason.lower()

    def test_evaluate_scorer(self):
        """Test scorer pattern scoring."""
        engine = RuleEngine(
            scorer_patterns=[
                (1, "x265 bonus", r"x265|HEVC", 50),
                (2, "MeGusta bonus", r"MeGusta", 100),
            ],
        )

        release = ProwlarrRelease(
            title="Movie.2024.1080p.x265-MeGusta-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is True
        assert result.total_score == 150

    def test_evaluate_exposes_matches_rejection_and_score_per_release(self):
        """Evaluation results should carry UI-ready matched rules and decisions per title."""
        engine = RuleEngine(
            size_limit_rules=[(1, "Movie Size", 1 * 1024**3, 2 * 1024**3)],
            exclusion_patterns=[(2, "Reject CAM", r"CAM")],
            scorer_patterns=[(3, "x265 bonus", r"x265|HEVC", 50)],
        )

        good = engine.evaluate(
            ProwlarrRelease(
                title="Movie.2024.1080p.x265-RLSGRP",
                size=int(1.5 * 1024**3),
                seeders=10,
                leechers=2,
                download_url="http://example.com/1",
                indexer="test",
            )
        )
        rejected = engine.evaluate(
            ProwlarrRelease(
                title="Movie.2024.HDCAM.x264-RLSGRP",
                size=int(1.5 * 1024**3),
                seeders=10,
                leechers=2,
                download_url="http://example.com/2",
                indexer="test",
            )
        )

        assert good.release.title == "Movie.2024.1080p.x265-RLSGRP"
        assert good.passed is True
        assert good.rejection_reason is None
        assert good.total_score == 50
        assert [(match.rule_name, match.score_delta) for match in good.matches if match.matched] == [
            ("Movie Size", 0),
            ("x265 bonus", 50),
        ]

        assert rejected.release.title == "Movie.2024.HDCAM.x264-RLSGRP"
        assert rejected.passed is False
        assert rejected.rejection_reason == "Matched exclusion pattern: Reject CAM"
        assert rejected.total_score == 0
        assert [match.rule_name for match in rejected.matches if match.matched] == [
            "Movie Size",
            "Reject CAM",
        ]

    def test_evaluate_batch(self):
        """Test batch evaluation."""
        engine = RuleEngine(
            requirement_patterns=[(1, "HD", r"1080p")],
            scorer_patterns=[(2, "x265", r"x265", 50)],
        )

        releases = [
            ProwlarrRelease(
                title="Movie.2024.1080p.x264-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url1",
                indexer="test",
            ),
            ProwlarrRelease(
                title="Movie.2024.1080p.x265-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url2",
                indexer="test",
            ),
        ]

        results = engine.evaluate_batch(releases)

        assert len(results) == 2
        assert results[0].total_score == 50
        assert results[0].release.title == "Movie.2024.1080p.x265-RLSGRP"

    def test_get_best_release(self):
        """Test getting best release."""
        engine = RuleEngine(
            scorer_patterns=[(1, "x265", r"x265", 50)],
        )

        releases = [
            ProwlarrRelease(
                title="Movie.2024.1080p.x264-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url1",
                indexer="test",
            ),
            ProwlarrRelease(
                title="Movie.2024.1080p.x265-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url2",
                indexer="test",
            ),
        ]

        best = engine.get_best_release(releases)

        assert best is not None
        assert best.total_score == 50
        assert best.release.title == "Movie.2024.1080p.x265-RLSGRP"

    def test_get_best_release_none_pass(self):
        """Test getting best release when none pass."""
        engine = RuleEngine(
            exclusion_patterns=[(1, "Reject", r"CAM")],
        )

        releases = [
            ProwlarrRelease(
                title="Movie.CAM.x264-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url1",
                indexer="test",
            ),
        ]

        best = engine.get_best_release(releases)

        assert best is None

    def test_multiple_exclusions_first_match_rejects(self):
        """Test that first matching exclusion rejects."""
        engine = RuleEngine(
            exclusion_patterns=[
                (1, "CAM", r"CAM"),
                (2, "TS", r"TS"),
            ],
        )

        release = ProwlarrRelease(
            title="Movie.TS.x264-RLSGRP",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is False

    def test_cam_ts_rule_does_not_match_normal_words(self):
        """Test that the camera/TS exclusion does not match substrings in words."""
        engine = RuleEngine(
            exclusion_patterns=[
                (1, "Reject Camera/TS/Screener", r"\b(?:CAM|TS|HDCAM|SCR|TELESYNC)\b"),
            ],
        )

        release = ProwlarrRelease(
            title="What a Girl Wants 2003 1080p WEBRip x264",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        result = engine.evaluate(release)

        assert result.passed is True

    def test_rule_match_dataclass(self):
        """Test RuleMatch dataclass."""
        match = RuleMatch(
            rule_id=1,
            rule_name="Test",
            matched=True,
            score_delta=50,
        )

        assert match.rule_id == 1
        assert match.matched is True
        assert match.score_delta == 50

    def test_release_evaluation_dataclass(self):
        """Test ReleaseEvaluation dataclass."""
        release = ProwlarrRelease(
            title="Test",
            size=1024,
            seeders=10,
            leechers=2,
            download_url="http://example.com",
            indexer="test",
        )

        evaluation = ReleaseEvaluation(
            release=release,
            passed=True,
            total_score=100,
            matches=[],
        )

        assert evaluation.passed is True
        assert evaluation.total_score == 100
        assert evaluation.rejection_reason is None
