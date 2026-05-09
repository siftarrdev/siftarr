from dataclasses import dataclass
from typing import Any

# Modern Plex agents (tv.plex.agents.series, tv.plex.agents.movie) store
# external IDs in "tmdb://ID" / "tvdb://ID" format inside each item's
# Guid array.  The legacy /library/search?guid= endpoint only accepts the
# old "com.plexapp.agents.themoviedb://ID" / "com.plexapp.agents.thetvdb://ID"
# format *and* modern Plex versions may return 400 for guid searches entirely.
#
# We try both GUID formats via /library/search?guid=, and if both fail we
# fall back to scanning all items in the relevant library section and
# matching by the Guid array.
_MODERN_GUID_PREFIXES: dict[str, list[str]] = {
    # key: search-prefix  value: list of prefixes to try, newest first
    "tmdb": ["tmdb://", "com.plexapp.agents.themoviedb://"],
    "tvdb": ["tvdb://", "com.plexapp.agents.thetvdb://"],
}


@dataclass(slots=True)
class PlexLookupResult:
    """Result for Plex lookups that can distinguish missing vs inconclusive."""

    item: dict[str, Any] | None
    authoritative: bool
    matched_guid: str | None = None
    failed_sections: tuple[str, ...] = ()


@dataclass(slots=True)
class PlexLibraryScanResult:
    """Result for a full/recent library scan with authoritative status."""

    media_type: str
    items: tuple[dict[str, Any], ...]
    authoritative: bool
    failed_sections: tuple[str, ...] = ()


@dataclass(slots=True)
class PlexEpisodeAvailabilityResult:
    """Episode availability with authoritative status."""

    availability: dict[tuple[int, int], bool]
    authoritative: bool


class PlexTransientScanError(RuntimeError):
    """Raised when a Plex section scan could not complete authoritatively."""
