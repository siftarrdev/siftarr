"""Unit tests for app.siftarr.services.release_serializers."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

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


class TestFormatReleaseSize:
    def test_zero_bytes(self) -> None:
        assert format_release_size(0) == "Unknown"

    def test_negative_bytes(self) -> None:
        assert format_release_size(-1) == "Unknown"

    def test_small_bytes(self) -> None:
        # 1 MB
        assert format_release_size(1024 * 1024) == "0.00 GB"

    def test_one_gib(self) -> None:
        gib = 1024 * 1024 * 1024
        assert format_release_size(gib) == "1.00 GB"

    def test_half_gib(self) -> None:
        half = 512 * 1024 * 1024
        assert format_release_size(half) == "0.50 GB"

    def test_large_size(self) -> None:
        size = 5 * 1024 * 1024 * 1024
        assert format_release_size(size) == "5.00 GB"

    def test_fractional_gib(self) -> None:
        size = int(2.34 * 1024 * 1024 * 1024)
        # 2.34 GiB approximately
        result = format_release_size(size)
        assert result.startswith("2.3")
        assert result.endswith("GB")


# -- release_failed_size_limit ------------------------------------------------


class TestReleaseFailedSizeLimit:
    def test_size_prefix_returns_true(self) -> None:
        assert release_failed_size_limit({"rejection_reason": "Size 40.00 GB above limit"}) is True

    def test_size_prefix_with_different_reason(self) -> None:
        # Must start with "Size " (capital S, space)
        assert release_failed_size_limit({"rejection_reason": "size too small"}) is False

    def test_non_string_rejection_reason(self) -> None:
        assert release_failed_size_limit({"rejection_reason": None}) is False

    def test_no_rejection_reason_key(self) -> None:
        assert release_failed_size_limit({}) is False

    def test_other_rejection_reason(self) -> None:
        assert release_failed_size_limit({"rejection_reason": "Exclusion pattern matched"}) is False

    def test_integer_rejection_reason(self) -> None:
        assert release_failed_size_limit({"rejection_reason": 42}) is False


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

    def test_infers_season_count_from_covered_seasons(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 3 * 1024 * 1024 * 1024,
            "covered_seasons": [1, 2, 3],
            "covered_season_count": 0,  # will be inferred from list
            "passed": True,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season_bytes"] == 1 * 1024 * 1024 * 1024

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

    def test_passed_none_gives_size_per_season_passed_none(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 2 * 1024 * 1024 * 1024,
            "covered_season_count": 2,
            "passed": None,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season_passed"] is None

    def test_no_size_rule_leaves_size_per_season_passed_none(self) -> None:
        release: dict[str, object] = {
            "size_bytes": 2 * 1024 * 1024 * 1024,
            "covered_season_count": 2,
            "passed": True,
            "rejection_reason": None,
        }
        result = apply_release_size_per_season_metadata(release)
        assert result["size_per_season_passed"] is None

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

    def test_with_coverage(self) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation()
        coverage = ParsedReleaseCoverage(
            season_numbers=(1, 2),
            episode_number=None,
            is_complete_series=False,
        )
        result = serialize_evaluated_release(
            release, evaluation, coverage=coverage, known_total_seasons=5
        )
        assert result["covered_seasons"] == [1, 2]
        assert result["covered_season_count"] == 2
        assert result["known_total_seasons"] == 5
        assert result["is_complete_series"] is False
        assert result["covers_all_known_seasons"] is False

    def test_with_coverage_covers_all(self) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation()
        coverage = ParsedReleaseCoverage(
            season_numbers=(1, 2, 3, 4, 5),
            episode_number=None,
            is_complete_series=False,
        )
        result = serialize_evaluated_release(
            release, evaluation, coverage=coverage, known_total_seasons=5
        )
        assert result["covers_all_known_seasons"] is True

    def test_with_complete_series_coverage(self) -> None:
        release = self._make_release()
        evaluation = self._make_evaluation()
        coverage = ParsedReleaseCoverage(
            season_numbers=(),
            episode_number=None,
            is_complete_series=True,
        )
        result = serialize_evaluated_release(
            release, evaluation, coverage=coverage, known_total_seasons=3
        )
        assert result["is_complete_series"] is True
        # covers_all_known_seasons with complete series flag
        assert result["covers_all_known_seasons"] is True

    def test_release_with_id(self) -> None:
        evaluation = self._make_evaluation()
        # ProwlarrRelease doesn't have an id field by default,
        # so we test via attribute setting
        release_with_id = self._make_release()
        # Add id attribute dynamically
        object.__setattr__(release_with_id, "id", 42)  # type: ignore[attr-defined]
        result = serialize_evaluated_release(release_with_id, evaluation)
        assert result.get("stored_release_id") == 42

    def test_no_publish_date(self) -> None:
        release = self._make_release(publish_date=None)
        evaluation = self._make_evaluation()
        result = serialize_evaluated_release(release, evaluation)
        assert result["publish_date"] is None

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


# -- dashboard_release_sort_key -----------------------------------------------


class TestDashboardReleaseSortKey:
    def test_higher_score_sorts_first(self) -> None:
        high_score: dict[str, object] = {
            "score": 100,
            "_size_bytes": 1000,
            "seeders": 5,
            "title": "A",
        }
        low_score: dict[str, object] = {
            "score": 50,
            "_size_bytes": 1000,
            "seeders": 5,
            "title": "B",
        }
        assert dashboard_release_sort_key(high_score) < dashboard_release_sort_key(low_score)

    def test_smaller_size_sorts_first_with_same_score(self) -> None:
        small: dict[str, object] = {"score": 100, "_size_bytes": 500, "seeders": 5, "title": "A"}
        large: dict[str, object] = {"score": 100, "_size_bytes": 2000, "seeders": 5, "title": "B"}
        assert dashboard_release_sort_key(small) < dashboard_release_sort_key(large)

    def test_more_seeders_sorts_first(self) -> None:
        many: dict[str, object] = {"score": 100, "_size_bytes": 1000, "seeders": 50, "title": "A"}
        few: dict[str, object] = {"score": 100, "_size_bytes": 1000, "seeders": 5, "title": "B"}
        # -seeders makes more seeders sort first
        assert dashboard_release_sort_key(many) < dashboard_release_sort_key(few)

    def test_missing_fields_use_defaults(self) -> None:
        empty: dict[str, object] = {}
        key = dashboard_release_sort_key(empty)
        assert isinstance(key, tuple)
        assert key[0] == 0.0  # -score defaults to 0

    def test_negative_size_bytes_treated_as_inf(self) -> None:
        neg: dict[str, object] = {"_size_bytes": -1, "score": 0, "seeders": 0, "title": ""}
        pos: dict[str, object] = {"_size_bytes": 100, "score": 0, "seeders": 0, "title": ""}
        assert dashboard_release_sort_key(neg) > dashboard_release_sort_key(pos)

    def test_string_size_bytes_treated_as_inf(self) -> None:
        invalid: dict[str, object] = {"_size_bytes": "big", "score": 0, "seeders": 0, "title": ""}
        valid: dict[str, object] = {"_size_bytes": 100, "score": 0, "seeders": 0, "title": ""}
        assert dashboard_release_sort_key(invalid) > dashboard_release_sort_key(valid)

    def test_isoformat_publish_date_parsed(self) -> None:
        release: dict[str, object] = {
            "score": 0,
            "_size_bytes": 0,
            "seeders": 0,
            "title": "",
            "publish_date": "2025-01-15T00:00:00Z",
        }
        key = dashboard_release_sort_key(release)
        assert key[3] != 0.0  # publish_timestamp should be non-zero


# -- season_pack_release_sort_key ---------------------------------------------


class TestSeasonPackReleaseSortKey:
    def test_size_limit_failure_sorts_after_pass(self) -> None:
        passed: dict[str, object] = {
            "score": 100,
            "_size_bytes": 1000,
            "seeders": 5,
            "title": "A",
            "rejection_reason": None,
        }
        failed: dict[str, object] = {
            "score": 100,
            "_size_bytes": 1000,
            "seeders": 5,
            "title": "B",
            "rejection_reason": "Size 40.00 GB above limit",
        }
        assert season_pack_release_sort_key(passed) < season_pack_release_sort_key(failed)

    def test_no_size_failure_sorts_normally(self) -> None:
        release: dict[str, object] = {
            "score": 50,
            "_size_bytes": 1000,
            "seeders": 5,
            "title": "A",
        }
        # Should produce a tuple with first element 0
        key = season_pack_release_sort_key(release)
        assert key[0] == 0


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

    def test_removes_size_bytes_key(self) -> None:
        releases: list[dict[str, object]] = [
            {"score": 10, "_size_bytes": 100, "seeders": 5, "title": "A", "size_bytes": 100},
        ]
        result = finalize_releases(releases)
        assert "_size_bytes" not in result[0]

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

    def test_empty_list(self) -> None:
        result = finalize_releases([])
        assert result == []


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
