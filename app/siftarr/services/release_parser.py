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


SINGLE_EPISODE_RELEASE_PATTERN = re.compile(
    r"(?:^|[.()\s_]+)S(?P<season>\d{1,2})E(?P<episode>\d{1,3})(?!\d)",
    re.IGNORECASE,
)
FOLLOWUP_EPISODE_TOKEN_PATTERN = re.compile(
    r"^[.()\s_-]*(?:E\d{1,3}|-\s*E?\d{1,3})(?!\d)",
    re.IGNORECASE,
)
ADDITIONAL_EPISODE_TOKEN_PATTERN = re.compile(
    r"(?:^|[.()\s_-]+)E\d{1,3}(?!\d)",
    re.IGNORECASE,
)


def is_exact_single_episode_release(title: str, season_number: int, episode_number: int) -> bool:
    """Return True when the title identifies exactly one requested episode."""
    match = SINGLE_EPISODE_RELEASE_PATTERN.search(title)
    if not match:
        return False

    if int(match.group("season")) != season_number:
        return False
    if int(match.group("episode")) != episode_number:
        return False

    remainder = title[match.end() :]
    if FOLLOWUP_EPISODE_TOKEN_PATTERN.match(remainder):
        return False
    if SINGLE_EPISODE_RELEASE_PATTERN.search(remainder):
        return False
    return not ADDITIONAL_EPISODE_TOKEN_PATTERN.search(remainder)


@dataclass(frozen=True)
class MovieReleaseIdentity:
    title: str | None
    year: int | None


_MOVIE_YEAR_PATTERN = re.compile(
    r"(?:^|[.()\s_\-\[\]])(?P<year>(?:19|20)\d{2})(?:$|[.()\s_\-\[\]])"
)
_MOVIE_QUALITY_TOKEN_PATTERN = re.compile(
    r"(?:^|[.()\s_\-\[\]])(?:720p|1080p|2160p|480p|web(?:-?dl)?|bluray|brrip|hdrip|dvdrip|hdtv|remux|x264|x265|h264|h265|hevc|av1)(?:$|[.()\s_\-\[\]])",
    re.IGNORECASE,
)
_MOVIE_QUALITY_IDENTITY_TOKENS = {
    "480p",
    "720p",
    "1080p",
    "2160p",
    "web",
    "dl",
    "webrip",
    "bluray",
    "brrip",
    "hdrip",
    "dvdrip",
    "hdtv",
    "remux",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "av1",
}


def normalize_movie_title_identity(title: str | None) -> str:
    """Normalize a movie title for release/request identity comparisons."""
    if not title:
        return ""
    normalized = re.sub(r"[\W_]+", " ", title.casefold())
    return " ".join(normalized.split())


def _movie_year_token(token: str) -> int | None:
    if re.fullmatch(r"(?:19|20)\d{2}", token):
        return int(token)
    return None


def _movie_year_after_exact_request_title(
    *, request_title: str | None, release_title: str
) -> tuple[bool, int | None]:
    expected_tokens = normalize_movie_title_identity(request_title).split()
    release_tokens = normalize_movie_title_identity(release_title).split()
    if not expected_tokens or release_tokens[: len(expected_tokens)] != expected_tokens:
        return (False, None)

    remaining_tokens = release_tokens[len(expected_tokens) :]
    if not remaining_tokens or remaining_tokens[0] in _MOVIE_QUALITY_IDENTITY_TOKENS:
        return (True, None)

    release_year = _movie_year_token(remaining_tokens[0])
    if release_year is None:
        return (False, None)
    return (True, release_year)


def parse_movie_release_identity(release_title: str) -> MovieReleaseIdentity:
    """Parse the likely movie title/year prefix from a torrent release title."""
    if not release_title:
        return MovieReleaseIdentity(title=None, year=None)

    year_match = _MOVIE_YEAR_PATTERN.search(release_title)
    if year_match:
        title_part = release_title[: year_match.start()]
        return MovieReleaseIdentity(
            title=title_part.strip(" ._-[()]") or None,
            year=int(year_match.group("year")),
        )

    quality_match = _MOVIE_QUALITY_TOKEN_PATTERN.search(release_title)
    if quality_match:
        title_part = release_title[: quality_match.start()]
        return MovieReleaseIdentity(title=title_part.strip(" ._-[()]") or None, year=None)

    return MovieReleaseIdentity(title=release_title.strip(" ._-[()]") or None, year=None)


def movie_release_identity_rejection_reason(
    *,
    request_title: str | None,
    request_year: int | None,
    release_title: str,
) -> str | None:
    """Return a rejection reason when a movie release appears to be the wrong title/year."""
    parsed = parse_movie_release_identity(release_title)
    expected_title = normalize_movie_title_identity(request_title)
    parsed_title = normalize_movie_title_identity(parsed.title)
    exact_title_matches, exact_title_release_year = _movie_year_after_exact_request_title(
        request_title=request_title, release_title=release_title
    )

    if exact_title_matches:
        if (
            request_year is not None
            and exact_title_release_year is not None
            and exact_title_release_year != request_year
        ):
            return (
                "Movie identity mismatch: release year "
                f"{exact_title_release_year} does not match request year {request_year}"
            )
        return None

    if expected_title and parsed_title and parsed_title != expected_title:
        return (
            "Movie identity mismatch: release title "
            f"'{parsed.title}' does not match request title '{request_title}'"
        )

    if request_year is not None and parsed.year is not None and parsed.year != request_year:
        return (
            "Movie identity mismatch: release year "
            f"{parsed.year} does not match request year {request_year}"
        )

    return None


_SEASON_EPISODE_PATTERNS = [
    re.compile(r"(?:^|[.()\s_]+)S(\d{1,2})E(\d{1,3})(?![0-9])", re.IGNORECASE),
]

_SEASON_RANGE_PATTERNS = [
    re.compile(r"(?:^|[.()\s_]+)S(\d{1,2})\s*-\s*S?(\d{1,2})(?![0-9Ee])", re.IGNORECASE),
    re.compile(
        r"(?:^|[.()\s_]+)Season[.\s]?(\d{1,2})\s*-\s*(?:Season[.\s]?)?(\d{1,2})(?![0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.()\s_]+)Seasons?[.\s]+(\d{1,2})\s+(?:thru|through)\s+(?:Seasons?[.\s]?)?(\d{1,2})(?![0-9])",
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
    re.compile(r"(?:^|[.()\s_]+)Complete(?:$|[.()\s_]+)", re.IGNORECASE),
]


def _expand_season_range(start: int, end: int) -> tuple[int, ...]:
    step = 1 if end >= start else -1
    return tuple(range(start, end + step, step))


def _append_unique(numbers: list[int], values: tuple[int, ...]) -> None:
    for value in values:
        if value not in numbers:
            numbers.append(value)


def _is_complete_series_match(
    title: str,
    season_numbers: tuple[int, ...],
    episode_number: int | None,
) -> bool:
    if episode_number is not None:
        return False

    if not any(pattern.search(title) for pattern in _COMPLETE_SERIES_PATTERNS):
        return False

    return len(season_numbers) != 1


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

    parsed_season_numbers = tuple(season_numbers)

    return ParsedReleaseCoverage(
        season_numbers=parsed_season_numbers,
        episode_number=None,
        is_complete_series=_is_complete_series_match(
            title,
            parsed_season_numbers,
            None,
        ),
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
