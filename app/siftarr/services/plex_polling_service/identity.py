"""Identity helpers for request grouping and scan deduplication."""

from datetime import UTC, datetime

from app.siftarr.models.request import MediaType, Request

from .models import MediaIdentity


class IdentityMixin:
    def _group_requests_by_media_identity(
        self, requests: list[Request], *, dedupe_within_cycle: bool
    ) -> dict[MediaIdentity, tuple[Request, ...]]:
        grouped: dict[MediaIdentity, list[Request]] = {}
        for req in requests:
            identity = self._get_media_identity(req, dedupe_within_cycle=dedupe_within_cycle)
            grouped.setdefault(identity, []).append(req)
        return {identity: tuple(group) for identity, group in grouped.items()}

    def _index_requests_by_media_identity(
        self, requests: list[Request]
    ) -> dict[MediaIdentity, tuple[Request, ...]]:
        indexed: dict[MediaIdentity, dict[int, Request]] = {}
        for req in requests:
            for identity in self._get_request_media_identity_candidates(req):
                indexed.setdefault(identity, {})[req.id] = req
        return {
            identity: tuple(requests_by_id.values()) for identity, requests_by_id in indexed.items()
        }

    def _get_media_identity(self, req: Request, *, dedupe_within_cycle: bool) -> MediaIdentity:
        request_id = req.id
        if not dedupe_within_cycle:
            return MediaIdentity(req.media_type, request_id=request_id)

        plex_rating_key = getattr(req, "plex_rating_key", None)
        if req.media_type == MediaType.MOVIE:
            if plex_rating_key:
                return MediaIdentity(MediaType.MOVIE, plex_rating_key=plex_rating_key)
            if req.tmdb_id is not None:
                return MediaIdentity(MediaType.MOVIE, tmdb_id=req.tmdb_id)
            return MediaIdentity(MediaType.MOVIE, request_id=request_id)

        if plex_rating_key:
            return MediaIdentity(MediaType.TV, plex_rating_key=plex_rating_key)
        if req.tmdb_id is not None:
            return MediaIdentity(MediaType.TV, tmdb_id=req.tmdb_id)
        if req.tvdb_id is not None:
            return MediaIdentity(MediaType.TV, tvdb_id=req.tvdb_id)
        return MediaIdentity(MediaType.TV, request_id=request_id)

    def _get_request_media_identity_candidates(self, req: Request) -> set[MediaIdentity]:
        candidates = {self._get_media_identity(req, dedupe_within_cycle=True)}
        plex_rating_key = getattr(req, "plex_rating_key", None)
        if plex_rating_key:
            candidates.add(MediaIdentity(req.media_type, plex_rating_key=plex_rating_key))
        if req.tmdb_id is not None:
            candidates.add(MediaIdentity(req.media_type, tmdb_id=req.tmdb_id))
        if req.media_type == MediaType.TV and req.tvdb_id is not None:
            candidates.add(MediaIdentity(MediaType.TV, tvdb_id=req.tvdb_id))
        return candidates

    def _coerce_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _extract_guid_ids(self, item: dict[str, object]) -> tuple[int | None, int | None]:
        tmdb_id: int | None = None
        tvdb_id: int | None = None

        guid_values = item.get("guids")
        for guid in guid_values if isinstance(guid_values, tuple | list) else ():
            guid_value = str(guid)
            prefix, _, raw_id = guid_value.partition("://")
            if not raw_id.isdigit():
                continue
            if prefix in {"tmdb", "com.plexapp.agents.themoviedb"} and tmdb_id is None:
                tmdb_id = int(raw_id)
            if prefix in {"tvdb", "com.plexapp.agents.thetvdb"} and tvdb_id is None:
                tvdb_id = int(raw_id)
        return tmdb_id, tvdb_id

    def _get_recent_item_canonical_identity(self, item: dict[str, object]) -> MediaIdentity | None:
        media_type = self._get_request_media_type_for_item(item)
        if media_type is None:
            return None

        tmdb_id, tvdb_id = self._extract_guid_ids(item)
        rating_key = item.get("rating_key")

        if tmdb_id is not None:
            return MediaIdentity(media_type, tmdb_id=tmdb_id)
        if media_type == MediaType.TV and tvdb_id is not None:
            return MediaIdentity(MediaType.TV, tvdb_id=tvdb_id)
        if rating_key:
            return MediaIdentity(media_type, plex_rating_key=str(rating_key))
        return None

    def _get_recent_item_identity_candidates(self, item: dict[str, object]) -> set[MediaIdentity]:
        media_type = self._get_request_media_type_for_item(item)
        if media_type is None:
            return set()

        candidates: set[MediaIdentity] = set()
        canonical = self._get_recent_item_canonical_identity(item)
        if canonical is not None:
            candidates.add(canonical)

        rating_key = item.get("rating_key")
        if rating_key:
            candidates.add(MediaIdentity(media_type, plex_rating_key=str(rating_key)))

        tmdb_id, tvdb_id = self._extract_guid_ids(item)
        if tmdb_id is not None:
            candidates.add(MediaIdentity(media_type, tmdb_id=tmdb_id))
        if media_type == MediaType.TV and tvdb_id is not None:
            candidates.add(MediaIdentity(MediaType.TV, tvdb_id=tvdb_id))
        return candidates

    def _get_request_media_type_for_item(self, item: dict[str, object]) -> MediaType | None:
        item_type = item.get("type")
        if item_type == "movie":
            return MediaType.MOVIE
        if item_type == "show":
            return MediaType.TV
        return None
