"""Persistence and matching for release rejection/cooldown decisions."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.release_disposition import ReleaseDisposition
from app.siftarr.services.releases.release_parser import (
    parse_release_coverage,
    parse_release_episode_keys,
)


def release_identity_key(release: object) -> str:
    """Return the metadata alias, which survives a hash learned after rejection."""
    title = re.sub(r"[^a-z0-9]+", " ", str(getattr(release, "title", "")).lower()).strip()
    raw = f"{title}\0{getattr(release, 'size', 0)}\0{str(getattr(release, 'indexer', '')).lower()}"
    return "meta:" + hashlib.sha256(raw.encode()).hexdigest()


def release_identity_aliases(release: object) -> set[str]:
    aliases = {release_identity_key(release)}
    info_hash = getattr(release, "info_hash", None)
    if isinstance(info_hash, str) and info_hash.strip():
        aliases.add("hash:" + info_hash.strip().lower())
    magnet = getattr(release, "magnet_url", None)
    if isinstance(magnet, str):
        match = re.search(r"urn:btih:([a-z0-9]+)", magnet, re.I)
        if match:
            aliases.add("hash:" + match.group(1).lower())
    return aliases


def release_hash(release: object) -> str | None:
    for alias in sorted(release_identity_aliases(release)):
        if not alias.startswith("hash:"):
            continue
        value = alias[5:]
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            return value
        if re.fullmatch(r"[a-z2-7]{32}", value):
            return base64.b32decode(value.upper()).hex()
    return None


def _identity_predicate(release: object, media_type: object):
    metadata = (ReleaseDisposition.release_key == release_identity_key(release)) & (
        ReleaseDisposition.target_scope == release_target_scope(release, media_type=media_type)
    )
    info_hash = release_hash(release)
    return or_(metadata, ReleaseDisposition.info_hash == info_hash) if info_hash else metadata


def release_target_scope(release: object, *, media_type: object | None = None) -> str:
    if str(getattr(media_type, "value", media_type)) != "tv":
        return "request"
    coverage = parse_release_coverage(str(getattr(release, "title", "")))
    payload = {
        "seasons": list(coverage.season_numbers),
        "episode": coverage.episode_number,
        "episodes": parse_release_episode_keys(str(getattr(release, "title", ""))),
        "complete": coverage.is_complete_series,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class ReleaseDispositionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_rejection(
        self,
        request_id: int,
        release: object,
        *,
        media_type: object,
        reason: str,
        staged_torrent_id: int | None = None,
    ) -> ReleaseDisposition:
        key = release_identity_key(release)
        scope = release_target_scope(release, media_type=media_type)
        existing = (
            (
                await self.db.execute(
                    select(ReleaseDisposition).where(
                        ReleaseDisposition.request_id == request_id,
                        _identity_predicate(release, media_type),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            existing = ReleaseDisposition(
                request_id=request_id, release_key=key, target_scope=scope
            )
            self.db.add(existing)
        existing.kind = "rejected"
        existing.info_hash = release_hash(release) or existing.info_hash
        existing.reason = reason
        existing.expires_at = None
        existing.staged_torrent_id = staged_torrent_id
        await self.db.flush()
        return existing

    async def blocked(
        self, request_id: int, release: object, *, media_type: object
    ) -> ReleaseDisposition | None:
        now = datetime.now(UTC).replace(tzinfo=None)
        result = await self.db.execute(
            select(ReleaseDisposition).where(
                ReleaseDisposition.request_id == request_id,
                _identity_predicate(release, media_type),
                ReleaseDisposition.expires_at.is_(None) | (ReleaseDisposition.expires_at > now),
            )
        )
        disposition = result.scalars().first()
        if not isinstance(disposition, ReleaseDisposition):
            return None
        disposition.info_hash = disposition.info_hash or release_hash(release)
        return disposition

    async def blocked_keys(
        self, request_id: int, releases: list[object], *, media_type: object
    ) -> dict[int, ReleaseDisposition]:
        """Load once and map candidate object identities to active dispositions."""
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = list(
            (
                await self.db.execute(
                    select(ReleaseDisposition).where(
                        ReleaseDisposition.request_id == request_id,
                        (
                            ReleaseDisposition.expires_at.is_(None)
                            | (ReleaseDisposition.expires_at > now)
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        blocked: dict[int, ReleaseDisposition] = {}
        for release in releases:
            aliases = release_identity_aliases(release)
            scope = release_target_scope(release, media_type=media_type)
            info_hash = release_hash(release)
            match = next(
                (
                    row
                    for row in rows
                    if (
                        (row.release_key in aliases and row.target_scope == scope)
                        or (info_hash is not None and row.info_hash == info_hash)
                    )
                ),
                None,
            )
            if match is not None:
                match.info_hash = match.info_hash or info_hash
                blocked[id(release)] = match
        return blocked

    async def clear(self, request_id: int, release: object, *, media_type: object) -> None:
        await self.db.execute(
            delete(ReleaseDisposition).where(
                ReleaseDisposition.request_id == request_id,
                _identity_predicate(release, media_type),
            )
        )
