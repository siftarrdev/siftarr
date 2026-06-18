"""Helpers for deleting active downloads and resetting request state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.siftarr.models.activity_log import EventType
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.activity_log_service import ActivityLogService
from app.siftarr.services.lifecycle.episode_derive import (
    derive_request_status_from_episodes,
    derive_season_status,
)
from app.siftarr.services.releases.release_parser import cached_parse_release_coverage
from app.siftarr.services.utils.torrent_identity import parse_magnet_info_hash


@dataclass(slots=True)
class DeleteDownloadResult:
    success: bool
    message: str | None = None
    qbit_hash: str | None = None


@dataclass(slots=True)
class _HashResolutionResult:
    success: bool
    qbit_hash: str | None = None
    message: str | None = None


_RESETTABLE_EPISODE_STATUSES = {
    RequestStatus.DOWNLOADING,
    RequestStatus.STAGED,
    RequestStatus.SEARCHING,
}


def _covered_episode(ep, coverage) -> bool:
    season_number = ep.season.season_number
    if coverage.is_complete_series:
        return True
    if coverage.episode_number is not None:
        return (
            season_number == coverage.season_number and ep.episode_number == coverage.episode_number
        )
    return season_number in coverage.season_numbers


class DownloadQueueService:
    def __init__(self, db: AsyncSession, qbittorrent: QbittorrentService) -> None:
        self.db = db
        self.qbittorrent = qbittorrent

    async def delete_download(self, torrent: StagedTorrent) -> DeleteDownloadResult:
        request = await self._load_request(torrent.request_id) if torrent.request_id else None
        hash_result = await self._resolve_qbit_hash(torrent)
        if not hash_result.success:
            return DeleteDownloadResult(False, hash_result.message)

        torrent_hash = hash_result.qbit_hash
        if torrent_hash is not None:
            deleted = await self.qbittorrent.delete_torrent(torrent_hash, delete_files=True)
            if not deleted:
                return DeleteDownloadResult(False, "Failed to delete torrent from qBittorrent")

        torrent.status = "discarded"
        torrent.move_status = None
        torrent.move_error = None
        torrent.moved_path = None
        torrent.moved_at = None

        if request is not None:
            if request.media_type == MediaType.TV:
                self._reset_tv_covered_episodes(request, torrent.title)
            elif request.status not in (RequestStatus.COMPLETED, RequestStatus.DENIED):
                request.status = RequestStatus.PENDING

        await ActivityLogService(self.db).log(
            EventType.DOWNLOAD_STARTED,
            request_id=torrent.request_id,
            details={"torrent_id": torrent.id, "title": torrent.title, "action": "deleted"},
        )
        await self.db.flush()
        return DeleteDownloadResult(True, qbit_hash=torrent_hash)

    async def _resolve_qbit_hash(self, torrent: StagedTorrent) -> _HashResolutionResult:
        candidates = [torrent.info_hash]
        candidates.append(parse_magnet_info_hash(torrent.magnet_url))
        try:
            for candidate in candidates:
                if candidate:
                    info = await self._get_torrent_info(candidate)
                    if info:
                        return _HashResolutionResult(
                            True, str(info.get("hash") or candidate).lower()
                        )
            info = await self._get_torrent_info_by_name(torrent.title)
        except Exception as exc:
            return _HashResolutionResult(
                False, message=f"Failed to look up qBittorrent torrent: {exc}"
            )
        return _HashResolutionResult(
            True, str(info.get("hash")).lower() if info and info.get("hash") else None
        )

    async def _get_torrent_info(self, torrent_hash: str) -> dict[str, Any] | None:
        strict_name = "get_torrent_info_or_raise"
        getter = (
            getattr(self.qbittorrent, strict_name, None)
            if hasattr(type(self.qbittorrent), strict_name)
            else None
        )
        if getter is None:
            getter = self.qbittorrent.get_torrent_info
        return await getter(torrent_hash)

    async def _get_torrent_info_by_name(self, title: str) -> dict[str, Any] | None:
        strict_name = "get_torrent_info_by_name_or_raise"
        getter = (
            getattr(self.qbittorrent, strict_name, None)
            if hasattr(type(self.qbittorrent), strict_name)
            else None
        )
        if getter is None:
            getter = self.qbittorrent.get_torrent_info_by_name
        return await getter(title)

    async def _load_request(self, request_id: int | None) -> Request | None:
        if request_id is None:
            return None
        result = await self.db.execute(
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
        )
        return result.scalar_one_or_none()

    def _reset_tv_covered_episodes(self, request: Request, title: str) -> None:
        coverage = cached_parse_release_coverage(title)
        all_episodes = [ep for season in request.seasons for ep in season.episodes]
        for ep in all_episodes:
            if ep.status in _RESETTABLE_EPISODE_STATUSES and _covered_episode(ep, coverage):
                ep.status = RequestStatus.PENDING
        for season in request.seasons:
            season.status = derive_season_status(list(season.episodes))
        request.status = derive_request_status_from_episodes(all_episodes)
