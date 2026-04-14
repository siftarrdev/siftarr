"""Parse season and episode information from release titles."""

import re
from dataclasses import dataclass


@dataclass
class ParsedSeasonEpisode:
    season_number: int | None
    episode_number: int | None


@dataclass
class ParsedReleaseCoverage:
    season_numbers: tuple[int, ...]
    episode_number: int | None
    is_complete_series: bool = False

    @property
    def season_number(self) -> int | None:
        """Return the first covered season for legacy callers."""
        if not self.season_numbers:
            return None
        return self.season_numbers[0]


def serialize_release_coverage(coverage: ParsedReleaseCoverage) -> str | None:
    """Serialize multi-season coverage for storage on release records."""
    if coverage.episode_number is not None:
        return None
    if coverage.is_complete_series:
        return "*"
    if len(coverage.season_numbers) <= 1:
        return None
    return ",".join(str(season) for season in coverage.season_numbers)


def parse_stored_release_coverage(
    stored_value: str | None,
    season_number: int | None,
    episode_number: int | None,
) -> ParsedReleaseCoverage:
    """Rebuild release coverage from stored release metadata."""
    if episode_number is not None:
        return ParsedReleaseCoverage(
            season_numbers=(season_number,) if season_number is not None else (),
            episode_number=episode_number,
        )

    if stored_value == "*":
        return ParsedReleaseCoverage(
            season_numbers=(), episode_number=None, is_complete_series=True
        )

    if stored_value:
        season_numbers = tuple(
            int(token) for token in stored_value.split(",") if token.strip().isdigit()
        )
        if season_numbers:
            return ParsedReleaseCoverage(season_numbers=season_numbers, episode_number=None)

    if season_number is not None:
        return ParsedReleaseCoverage(season_numbers=(season_number,), episode_number=None)

    return ParsedReleaseCoverage(season_numbers=(), episode_number=None)


_SEASON_EPISODE_PATTERNS = [
    re.compile(r"(?:^|[.()\s_]+)S(\d{1,2})E(\d{1,3})(?![0-9])", re.IGNORECASE),
]

_SEASON_RANGE_PATTERNS = [
    re.compile(r"(?:^|[.()\s_]+)S(\d{1,2})\s*-\s*S?(\d{1,2})(?![0-9Ee])", re.IGNORECASE),
    re.compile(
        r"(?:^|[.()\s_]+)Season[.\s]?(\d{1,2})\s*-\s*(?:Season[.\s]?)?(\d{1,2})(?![0-9])",
        re.IGNORECASE,
    ),
]

_SEASON_PACK_PATTERNS = [
    re.compile(r"(?:^|[.()\s_-]+)S(\d{1,2})(?![0-9Ee])", re.IGNORECASE),
    re.compile(r"(?:^|[.()\s_-]+)Season[.\s]?(\d{1,2})(?![0-9])", re.IGNORECASE),
]

_COMPLETE_SERIES_PATTERNS = [
    re.compile(
        r"(?:^|[.()\s_]+)(?:The[.()\s_]+)?Complete[.()\s_]+Series(?:$|[.()\s_]+)", re.IGNORECASE
    ),
    re.compile(r"(?:^|[.()\s_]+)All[.()\s_]+Seasons?(?:$|[.()\s_]+)", re.IGNORECASE),
    re.compile(r"(?:^|[.()\s_]+)Complete[.()\s_]+Seasons?(?:$|[.()\s_]+)", re.IGNORECASE),
]


def _expand_season_range(start: int, end: int) -> tuple[int, ...]:
    step = 1 if end >= start else -1
    return tuple(range(start, end + step, step))


def _append_unique(numbers: list[int], values: tuple[int, ...]) -> None:
    for value in values:
        if value not in numbers:
            numbers.append(value)


def parse_release_coverage(title: str) -> ParsedReleaseCoverage:
    """Parse season coverage and episode details from a release title."""
    if not title:
        return ParsedReleaseCoverage(season_numbers=(), episode_number=None)

    for pattern in _SEASON_EPISODE_PATTERNS:
        match = pattern.search(title)
        if match:
            return ParsedReleaseCoverage(
                season_numbers=(int(match.group(1)),),
                episode_number=int(match.group(2)),
            )

    matches: list[tuple[int, int, tuple[int, ...]]] = []
    for pattern in _SEASON_RANGE_PATTERNS:
        for match in pattern.finditer(title):
            matches.append(
                (
                    match.start(),
                    0,
                    _expand_season_range(int(match.group(1)), int(match.group(2))),
                )
            )

    for pattern in _SEASON_PACK_PATTERNS:
        for match in pattern.finditer(title):
            matches.append((match.start(), 1, (int(match.group(1)),)))

    season_numbers: list[int] = []
    for _, _, numbers in sorted(matches):
        _append_unique(season_numbers, numbers)

    return ParsedReleaseCoverage(
        season_numbers=tuple(season_numbers),
        episode_number=None,
        is_complete_series=any(pattern.search(title) for pattern in _COMPLETE_SERIES_PATTERNS),
    )


def parse_season_episode(title: str) -> ParsedSeasonEpisode:
    """Parse a release title to extract season and episode numbers.

    Returns:
        ParsedSeasonEpisode with:
        - season_number=None, episode_number=None — unknown/unparsed
        - season_number=N, episode_number=None — season pack for season N
        - season_number=N, episode_number=M — specific episode SNE M
    """
    coverage = parse_release_coverage(title)
    return ParsedSeasonEpisode(
        season_number=coverage.season_number,
        episode_number=coverage.episode_number,
    )
