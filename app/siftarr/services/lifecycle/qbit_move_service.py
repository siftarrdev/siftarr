"""qBittorrent move and retention workflow."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.config import Settings
from app.siftarr.models._base import utc_now
from app.siftarr.models.activity_log import EventType
from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService

logger = logging.getLogger(__name__)

TV_PATTERN = re.compile(
    r"[._\s](S\d{2}E\d{2}|Seasons?[\s._]?\d{1,2}(-\d{1,2})?|S\d{2}(?![._\s]?E\d{2}))",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")
_UNSAFE_PATH_CHARS_RE = re.compile(r"[\\/:*?\"<>|]+")


@dataclass(frozen=True)
class QbitMoveResult:
    moved: int = 0
    removed: int = 0
    errors: int = 0


@dataclass(frozen=True)
class _MoveCandidate:
    torrent: dict[str, Any]
    destination: Path
    staged: StagedTorrent | None = None
    request: Request | None = None


def _normalize_name(name: str | None) -> str:
    return _NON_ALNUM_RE.sub(" ", name or "").strip().lower()


def _safe_folder_name(value: str | None, fallback: str) -> str:
    cleaned = _UNSAFE_PATH_CHARS_RE.sub(" ", value or "").strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class QbitMoveService:
    """Moves completed torrents into media roots and prunes old qBit entries."""

    def __init__(
        self,
        db: AsyncSession,
        qbittorrent_service: QbittorrentService,
        settings: Settings,
        *,
        log: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.qbittorrent = qbittorrent_service
        self.settings = settings
        self.logger = log or logger
        self.activity_log = ActivityLogService(db)

    async def run(self) -> QbitMoveResult:
        """Run move processing, then retention cleanup, when enabled."""
        if not self.settings.qbittorrent_move_enabled:
            self.logger.debug("QbitMoveService: disabled, skipping")
            return QbitMoveResult()

        completed = await self.qbittorrent.get_completed_torrents()
        candidates = await self._select_move_candidates(completed)
        moved = errors = 0
        for candidate in candidates:
            if await self._move_candidate(candidate):
                moved += 1
            else:
                errors += 1

        removed = await self._cleanup_retention(completed)
        return QbitMoveResult(moved=moved, removed=removed, errors=errors)

    async def _select_move_candidates(
        self, completed: list[dict[str, Any]]
    ) -> list[_MoveCandidate]:
        completed_by_hash = {
            str(torrent["hash"]).lower(): torrent
            for torrent in completed
            if isinstance(torrent.get("hash"), str) and torrent.get("hash")
        }

        result = await self.db.execute(
            select(StagedTorrent, Request)
            .outerjoin(Request, Request.id == StagedTorrent.request_id)
            .where(
                StagedTorrent.status == "approved",
                or_(StagedTorrent.move_status.is_(None), StagedTorrent.move_status != "moved"),
            )
        )
        rows = list(result.all())

        candidates: list[_MoveCandidate] = []
        managed_hashes: set[str] = set()
        used_hashes: set[str] = set()
        for staged, request in rows:
            torrent = self._match_completed_torrent(
                staged, completed, completed_by_hash, used_hashes
            )
            if torrent is None:
                continue
            torrent_hash = str(torrent.get("hash") or "").lower()
            if torrent_hash:
                managed_hashes.add(torrent_hash)
                used_hashes.add(torrent_hash)
            destination = self._destination_for(torrent, request=request, strict=True)
            if destination is None:
                await self._mark_managed_error(staged, "unsafe_destination", torrent)
                continue
            candidates.append(
                _MoveCandidate(
                    torrent=torrent, destination=destination, staged=staged, request=request
                )
            )

        if not self.settings.qbittorrent_move_unmanaged_fallback_enabled:
            return candidates

        completed_dir = _resolved(self.settings.qbittorrent_move_completed_dir)
        for torrent in completed:
            torrent_hash = str(torrent.get("hash") or "").lower()
            if torrent_hash and torrent_hash in managed_hashes:
                continue
            save_path = torrent.get("save_path") or torrent.get("download_location")
            if not save_path or not _is_relative_to(_resolved(str(save_path)), completed_dir):
                continue
            destination = self._destination_for(torrent, request=None, strict=True)
            if destination is None:
                self.logger.warning(
                    "QbitMoveService: unsafe unmanaged destination for %s", torrent.get("name")
                )
                continue
            candidates.append(_MoveCandidate(torrent=torrent, destination=destination))
        return candidates

    def _match_completed_torrent(
        self,
        staged: StagedTorrent,
        completed: list[dict[str, Any]],
        completed_by_hash: dict[str, dict[str, Any]],
        used_hashes: set[str],
    ) -> dict[str, Any] | None:
        if staged.info_hash:
            torrent = completed_by_hash.get(staged.info_hash.lower())
            if torrent is not None:
                return torrent

        title_norm = _normalize_name(staged.title)
        if not title_norm:
            return None
        for torrent in completed:
            torrent_hash = str(torrent.get("hash") or "").lower()
            if torrent_hash and torrent_hash in used_hashes:
                continue
            if title_norm in _normalize_name(str(torrent.get("name") or "")):
                return torrent
        return None

    def _destination_for(
        self,
        torrent: dict[str, Any],
        *,
        request: Request | None,
        strict: bool,
    ) -> Path | None:
        movie_root = _resolved(self.settings.qbittorrent_move_movie_root)
        tv_root = _resolved(self.settings.qbittorrent_move_tv_root)

        if request is not None and request.media_type == MediaType.TV:
            destination = tv_root / _safe_folder_name(request.title, "Unknown Show")
            return (
                destination
                if not strict or _is_relative_to(_resolved(destination), tv_root)
                else None
            )

        if request is not None and request.media_type == MediaType.MOVIE:
            destination = movie_root
            return (
                destination
                if not strict or _is_relative_to(_resolved(destination), movie_root)
                else None
            )

        name = str(torrent.get("name") or "")
        match = TV_PATTERN.search(name)
        if match:
            raw_show = re.sub(
                r"\s\d{4}$", "", name[: match.start()].replace(".", " ").replace("_", " ")
            ).strip()
            destination = tv_root / _safe_folder_name(raw_show.title(), "Unknown Show")
            return (
                destination
                if not strict or _is_relative_to(_resolved(destination), tv_root)
                else None
            )

        destination = movie_root
        return (
            destination
            if not strict or _is_relative_to(_resolved(destination), movie_root)
            else None
        )

    async def _move_candidate(self, candidate: _MoveCandidate) -> bool:
        torrent_hash = candidate.torrent.get("hash")
        if not isinstance(torrent_hash, str) or not torrent_hash:
            if candidate.staged is not None:
                await self._mark_managed_error(candidate.staged, "missing_hash", candidate.torrent)
            return False

        success = await self.qbittorrent.set_torrent_location(
            torrent_hash,
            str(candidate.destination),
            move=True,
        )
        if candidate.staged is None:
            if success:
                self.logger.info(
                    "QbitMoveService: moved unmanaged torrent %s -> %s",
                    candidate.torrent.get("name"),
                    candidate.destination,
                )
            else:
                self.logger.error(
                    "QbitMoveService: failed moving unmanaged torrent %s", torrent_hash
                )
            return success

        if success:
            candidate.staged.move_status = "moved"
            candidate.staged.moved_path = str(candidate.destination)
            candidate.staged.move_error = None
            candidate.staged.moved_at = utc_now()
            await self.activity_log.log(
                EventType.REQUEST_STATUS_CHANGED,
                request_id=candidate.staged.request_id,
                details={
                    "action": "qbit_move",
                    "torrent_id": candidate.staged.id,
                    "hash": torrent_hash,
                    "destination": str(candidate.destination),
                },
            )
            await self.db.commit()
            return True

        await self._mark_managed_error(candidate.staged, "set_location_failed", candidate.torrent)
        return False

    async def _mark_managed_error(
        self,
        staged: StagedTorrent,
        error: str,
        torrent: dict[str, Any] | None = None,
    ) -> None:
        staged.move_status = "error"
        staged.move_error = error
        await self.activity_log.log(
            EventType.ERROR,
            request_id=staged.request_id,
            details={
                "action": "qbit_move",
                "torrent_id": staged.id,
                "hash": torrent.get("hash") if torrent else staged.info_hash,
                "error": error,
            },
        )
        await self.db.commit()

    async def _cleanup_retention(self, completed: list[dict[str, Any]]) -> int:
        retention_seconds = self.settings.qbittorrent_move_retention_weeks * 7 * 24 * 60 * 60
        hashes = [
            str(torrent["hash"])
            for torrent in completed
            if isinstance(torrent.get("hash"), str)
            and int(torrent.get("seeding_time") or 0) > retention_seconds
        ]
        if not hashes:
            return 0
        if await self.qbittorrent.delete_torrents(hashes, delete_files=False):
            self.logger.info("QbitMoveService: removed %d old completed torrent(s)", len(hashes))
            return len(hashes)
        self.logger.error("QbitMoveService: failed removing old completed torrents")
        return 0
