"""Tests for the release parser."""

from app.siftarr.services.release_parser import (
    ParsedReleaseCoverage,
    ParsedSeasonEpisode,
    is_exact_single_episode_release,
    parse_release_coverage,
    parse_season_episode,
)


class TestParseSeasonEpisode:
    def test_s01e05_extracts_season_and_episode(self):
        result = parse_season_episode(".S01E05.")
        assert result == ParsedSeasonEpisode(season_number=1, episode_number=5)

    def test_s02e12_extracts_season_and_episode(self):
        result = parse_season_episode(".S02E12.")
        assert result == ParsedSeasonEpisode(season_number=2, episode_number=12)

    def test_s01_season_pack(self):
        result = parse_season_episode(".S01.")
        assert result == ParsedSeasonEpisode(season_number=1, episode_number=None)

    def test_season_2_pack(self):
        result = parse_season_episode(".Season.2.")
        assert result == ParsedSeasonEpisode(season_number=2, episode_number=None)

    def test_season2_without_dot(self):
        result = parse_season_episode(".Season2.")
        assert result == ParsedSeasonEpisode(season_number=2, episode_number=None)

    def test_no_match_returns_none(self):
        result = parse_season_episode("Some.Random.Title")
        assert result == ParsedSeasonEpisode(season_number=None, episode_number=None)

    def test_4k_returns_none(self):
        result = parse_season_episode("Movie.4K.Bluray")
        assert result == ParsedSeasonEpisode(season_number=None, episode_number=None)

    def test_empty_string_returns_none(self):
        result = parse_season_episode("")
        assert result == ParsedSeasonEpisode(season_number=None, episode_number=None)

    def test_multiple_matches_returns_first(self):
        result = parse_season_episode(".S01E03.S02E05.")
        assert result == ParsedSeasonEpisode(season_number=1, episode_number=3)

    def test_season_episode_with_parentheses(self):
        result = parse_season_episode("(S03E07)")
        assert result == ParsedSeasonEpisode(season_number=3, episode_number=7)

    def test_season_episode_with_underscores(self):
        result = parse_season_episode("_S04E10_")
        assert result == ParsedSeasonEpisode(season_number=4, episode_number=10)

    def test_season_pack_not_confused_by_episode_in_adjacent_text(self):
        result = parse_season_episode(".S05.1080p.")
        assert result == ParsedSeasonEpisode(season_number=5, episode_number=None)

    def test_season_episode_takes_priority_over_season_pack(self):
        result = parse_season_episode(".S01E02.")
        assert result.season_number == 1
        assert result.episode_number == 2

    def test_season_episode_case_insensitive(self):
        result = parse_season_episode(".s01e05.")
        assert result == ParsedSeasonEpisode(season_number=1, episode_number=5)

    def test_two_digit_season(self):
        result = parse_season_episode(".S12E03.")
        assert result == ParsedSeasonEpisode(season_number=12, episode_number=3)

    def test_three_digit_episode(self):
        result = parse_season_episode(".S01E100.")
        assert result == ParsedSeasonEpisode(season_number=1, episode_number=100)

    def test_multi_season_pack_returns_first_season_for_legacy_callers(self):
        result = parse_season_episode("Show.S01-S03.1080p")
        assert result == ParsedSeasonEpisode(season_number=1, episode_number=None)

    def test_complete_series_without_season_numbers_stays_unparsed_for_legacy_callers(self):
        result = parse_season_episode("Show.Complete.Series.1080p")
        assert result == ParsedSeasonEpisode(season_number=None, episode_number=None)


class TestParseReleaseCoverage:
    def test_single_season_pack(self):
        result = parse_release_coverage(".S01.")
        assert result == ParsedReleaseCoverage(season_numbers=(1,), episode_number=None)

    def test_multi_season_sxx_range(self):
        result = parse_release_coverage("Show.S01-S05.1080p")
        assert result == ParsedReleaseCoverage(season_numbers=(1, 2, 3, 4, 5), episode_number=None)

    def test_multi_season_compact_sxx_range_without_repeated_s(self):
        result = parse_release_coverage("Show.S01-07.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1, 2, 3, 4, 5, 6, 7),
            episode_number=None,
        )

    def test_multi_season_compact_single_digit_sxx_range(self):
        result = parse_release_coverage("Show.S1-7.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1, 2, 3, 4, 5, 6, 7),
            episode_number=None,
        )

    def test_multi_season_word_range(self):
        result = parse_release_coverage("Show.Season 1-3.1080p")
        assert result == ParsedReleaseCoverage(season_numbers=(1, 2, 3), episode_number=None)

    def test_repeated_season_tokens(self):
        result = parse_release_coverage("Show.S01.S02.S03.1080p")
        assert result == ParsedReleaseCoverage(season_numbers=(1, 2, 3), episode_number=None)

    def test_episode_takes_priority_over_pack_coverage(self):
        result = parse_release_coverage("Show.S01E02.S01-S03.1080p")
        assert result == ParsedReleaseCoverage(season_numbers=(1,), episode_number=2)

    def test_complete_series_flag_without_season_numbers(self):
        result = parse_release_coverage("Show.Complete.Series.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(),
            episode_number=None,
            is_complete_series=True,
        )

    def test_bare_complete_marks_complete_series(self):
        result = parse_release_coverage("Show.Complete.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(),
            episode_number=None,
            is_complete_series=True,
        )

    def test_complete_series_with_season_numbers(self):
        result = parse_release_coverage("Show.S01-S03.Complete.Series.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1, 2, 3),
            episode_number=None,
            is_complete_series=True,
        )

    def test_complete_single_season_prefix_stays_single_season(self):
        result = parse_release_coverage("Show.Complete.S01.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1,),
            episode_number=None,
        )

    def test_complete_single_season_suffix_stays_single_season(self):
        result = parse_release_coverage("Show.S01.Complete.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1,),
            episode_number=None,
        )

    def test_complete_episode_title_stays_exact_episode(self):
        result = parse_release_coverage("Show.S01E02.Complete.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1,),
            episode_number=2,
        )

    def test_no_match_returns_empty_coverage(self):
        result = parse_release_coverage("Movie.4K.Bluray")
        assert result == ParsedReleaseCoverage(season_numbers=(), episode_number=None)

    def test_multi_season_thru_range(self):
        result = parse_release_coverage("Show.Seasons 1 thru 7.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1, 2, 3, 4, 5, 6, 7),
            episode_number=None,
        )

    def test_multi_season_through_range(self):
        result = parse_release_coverage("Show.Season 1 through 5.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(1, 2, 3, 4, 5),
            episode_number=None,
        )

    def test_multi_season_thru_range_case_insensitive(self):
        result = parse_release_coverage("Show.SEASONS 2 THRU 9.1080p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(2, 3, 4, 5, 6, 7, 8, 9),
            episode_number=None,
        )

    def test_multi_season_through_range_with_optional_season_prefix(self):
        result = parse_release_coverage("Show.Seasons 3 through 6.720p")
        assert result == ParsedReleaseCoverage(
            season_numbers=(3, 4, 5, 6),
            episode_number=None,
        )


class TestIsExactSingleEpisodeRelease:
    def test_exact_single_episode_match(self):
        assert is_exact_single_episode_release("Show.S01E02.1080p", 1, 2) is True

    def test_wrong_season(self):
        assert is_exact_single_episode_release("Show.S01E02.1080p", 2, 2) is False

    def test_wrong_episode(self):
        assert is_exact_single_episode_release("Show.S01E02.1080p", 1, 3) is False

    def test_multi_episode_e01e02(self):
        assert is_exact_single_episode_release("Show.S01E01E02.1080p", 1, 1) is False

    def test_range_episode_dash_e02(self):
        assert is_exact_single_episode_release("Show.S01E01-E02.1080p", 1, 1) is False

    def test_season_pack_no_episode(self):
        assert is_exact_single_episode_release("Show.S01.1080p", 1, 1) is False

    def test_complete_series(self):
        assert is_exact_single_episode_release("Show.Complete.1080p", 1, 1) is False

    def test_no_pattern_at_all(self):
        assert is_exact_single_episode_release("Some.Random.Title", 1, 1) is False

    def test_exact_match_with_resolution_suffix(self):
        assert is_exact_single_episode_release("Show.S03E07.720p.WEB-DL", 3, 7) is True

    def test_multi_episode_with_additional_token(self):
        assert is_exact_single_episode_release("Show.S01E02.E03.1080p", 1, 2) is False

    def test_case_insensitive(self):
        assert is_exact_single_episode_release("show.s01e02.1080p", 1, 2) is True

    def test_two_digit_season(self):
        assert is_exact_single_episode_release("Show.S12E05.1080p", 12, 5) is True

    def test_three_digit_episode(self):
        assert is_exact_single_episode_release("Show.S01E100.1080p", 1, 100) is True
