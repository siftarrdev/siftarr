"""Tests for the release parser."""

from app.siftarr.services.release_parser import ParsedSeasonEpisode, parse_season_episode


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
