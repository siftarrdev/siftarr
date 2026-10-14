"""Service for interacting with qBittorrent API."""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import qbittorrentapi

from app.siftarr.config import Settings, get_settings
from app.siftarr.services.utils.torrent_identity import (
    normalize_torrent_name,
    parse_magnet_info_hash,
    torrent_file_info_hash,
)
from app.siftarr.services.utils.type_utils import normalize_float

logger = logging.getLogger(__name__)


class MediaCategory(StrEnum):
    """Media categories for torrent categorization."""

    MOVIES = "radarr"
    TV = "sonarr"


@dataclass(slots=True)
class BulkTorrentPayload:
    """Torrent payload for qBittorrent bulk add."""

    key: int | str
    title: str
    torrent_path: str | None = None
    magnet_uri: str | None = None
    category: MediaCategory = MediaCategory.MOVIES
    download_path: str | None = None
    is_paused: bool = False
    ratio_limit: float | None = None
    seeding_time_limit: int | None = None


@dataclass(slots=True)
class BulkAddResult:
    """Per-torrent result from qBittorrent bulk add."""

    key: int | str
    success: bool
    torrent_hash: str | None = None
    error: str | None = None


_BULK_ADD_CHUNK_SIZE = 10

_TORRENT_FIELDS = (
    "hash",
    "name",
    "size",
    "progress",
    "state",
    "category",
    "ratio",
    "added_on",
    "last_activity",
    "completed_on",
    "save_path",
    "download_location",
    "seeding_time",
    "eta",
    "dlspeed",
    "upspeed",
    "downloaded",
    "uploaded",
)


def _qbit_response_value(response: Any, name: str) -> Any:
    if isinstance(response, dict):
        return response.get(name)
    get = getattr(response, "get", None)
    if callable(get):
        try:
            return get(name)
        except Exception:
            pass
    return getattr(response, name, None)


def _failure_count(response: Any) -> int | None:
    """Return the response failure count, or None when missing or not an int."""
    failure_count = _qbit_response_value(response, "failure_count")
    if failure_count is None:
        return None
    try:
        return int(failure_count)
    except TypeError, ValueError:
        return None


def _qbit_add_response_accepted(response: Any) -> bool:
    """Return True when qBit/qbittorrentapi accepted an add request."""
    if response == "Ok.":
        return True
    failure_count = _failure_count(response)
    if failure_count is None or failure_count > 0:
        return False
    return any(
        _qbit_response_value(response, name) is not None
        for name in ("pending_count", "success_count", "added_torrent_ids")
    )


def _qbit_add_response_failed(response: Any) -> bool:
    failure_count = _failure_count(response)
    return failure_count is not None and failure_count > 0


class QbittorrentService:
    """Service for interacting with qBittorrent API."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the qBittorrent service."""
        self.settings = settings or get_settings()
        self._client: qbittorrentapi.Client | None = None

    @property
    def client(self) -> qbittorrentapi.Client:
        """Get or create qBittorrent client."""
        if self._client is None:
            self._client = qbittorrentapi.Client(
                host=str(self.settings.qbittorrent_url),
                EXTRA_HEADERS={"Authorization": f"Bearer {self.settings.qbittorrent_api_key}"},
            )
        return self._client

    @staticmethod
    def _serialize_torrent(torrent: Any) -> dict[str, Any]:
        return {field: getattr(torrent, field, None) for field in _TORRENT_FIELDS}

    async def authenticate(self) -> bool:
        """Authenticate with qBittorrent.

        Returns:
            True if authentication successful, False otherwise.
        """
        try:
            await asyncio.to_thread(lambda: self.client.app.web_api_version)
            return True
        except qbittorrentapi.LoginFailed:
            return False

    async def ensure_category_exists(self, category: str) -> bool:
        """Ensure a category exists in qBittorrent, create if needed.

        Args:
            category: The category name to ensure exists.

        Returns:
            True if category exists or was created, False otherwise.
        """
        try:
            categories = await asyncio.to_thread(self.client.torrents_categories)
            if category not in categories:
                await asyncio.to_thread(
                    self.client.torrents_create_category,
                    name=category,
                    save_path=None,
                )
            return True
        except Exception:
            return False

    async def add_torrent(
        self,
        torrent_path: str | None = None,
        magnet_uri: str | None = None,
        category: MediaCategory = MediaCategory.MOVIES,
        download_path: str | None = None,
        is_paused: bool = False,
        ratio_limit: float | None = None,
        seeding_time_limit: int | None = None,
    ) -> str | None:
        """Add a torrent to qBittorrent (idempotent).

        If the torrent already exists in qBittorrent (detected by info hash),
        this returns the existing hash instead of adding a duplicate.

        Args:
            torrent_path: Path to .torrent file (mutually exclusive with magnet_uri).
            magnet_uri: Magnet URI (mutually exclusive with torrent_path).
            category: Category to assign (radarr for movies, sonarr for TV).
            download_path: Optional custom download path.
            is_paused: Start paused.
            ratio_limit: Seed ratio limit.
            seeding_time_limit: Seeding time limit in minutes.

        Returns:
            Torrent hash if successful, None otherwise.
        """
        # ── 1. Compute the info hash for duplicate detection ──
        info_hash: str | None = None
        if magnet_uri:
            info_hash = parse_magnet_info_hash(magnet_uri)
        elif torrent_path:
            info_hash = torrent_file_info_hash(torrent_path)

        # ── 2. Check if already in qBittorrent (idempotent) ──
        if info_hash:
            existing = await self.get_torrent_info(info_hash)
            if existing:
                logger.info("Torrent already in qBittorrent (hash=%s), skipping add", info_hash)
                return info_hash

        # ── 3. Add to qBittorrent ──
        try:
            await self.ensure_category_exists(category.value)

            source: dict[str, Any]
            if magnet_uri:
                source = {"urls": magnet_uri}
            elif torrent_path:
                with open(torrent_path, "rb") as f:
                    source = {"torrent_files": [f.read()]}
            else:
                raise ValueError("Either torrent_path or magnet_uri must be provided")

            result = await asyncio.to_thread(
                self.client.torrents_add,
                **source,
                category=category.value,
                is_paused=is_paused,
                download_path=download_path,
                ratio_limit=ratio_limit,
                seeding_time_limit=seeding_time_limit,
            )

            if _qbit_add_response_accepted(result):
                # qBit accepts add requests before metadata is always queryable.
                # Return a known hash when available, otherwise return an
                # accepted sentinel so callers do not treat delayed visibility
                # as a failed submission.
                if info_hash:
                    return info_hash
                if magnet_uri:
                    try:
                        torrents = await asyncio.to_thread(self.client.torrents_info)
                        for t in sorted(torrents, key=lambda t: t.added_on, reverse=True):
                            if magnet_uri in (t.magnet_uri or ""):
                                return str(t.hash)
                    except Exception:
                        logger.debug(
                            "qBit add accepted before torrent list confirmed", exc_info=True
                        )
                return "Ok."
            if info_hash:
                existing = await self.get_torrent_info(info_hash)
                if existing:
                    logger.info(
                        "Torrent add returned %r but torrent already exists (hash=%s)",
                        result,
                        info_hash,
                    )
                    return info_hash
            return None
        except Exception as e:
            logger.error("Error adding torrent: %s", e)
            return None

    async def add_torrents_bulk(
        self,
        payloads: list[BulkTorrentPayload],
    ) -> list[BulkAddResult]:
        """Add multiple torrents using grouped qBittorrent bulk calls.

        Results are per payload. Known existing torrents are treated as success.
        For qBit's coarse ``Ok.`` batch response, success means qBit accepted
        the submission; metadata may not be queryable until qBit finishes
        processing the batch.
        """
        if not payloads:
            return []

        results: dict[int | str, BulkAddResult] = {}
        pending: list[tuple[BulkTorrentPayload, str | None]] = []
        for payload in payloads:
            info_hash = None
            if payload.magnet_uri:
                info_hash = parse_magnet_info_hash(payload.magnet_uri)
            elif payload.torrent_path:
                info_hash = torrent_file_info_hash(payload.torrent_path)

            if info_hash:
                existing = await self.get_torrent_info(info_hash)
                if existing:
                    results[payload.key] = BulkAddResult(
                        key=payload.key,
                        success=True,
                        torrent_hash=info_hash,
                    )
                    continue
            pending.append((payload, info_hash))

        groups: dict[
            tuple[str, str | None, bool, float | None, int | None, bool],
            list[tuple[BulkTorrentPayload, str | None]],
        ] = {}
        for payload, info_hash in pending:
            key = (
                payload.category.value,
                payload.download_path,
                payload.is_paused,
                payload.ratio_limit,
                payload.seeding_time_limit,
                bool(payload.magnet_uri),
            )
            groups.setdefault(key, []).append((payload, info_hash))

        for (
            category,
            download_path,
            is_paused,
            ratio_limit,
            seeding_time_limit,
            is_magnet_group,
        ), items in groups.items():
            try:
                await self.ensure_category_exists(category)
                for chunk_start in range(0, len(items), _BULK_ADD_CHUNK_SIZE):
                    chunk = items[chunk_start : chunk_start + _BULK_ADD_CHUNK_SIZE]
                    source: dict[str, Any]
                    if is_magnet_group:
                        source = {"urls": "\n".join(p.magnet_uri or "" for p, _ in chunk)}
                    else:
                        torrent_files = []
                        for p, _ in chunk:
                            if not p.torrent_path:
                                raise ValueError("Missing torrent_path for file bulk add")
                            with open(p.torrent_path, "rb") as file_handle:
                                torrent_files.append(file_handle.read())
                        source = {"torrent_files": torrent_files}
                    chunk_result = await asyncio.to_thread(
                        self.client.torrents_add,
                        **source,
                        category=category,
                        is_paused=is_paused,
                        download_path=download_path,
                        ratio_limit=ratio_limit,
                        seeding_time_limit=seeding_time_limit,
                    )
                    if _qbit_add_response_accepted(chunk_result):
                        for payload, info_hash in chunk:
                            results[payload.key] = BulkAddResult(
                                key=payload.key,
                                success=True,
                                torrent_hash=info_hash,
                            )
                    else:
                        error = str(chunk_result)
                        if _qbit_add_response_failed(chunk_result):
                            error = f"qBittorrent reported add failures: {chunk_result}"
                        for payload, _ in chunk:
                            results[payload.key] = BulkAddResult(
                                key=payload.key,
                                success=False,
                                error=error,
                            )
            except Exception as exc:
                logger.error("Error bulk adding torrents: %s", exc)
                for payload, _ in items:
                    results.setdefault(
                        payload.key,
                        BulkAddResult(
                            key=payload.key,
                            success=False,
                            error=str(exc),
                        ),
                    )

        return [
            results.get(
                payload.key,
                BulkAddResult(payload.key, False, error="Torrent was not submitted"),
            )
            for payload in payloads
        ]

    async def get_torrent_info(self, torrent_hash: str) -> dict | None:
        """Get information about a torrent.

        Args:
            torrent_hash: The torrent hash.

        Returns:
            A dict containing torrent information if found, None otherwise.
        """
        try:
            return await self.get_torrent_info_or_raise(torrent_hash)
        except Exception:
            return None

    async def get_torrent_info_or_raise(self, torrent_hash: str) -> dict | None:
        """Get torrent information, raising on qBittorrent/API failures."""
        torrents = await asyncio.to_thread(
            self.client.torrents_info,
            torrent_hashes=torrent_hash,
        )
        if torrents:
            torrent = torrents[0]
            return self._serialize_torrent(torrent)
        return None

    async def get_all_active_torrents(self) -> list[dict]:
        """Get all active torrents from qBittorrent.

        Returns:
            A list of dicts with keys: hash, name, progress, state, category.
        """
        try:
            return await self.get_all_active_torrents_or_raise()
        except Exception:
            return []

    async def get_all_active_torrents_or_raise(self) -> list[dict]:
        """Get all active torrents, raising on qBittorrent/API failures."""
        torrents = await asyncio.to_thread(self.client.torrents_info)
        return [self._serialize_torrent(t) for t in torrents]

    async def get_unfinished_torrents_or_raise(self) -> list[dict]:
        """Return every qBittorrent torrent that has not completed.

        qBittorrent's unfiltered torrent endpoint is used deliberately: category
        and Siftarr database linkage must not hide an in-progress torrent.
        """
        torrents = await self.get_all_active_torrents_or_raise()
        # Malformed or unknown progress counts as 0 so the entry stays visible
        # rather than accidentally concealing an active qBit download.
        return [t for t in torrents if not (normalize_float(t.get("progress")) >= 1.0)]

    async def get_completed_torrents(self) -> list[dict]:
        """Get completed torrents from qBittorrent, returning [] on failure."""
        try:
            return await self.get_completed_torrents_or_raise()
        except Exception:
            return []

    async def get_completed_torrents_or_raise(self) -> list[dict]:
        """Return completed torrents, propagating qBittorrent failures.

        Callers that must distinguish "qBittorrent is unreachable" from
        "there are no completed torrents" need this variant; the swallowing
        wrapper above reports both as an empty list.
        """
        torrents = await asyncio.to_thread(
            self.client.torrents_info,
            status_filter="completed",
        )
        return [self._serialize_torrent(t) for t in torrents]

    async def set_torrent_location(
        self,
        torrent_hashes: str | list[str],
        location: str,
        *,
        move: bool = True,
    ) -> bool:
        """Set torrent content location, moving files by default."""
        try:
            await asyncio.to_thread(
                self.client.torrents_set_location,
                torrent_hashes=torrent_hashes,
                location=location,
                move=move,
            )
            return True
        except Exception:
            return False

    async def delete_torrents(
        self,
        torrent_hashes: str | list[str],
        *,
        delete_files: bool = False,
    ) -> bool:
        """Delete torrents from qBittorrent without deleting files by default."""
        try:
            await asyncio.to_thread(
                self.client.torrents_delete,
                torrent_hashes=torrent_hashes,
                delete_files=delete_files,
            )
            return True
        except Exception:
            return False

    async def get_torrent_progress_by_name(self, name_fragment: str) -> float | None:
        """Get progress of a torrent matching a name fragment.

        Tries exact substring first, then falls back to normalised matching
        where separators (dots, dashes, spaces) are treated interchangeably.
        """
        torrent = await self.get_torrent_info_by_name(name_fragment)
        return torrent["progress"] if torrent else None

    async def get_torrent_info_by_name(self, name_fragment: str) -> dict | None:
        """Get qBittorrent info for the first torrent matching a name fragment.

        Tries exact substring first, then falls back to normalised matching
        where separators (dots, dashes, spaces) are treated interchangeably.
        """
        torrents = await self.get_all_active_torrents()
        return self._find_torrent_info_by_name(torrents, name_fragment)

    async def get_torrent_info_by_name_or_raise(self, name_fragment: str) -> dict | None:
        """Get torrent information by name, raising on qBittorrent/API failures."""
        torrents = await self.get_all_active_torrents_or_raise()
        return self._find_torrent_info_by_name(torrents, name_fragment)

    def _find_torrent_info_by_name(self, torrents: list[dict], name_fragment: str) -> dict | None:
        name_lower = name_fragment.lower()
        name_norm = normalize_torrent_name(name_fragment)
        for torrent in torrents:
            qname = torrent.get("name") or ""
            qname_lower = qname.lower()
            if name_lower in qname_lower or name_norm in normalize_torrent_name(qname):
                return torrent
        return None

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        """Delete a torrent from qBittorrent.

        Args:
            torrent_hash: The torrent hash to delete.
            delete_files: Whether to delete downloaded files.

        Returns:
            True if deletion successful, False otherwise.
        """
        return await self.delete_torrents(torrent_hash, delete_files=delete_files)
