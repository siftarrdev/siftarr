from unittest.mock import MagicMock

from app.siftarr.models.request import MediaType, Request, RequestStatus


def make_request(
    id: int = 1,
    media_type: MediaType = MediaType.MOVIE,
    status: RequestStatus = RequestStatus.SEARCHING,
    tmdb_id: int | None = 12345,
    tvdb_id: int | None = None,
    title: str = "Test",
    seasons: list | None = None,
    plex_rating_key: str | None = None,
) -> MagicMock:
    req = MagicMock(spec=Request)
    req.id = id
    req.media_type = media_type
    req.status = status
    req.tmdb_id = tmdb_id
    req.tvdb_id = tvdb_id
    req.title = title
    req.seasons = seasons or []
    req.requested_episodes = None
    req.plex_rating_key = plex_rating_key
    return req


def make_season(season_number: int, episodes: list) -> MagicMock:
    season = MagicMock()
    season.season_number = season_number
    season.episodes = episodes
    season.status = RequestStatus.SEARCHING
    return season


def make_episode(
    episode_number: int, status: RequestStatus = RequestStatus.SEARCHING
) -> MagicMock:
    episode = MagicMock()
    episode.episode_number = episode_number
    episode.status = status
    episode.air_date = None
    return episode


async def set_request_status(request, status, seasons, availability):
    del availability
    request.status = status
    return seasons
