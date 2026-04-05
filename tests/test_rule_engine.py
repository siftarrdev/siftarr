"""Tests for the Rule Engine."""

from app.arbitratarr.services.prowlarr_service import ProwlarrRelease
from app.arbitratarr.services.rule_engine import RuleEngine


class TestRuleEngine:
    """Test cases for RuleEngine."""

    def test_size_filter_min(self) -> None:
        """Test minimum size filter."""
        engine = RuleEngine(min_size_bytes=1024 * 1024 * 1024)  # 1GB

        release = ProwlarrRelease(
            title="Test.Movie.2024.1080p.x264-RLSGRP",
            size=500 * 1024 * 1024,  # 500MB
            seeders=10,
            leechers=2,
            download_url="https://example.com/torrent",
            indexer="test",
        )

        result = engine.evaluate(release)
        assert not result.passed
        assert "below minimum" in result.rejection_reason

    def test_size_filter_max(self) -> None:
        """Test maximum size filter."""
        engine = RuleEngine(max_size_bytes=10 * 1024 * 1024 * 1024)  # 10GB

        release = ProwlarrRelease(
            title="Test.Movie.2024.1080p.x264-RLSGRP",
            size=20 * 1024 * 1024 * 1024,  # 20GB
            seeders=10,
            leechers=2,
            download_url="https://example.com/torrent",
            indexer="test",
        )

        result = engine.evaluate(release)
        assert not result.passed
        assert "above maximum" in result.rejection_reason

    def test_exclusion_pattern(self) -> None:
        """Test exclusion pattern matching."""
        engine = RuleEngine(
            exclusion_patterns=[(1, "CAM/TS rejection", r"CAM|TS|HDCAM")],
        )

        release = ProwlarrRelease(
            title="Movie.2024.HDCAM.x264-RLSGRP",
            size=1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="https://example.com/torrent",
            indexer="test",
        )

        result = engine.evaluate(release)
        assert not result.passed
        assert "exclusion" in result.rejection_reason.lower()

    def test_requirement_pattern(self) -> None:
        """Test requirement pattern matching."""
        engine = RuleEngine(
            requirement_patterns=[(1, "HD required", r"1080p|720p")],
        )

        # Should pass - has 1080p
        release_pass = ProwlarrRelease(
            title="Movie.2024.1080p.x264-RLSGRP",
            size=1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="https://example.com/torrent",
            indexer="test",
        )

        result = engine.evaluate(release_pass)
        assert result.passed

        # Should fail - no HD resolution
        release_fail = ProwlarrRelease(
            title="Movie.2024.480p.x264-RLSGRP",
            size=1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="https://example.com/torrent",
            indexer="test",
        )

        result = engine.evaluate(release_fail)
        assert not result.passed

    def test_scorer_pattern(self) -> None:
        """Test scorer pattern matching."""
        engine = RuleEngine(
            scorer_patterns=[
                (1, "x265 bonus", r"x265|HEVC", 50),
                (2, "MeGusta bonus", r"MeGusta", 100),
            ],
        )

        release = ProwlarrRelease(
            title="Movie.2024.1080p.x265-MeGusta-RLSGRP",
            size=1024 * 1024 * 1024,
            seeders=10,
            leechers=2,
            download_url="https://example.com/torrent",
            indexer="test",
        )

        result = engine.evaluate(release)
        assert result.passed
        assert result.total_score == 150  # 50 + 100

    def test_evaluate_batch(self) -> None:
        """Test batch evaluation returns sorted results."""
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
        assert results[0].total_score == 50  # x265 scored
        assert results[0].release.title == "Movie.2024.1080p.x265-RLSGRP"

    def test_get_best_release(self) -> None:
        """Test getting the single best release."""
        engine = RuleEngine(
            requirement_patterns=[(1, "HD", r"1080p")],
            scorer_patterns=[(2, "MeGusta", r"MeGusta", 100)],
        )

        releases = [
            ProwlarrRelease(
                title="Movie.2024.1080p-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url1",
                indexer="test",
            ),
            ProwlarrRelease(
                title="Movie.2024.1080p-MeGusta-RLSGRP",
                size=1024,
                seeders=10,
                leechers=2,
                download_url="url2",
                indexer="test",
            ),
        ]

        best = engine.get_best_release(releases)

        assert best is not None
        assert best.total_score == 100
