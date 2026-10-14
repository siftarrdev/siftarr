"""Unit tests for app.siftarr.services.release_serializers."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType
from app.siftarr.models.staged_torrent import STAGED_STATUS_APPROVED, StagedTorrent
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation, RuleMatch
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease
from app.siftarr.services.releases.release_parser import ParsedReleaseCoverage
from app.siftarr.services.releases.release_serializers import (
    apply_release_size_per_season_metadata,
    dashboard_release_sort_key,
    finalize_releases,
    format_release_size,
    release_failed_size_limit,
    season_pack_release_sort_key,
    serialize_active_staged_torrent,
    serialize_evaluated_release,
    serialize_stored_evaluated_release,
    serialize_target_scope,
)

# -- format_release_size -------------------------------------------------------

GIB = 1024 * 1024 * 1024


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "Unknown"),
        (-1, "Unknown"),
        (1024 * 1024, "0.00 GB"),
        (GIB // 2, "0.50 GB"),
        (GIB, "1.00 GB"),
        (5 * GIB, "5.00 GB"),
    ],
    ids=["zero", "negative", "one-mib", "half-gib", "one-gib", "five-gib"],
)
def test_format_release_size(size: int, expected: str) -> None:
    assert format_release_size(size) == expected


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ({"rejection_reason": "Size 40.00 GB above limit"}, True),
        ({"rejection_reason": "size too small"}, False),
        ({"rejection_reason": "Exclusion pattern matched"}, False),
        ({"rejection_reason": None}, False),
        ({"rejection_reason": 42}, False),
        ({}, False),
    ],
    ids=["size-prefix", "lowercase-size", "other-reason", "none", "non-string", "missing-key"],
)
def test_release_failed_size_limit(release: dict[str, object], expected: bool) -> None:
    assert release_failed_size_limit(release) is expected


# -- apply_release_size_per_season_metadata -----------------------------------


class TestApplyReleaseSizePerSeasonMetadata:
    def test_basic_season_size(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 2 * 1024 * 1024 * 1024,
            "covered_seasons": [1, 2],
            "covered_season_count": 2,
            "known_total_seasons": 5,
            "passed": True,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season"] is not None
        assert isinstance(result["size_per_season_bytes"], int)
        assert result["size_per_season_bytes"] == 1 * 1024 * 1024 * 1024

    def test_zero_size_bytes_returns_none(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 0,
            "covered_season_count": 2,
            "passed": True,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season"] is None
        assert result["size_per_season_bytes"] is None
        assert result["size_per_season_passed"] is None

    def test_zero_season_count_returns_none(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 1000,
            "covered_season_count": 0,
            "passed": True,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season"] is None

    def test_infers_from_complete_series(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 5 * 1024 * 1024 * 1024,
            "covered_seasons": [],
            "covered_season_count": 0,
            "is_complete_series": True,
            "known_total_seasons": 5,
            "passed": True,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season_bytes"] == 1 * 1024 * 1024 * 1024

    def test_matching_size_rule_marks_size_per_season_passed(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 2 * 1024 * 1024 * 1024,
            "covered_season_count": 2,
            "passed": True,
            "matches": [{"rule_type": "size_limit", "effect": "size_limit", "matched": True}],
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season_passed"] is True

    def test_passed_true_but_size_limit_failed(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 2 * 1024 * 1024 * 1024,
            "covered_season_count": 2,
            "passed": True,
            "matches": [{"rule_type": "size_limit", "effect": "size_limit", "matched": False}],
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season_passed"] is False


# -- serialize_evaluated_release ----------------------------------------------


class TestSerializeEvaluatedRelease:
    def _make_release(self, **overrides: object) -> ProwlarrRelease:
        defaults: dict[str, object] = {
            "title": "Test.Release.S01.1080p.WEB-DL",
            "size": 2 * 1024 * 1024 * 1024,
            "seeders": 10,
            "leechers": 3,
            "download_url": "http://example.com/file.torrent",
            "magnet_url": None,
            "info_hash": "abc123",
            "indexer": "TestIndexer",
            "publish_date": datetime(2025, 1, 1, tzinfo=UTC),
            "resolution": "1080p",
            "codec": "h264",
            "release_group": "GROUP",
            "files": None,
            "uploaded_by": None,
        }
        defaults.update(overrides)
        return ProwlarrRelease.model_validate(defaults)

    def _make_evaluation(self, **overrides: object) -> ReleaseEvaluation:
        defaults: dict[str, object] = {
            "passed": True,
            "total_score": 50,
            "rejection_reason": None,
            "matches": [],
        }
        defaults.update(overrides)
        # Build a mock-like object with the needed attributes
        release = self._make_release()
        rejection_reason = defaults.get("rejection_reason")
        if rejection_reason is not None and not isinstance(rejection_reason, str):
            rejection_reason = None
        return ReleaseEvaluation(
            release=release,
            passed=defaults["passed"],
            total_score=defaults["total_score"],
            matches=defaults["matches"],
            rejection_reason=rejection_reason,
        )

    def test_basic_serialization(self) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation()
        result = serialize_evaluated_release(release, evaluation)
        assert result["title"] == "Test.Release.S01.1080p.WEB-DL"
        assert result["score"] == 50
        assert result["passed"] is True
        assert result["status"] == "passed"
        assert result["status_label"] == "Passed"
        assert result["size"] == format_release_size(release.size)
        assert result["size_bytes"] == release.size
        assert result["_size_bytes"] == release.size
        assert result["seeders"] == 10
        assert result["rejection_reason"] is None

    def test_serializes_rule_match_metadata_and_size_passed(self) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation(
            matches=[
                RuleMatch(
                    rule_id=1,
                    rule_name="Season pack size",
                    matched=True,
                    rule_type="size_limit",
                    effect="size_limit",
                )
            ]
        )
        result = serialize_evaluated_release(release, evaluation)
        assert result["size_passed"] is True
        assert result["matches"] == [
            {
                "rule_name": "Season pack size",
                "matched": True,
                "score_delta": 0,
                "rule_type": "size_limit",
                "effect": "size_limit",
            }
        ]

    def test_rejected_release(self) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation(passed=False, rejection_reason="Exclusion matched")
        result = serialize_evaluated_release(release, evaluation)
        assert result["status"] == "rejected"
        assert result["status_label"] == "Rejected"
        assert result["rejection_reason"] == "Exclusion matched"

    @pytest.mark.parametrize(
        ("seasons", "complete", "total", "covers_all"),
        [
            ((1, 2), False, 5, False),
            ((1, 2, 3, 4, 5), False, 5, True),
            ((), True, 3, True),
        ],
        ids=["partial", "all-seasons", "complete-series"],
    )
    def test_with_coverage(
        self, seasons: tuple[int, ...], complete: bool, total: int, covers_all: bool
    ) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation()
        coverage = ParsedReleaseCoverage(
            season_numbers=seasons, episode_number=None, is_complete_series=complete
        )
        result = serialize_evaluated_release(
            release, evaluation, coverage=coverage, known_total_seasons=total
        )
        assert result["covered_seasons"] == list(seasons)
        assert result["covered_season_count"] == len(seasons)
        assert result["known_total_seasons"] == total
        assert result["is_complete_series"] is complete
        assert result["covers_all_known_seasons"] is covers_all

    def test_release_with_id(self) -> None:
        evaluation = self._make_evaluation()
        # ProwlarrRelease doesn't have an id field by default,
        # so we test via attribute setting
        release_with_id = self._make_release()
        # Add id attribute dynamically
        object.__setattr__(release_with_id, "id", 42)  # type: ignore[attr-defined]
        result = serialize_evaluated_release(release_with_id, evaluation)
        assert result.get("stored_release_id") == 42

    def test_stored_release_reuses_rule_evidence_without_live_evaluation(self) -> None:
        release = Release(
            title="Stored.Movie.1080p",
            size=1024,
            seeders=4,
            leechers=0,
            download_url="https://example.test/download",
            magnet_url=None,
            indexer="Idx",
            resolution="1080p",
            codec="h264",
            release_group="GRP",
            uploaded_by=None,
            publish_date=datetime(2026, 1, 1, tzinfo=UTC),
            score=91,
            passed_rules=True,
            rule_evidence={
                "passed": True,
                "score": 91,
                "rejection_reason": None,
                "matches": [{"rule_name": "Stored", "matched": True, "score_delta": 91}],
            },
        )

        result = serialize_stored_evaluated_release(release, None, media_type=MediaType.MOVIE)

        assert result["score"] == 91
        assert result["passed"] is True
        assert result["matches"] == [{"rule_name": "Stored", "matched": True, "score_delta": 91}]

    def test_stored_release_legacy_row_uses_live_evaluation_fallback(self) -> None:
        release = Release(
            title="Legacy.Movie.1080p",
            size=1024,
            seeders=4,
            leechers=0,
            download_url="https://example.test/download",
            indexer="Idx",
            resolution="1080p",
            score=0,
            passed_rules=False,
            rule_evidence=None,
        )
        evaluation = MagicMock(
            passed=False,
            total_score=-25,
            rejection_reason="Legacy fallback rejected",
            matches=[],
        )

        result = serialize_stored_evaluated_release(release, evaluation, media_type=MediaType.MOVIE)

        assert result["rejection_reason"] == "Legacy fallback rejected"
        rule_evidence = cast(dict[str, Any], result["rule_evidence"])
        assert rule_evidence["score"] == -25

    def test_active_staged_torrent_status_output_uses_stored_value(self) -> None:
        torrent = StagedTorrent(
            id=7,
            request_id=1,
            torrent_path="/tmp/test.torrent",
            json_path="",
            original_filename="test.torrent",
            title="Stored.Movie.1080p",
            size=1024,
            indexer="Idx",
            score=10,
            status=STAGED_STATUS_APPROVED,
            selection_source="rule",
        )

        result = serialize_active_staged_torrent(torrent, media_type=MediaType.MOVIE)

        assert result["status"] == "approved"


# -- sort keys -----------------------------------------------------------------


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {"score": 100, "_size_bytes": 1000, "seeders": 5, "title": "A"}
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_row(score=100), _row(score=50)),
        (_row(_size_bytes=500), _row(_size_bytes=2000)),
        (_row(seeders=50), _row(seeders=5)),
        (_row(_size_bytes=100), _row(_size_bytes=-1)),
        (_row(_size_bytes=100), _row(_size_bytes="big")),
    ],
    ids=["higher-score", "smaller-size", "more-seeders", "negative-size-last", "invalid-size-last"],
)
def test_dashboard_sort_key_orders_first_before_second(
    first: dict[str, object], second: dict[str, object]
) -> None:
    assert dashboard_release_sort_key(first) < dashboard_release_sort_key(second)


def test_dashboard_sort_key_parses_isoformat_publish_date() -> None:
    key = dashboard_release_sort_key(_row(publish_date="2025-01-15T00:00:00Z"))
    assert key[3] != 0.0


def test_dashboard_sort_key_defaults_missing_fields() -> None:
    assert dashboard_release_sort_key({})[0] == 0.0


def test_size_limit_failure_sorts_after_pass() -> None:
    passed = _row(rejection_reason=None)
    failed = _row(rejection_reason="Size 40.00 GB above limit", title="B")
    assert season_pack_release_sort_key(passed) < season_pack_release_sort_key(failed)


# -- finalize_releases --------------------------------------------------------


class TestFinalizeReleases:
    def test_sorts_by_default_key(self) -> None:
        low: dict[str, object] = {
            "score": 10,
            "_size_bytes": 100,
            "seeders": 5,
            "title": "Low",
            "size_bytes": 100,
        }
        high: dict[str, object] = {
            "score": 100,
            "_size_bytes": 50,
            "seeders": 10,
            "title": "High",
            "size_bytes": 50,
        }
        result = finalize_releases([low, high])
        assert result[0]["title"] == "High"
        assert result[1]["title"] == "Low"

    def test_custom_sort_key(self) -> None:
        # Use a sort key that sorts by title ascending
        releases: list[dict[str, object]] = [
            {"title": "Zebra", "score": 0, "_size_bytes": 0, "seeders": 0},
            {"title": "Apple", "score": 0, "_size_bytes": 0, "seeders": 0},
        ]
        result = finalize_releases(releases, sort_key=lambda r: str(r["title"]))
        assert result[0]["title"] == "Apple"
        assert result[1]["title"] == "Zebra"

    def test_finalize_with_season_pack_sort_key(self) -> None:
        passed: dict[str, object] = {
            "score": 100,
            "_size_bytes": 1000,
            "seeders": 5,
            "title": "A",
            "size_bytes": 1000,
            "rejection_reason": None,
        }
        failed: dict[str, object] = {
            "score": 50,
            "_size_bytes": 2000,
            "seeders": 3,
            "title": "B",
            "size_bytes": 2000,
            "rejection_reason": "Size 40.00 GB above limit",
        }
        result = finalize_releases([failed, passed], sort_key=season_pack_release_sort_key)
        # Size-limit-passing release should come first
        assert result[0]["rejection_reason"] is None
        assert result[1]["rejection_reason"] == "Size 40.00 GB above limit"
        # _size_bytes should be removed
        assert "_size_bytes" not in result[0]
        assert "_size_bytes" not in result[1]


# -- serialize_target_scope (season-pack UI contract) ---------------------------


class TestSerializeTargetScopeForPacks:
    """Lock the target_scope contract the dashboard season-pack UI groups by."""

    def test_single_season_pack_scope(self) -> None:
        scope = serialize_target_scope(
            media_type=MediaType.TV,
            title="Top.Gear.S14.1080p.WEB-DL.x265",
            season_number=14,
            episode_number=None,
            season_coverage=None,
        )
        assert scope == {"type": "season_pack", "season_numbers": [14]}

    def test_multi_season_pack_scope_from_stored_coverage(self) -> None:
        scope = serialize_target_scope(
            media_type=MediaType.TV,
            title="Top.Gear.S14-S22.1080p.Mixed.WEB",
            season_number=14,
            episode_number=None,
            season_coverage="14,15,16,17,18,19,20,21,22",
        )
        assert scope["type"] == "multi_season_pack"
        assert scope["season_numbers"] == [14, 15, 16, 17, 18, 19, 20, 21, 22]

    def test_complete_series_scope(self) -> None:
        scope = serialize_target_scope(
            media_type=MediaType.TV,
            title="Top.Gear.Complete.Series.720p",
            season_number=None,
            episode_number=None,
            season_coverage="*",
        )
        assert scope == {"type": "complete_series"}
