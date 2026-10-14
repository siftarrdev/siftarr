"""Tests for the release parser."""

import pytest

from app.siftarr.models.request import MediaType
from app.siftarr.services.releases.release_parser import (
    ParsedReleaseCoverage,
    is_exact_single_episode_release,
    is_multi_episode_release,
    movie_release_identity_rejection_reason,
    parse_movie_release_identity,
    parse_release_coverage,
    tv_release_identity_rejection_reason,
)
from app.siftarr.services.releases.release_serializers import serialize_target_scope


@pytest.mark.parametrize(
    ("text", "season", "episode"),
    [
        (".S01E05.", 1, 5),
        (".s01e05.", 1, 5),
        (".S12E03.", 12, 3),
        (".S01E100.", 1, 100),
        ("(S03E07)", 3, 7),
        ("_S04E10_", 4, 10),
        (".S01E03.S02E05.", 1, 3),
        (".S01.", 1, None),
        (".S05.1080p.", 5, None),
        (".Season.2.", 2, None),
        (".Season2.", 2, None),
        ("Show.S01-S03.1080p", 1, None),
        ("Some.Random.Title", None, None),
        ("Movie.4K.Bluray", None, None),
        ("", None, None),
        ("Show.Complete.Series.1080p", None, None),
    ],
    ids=[
        "episode",
        "lowercase",
        "two-digit-season",
        "three-digit-episode",
        "parentheses",
        "underscores",
        "multiple-returns-first",
        "season-pack",
        "season-pack-with-quality",
        "season-word-dot",
        "season-word-no-dot",
        "multi-season-returns-first",
        "no-match",
        "4k-not-season",
        "empty",
        "complete-series-unparsed",
    ],
)
def test_parse_season_episode(text, season, episode):
    coverage = parse_release_coverage(text)
    assert (coverage.season_number, coverage.episode_number) == (season, episode)


class TestTVReleaseIdentity:
    @pytest.mark.parametrize(
        ("request_title", "request_year", "release_title"),
        [
            ("Parenthood", 2010, "Parenthood.S01E01.1080p.WEB-DL"),
            ("Let’s", 2026, "Lets.S01E01.1080p.WEB-DL"),
        ],
        ids=["no-explicit-year", "unicode-apostrophe"],
    )
    def test_accepts(self, request_title, request_year, release_title):
        assert (
            tv_release_identity_rejection_reason(
                request_title=request_title,
                request_year=request_year,
                release_title=release_title,
            )
            is None
        )

    @pytest.mark.parametrize(
        ("request_title", "request_year", "release_title"),
        [
            ("Parenthood", 2010, "Evil.S03E08.The.Demon.of.Parenthood.1080p"),
            ("Top Gear", 2002, "Top.Gear.US.S01E01.1080p"),
            ("Parenthood", 2010, "Parenthood.2025.S01.1080p"),
        ],
        ids=["episode-title-contains-show", "longer-show-name-prefix", "wrong-year"],
    )
    def test_rejects(self, request_title, request_year, release_title):
        reason = tv_release_identity_rejection_reason(
            request_title=request_title,
            request_year=request_year,
            release_title=release_title,
        )
        assert reason is not None
        assert reason.startswith("TV identity mismatch")

    def test_wrong_year_reason_names_both_years(self):
        assert (
            tv_release_identity_rejection_reason(
                request_title="Parenthood",
                request_year=2010,
                release_title="Parenthood.2025.S01.1080p",
            )
            == "TV identity mismatch: release year 2025 does not match request year 2010"
        )


def _coverage(*seasons, episode=None, complete=False):
    return ParsedReleaseCoverage(
        season_numbers=tuple(seasons), episode_number=episode, is_complete_series=complete
    )


class TestParseReleaseCoverage:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Show.S01E02.1080p", _coverage(1, episode=2)),
            ("Show.S01E02.S01-S03.1080p", _coverage(1, episode=2)),
            ("Show.S01E02.Complete.1080p", _coverage(1, episode=2)),
            (".S01.", _coverage(1)),
            ("Show.S01.S02.S03.1080p", _coverage(1, 2, 3)),
            ("Show.S01-S05.1080p", _coverage(1, 2, 3, 4, 5)),
            ("Show.S01-07.1080p", _coverage(*range(1, 8))),
            ("Show.S1-7.1080p", _coverage(*range(1, 8))),
            ("Show.Season 1-3.1080p", _coverage(1, 2, 3)),
            ("Show.Seasons 1 thru 7.1080p", _coverage(*range(1, 8))),
            ("Show.Season 1 through 5.1080p", _coverage(1, 2, 3, 4, 5)),
            ("Show.SEASONS 2 THRU 9.1080p", _coverage(*range(2, 10))),
            ("Show.Seasons 3 through 6.720p", _coverage(3, 4, 5, 6)),
            ("Show.Complete.Series.1080p", _coverage(complete=True)),
            ("Show.Complete.1080p", _coverage(complete=True)),
            ("Show.S01-S03.Complete.Series.1080p", _coverage(1, 2, 3, complete=True)),
            ("Show.Complete.S01.1080p", _coverage(1)),
            ("Show.S01.Complete.1080p", _coverage(1)),
            ("Movie.4K.Bluray", _coverage()),
        ],
        ids=[
            "episode",
            "episode-beats-pack",
            "complete-episode-stays-episode",
            "single-season",
            "repeated-season-tokens",
            "sxx-range",
            "compact-range",
            "compact-single-digit-range",
            "season-word-range",
            "thru",
            "through",
            "thru-uppercase",
            "through-720p",
            "complete-series",
            "bare-complete",
            "complete-series-with-seasons",
            "complete-single-season-prefix-stays-single-season",
            "complete-single-season-suffix-stays-single-season",
            "no-match",
        ],
    )
    def test_coverage(self, title, expected):
        assert parse_release_coverage(title) == expected

    @pytest.mark.parametrize(
        ("title", "scope"),
        [
            (
                "Show.S01E02.1080p",
                {"type": "single_episode", "season_number": 1, "episode_number": 2},
            ),
            (
                "Show.S01-S05.1080p",
                {"type": "multi_season_pack", "season_numbers": [1, 2, 3, 4, 5]},
            ),
            ("Show.Complete.Series.1080p", {"type": "complete_series"}),
            ("Movie.4K.Bluray", {"type": "unknown"}),
            (
                "Show.S01E01-E03.1080p",
                {"type": "multi_episode_pack", "season_number": 1, "first_episode_number": 1},
            ),
        ],
        ids=["single-episode", "multi-season", "complete-series", "unknown", "multi-episode"],
    )
    def test_target_scope(self, title, scope):
        assert serialize_target_scope(media_type=MediaType.TV, title=title) == scope

    def test_multi_episode_pack_is_not_exact_single_episode(self):
        title = "Show.S01E01-E03.1080p"
        assert parse_release_coverage(title) == _coverage(1, episode=1)
        assert is_multi_episode_release(title) is True
        assert is_exact_single_episode_release(title, 1, 1) is False


@pytest.mark.parametrize(
    ("title", "season", "episode", "expected"),
    [
        ("Show.S01E02.1080p", 1, 2, True),
        ("Show.S03E07.720p.WEB-DL", 3, 7, True),
        ("show.s01e02.1080p", 1, 2, True),
        ("Show.S01E02.1080p", 2, 2, False),
        ("Show.S01E02.1080p", 1, 3, False),
        ("Show.S01E01E02.1080p", 1, 1, False),
        ("Show.S01E01-E02.1080p", 1, 1, False),
        ("Show.S01E02.E03.1080p", 1, 2, False),
        ("Show.S01.1080p", 1, 1, False),
        ("Show.Complete.1080p", 1, 1, False),
        ("Some.Random.Title", 1, 1, False),
    ],
    ids=[
        "match",
        "match-with-resolution",
        "case-insensitive",
        "wrong-season",
        "wrong-episode",
        "multi-episode-e01e02",
        "range-dash-e02",
        "multi-episode-extra-token",
        "season-pack",
        "complete-series",
        "no-pattern",
    ],
)
def test_is_exact_single_episode_release(title, season, episode, expected):
    assert is_exact_single_episode_release(title, season, episode) is expected


class TestMovieReleaseIdentity:
    def test_parses_movie_title_and_year_before_quality_tokens(self):
        result = parse_movie_release_identity("The.Cheetah.Girls.2003.1080p.WEB-DL")
        assert result.title == "The.Cheetah.Girls"
        assert result.year == 2003

    @pytest.mark.parametrize(
        ("request_title", "request_year", "release_title"),
        [
            ("The Cheetah Girls", 2003, "the.cheetah-girls.(2003).1080p.WEB-DL"),
            ("The Cheetah Girls", 2003, "The.Cheetah.Girls.1080p.WEB-DL"),
            ("1917", 2019, "1917.2019.1080p.BluRay.x265"),
            ("Blade Runner 2049", 2017, "Blade.Runner.2049.2017.1080p.WEB-DL"),
            ("2012", 2009, "2012.2009.1080p.BluRay.x264"),
        ],
        ids=["title-variant", "missing-release-year", "1917", "blade-runner-2049", "2012"],
    )
    def test_accepts(self, request_title, request_year, release_title):
        assert (
            movie_release_identity_rejection_reason(
                request_title=request_title,
                request_year=request_year,
                release_title=release_title,
            )
            is None
        )

    def test_numbered_sequel_rejected_even_with_high_quality_tokens(self):
        reason = movie_release_identity_rejection_reason(
            request_title="The Cheetah Girls",
            request_year=2003,
            release_title="The.Cheetah.Girls.2.2005.1080p.WEB-DL",
        )
        assert reason is not None
        assert "release title 'The.Cheetah.Girls.2'" in reason

    def test_wrong_year_rejected_when_title_matches(self):
        reason = movie_release_identity_rejection_reason(
            request_title="The Cheetah Girls",
            request_year=2003,
            release_title="The.Cheetah.Girls.2005.1080p.WEB-DL",
        )
        assert (
            reason == "Movie identity mismatch: release year 2005 does not match request year 2003"
        )
