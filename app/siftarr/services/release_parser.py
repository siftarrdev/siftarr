"""Parse season and episode information from release titles."""

import re
from dataclasses import dataclass


@dataclass
class ParsedSeasonEpisode:
    season_number: int | None
    episode_number: int | None


_SEASON_EPISODE_PATTERNS = [
    re.compile(r"[.()\s_]+S(\d{1,2})E(\d{1,3})(?![0-9])", re.IGNORECASE),
    re.compile(r"[.()\s_]+s(\d{1,2})e(\d{1,3})(?![0-9])", re.IGNORECASE),
]

_SEASON_PACK_PATTERNS = [
    re.compile(r"[.()\s_]+S(\d{1,2})(?![0-9Ee])", re.IGNORECASE),
    re.compile(r"[.()\s_]+Season\.?(\d{1,2})(?![0-9])", re.IGNORECASE),
]


def parse_season_episode(title: str) -> ParsedSeasonEpisode:
    """Parse a release title to extract season and episode numbers.

    Returns:
        ParsedSeasonEpisode with:
        - season_number=None, episode_number=None — unknown/unparsed
        - season_number=N, episode_number=None — season pack for season N
        - season_number=N, episode_number=M — specific episode SNE M
    """
    if not title:
        return ParsedSeasonEpisode(season_number=None, episode_number=None)

    for pattern in _SEASON_EPISODE_PATTERNS:
        match = pattern.search(title)
        if match:
            return ParsedSeasonEpisode(
                season_number=int(match.group(1)),
                episode_number=int(match.group(2)),
            )

    for pattern in _SEASON_PACK_PATTERNS:
        match = pattern.search(title)
        if match:
            return ParsedSeasonEpisode(
                season_number=int(match.group(1)),
                episode_number=None,
            )

    return ParsedSeasonEpisode(season_number=None, episode_number=None)
