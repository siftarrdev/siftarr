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


def _parse_magnet_info_hash(magnet_uri: str) -> str | None:
    """Extract the info hash (hex) from a magnet URI."""
    return parse_magnet_info_hash(magnet_uri)


def _torrent_file_info_hash(torrent_path: str) -> str | None:
    """Compute the SHA1 info hash of a .torrent file.

    The info hash is SHA1 of the raw bencoded ``info`` dictionary inside the
    top-level torrent metadata.
    """
    return torrent_file_info_hash(torrent_path)


def _normalize_name(name: str) -> str:
    """Normalize separators for loose name matching (dots, dashes, spaces → space)."""
    return normalize_torrent_name(name)


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


def _qbit_add_response_accepted(response: Any) -> bool:
    """Return True when qBit/qbittorrentapi accepted an add request."""
    if response == "Ok.":
        return True
    failure_count = _qbit_response_value(response, "failure_count")
    if failure_count is None:
        return False
    try:
        if int(failure_count) > 0:
            return False
    except TypeError, ValueError:
        return False
    return any(
        _qbit_response_value(response, name) is not None
        for name in ("pending_count", "success_count", "added_torrent_ids")
    )


def _qbit_add_response_failed(response: Any) -> bool:
    failure_count = _qbit_response_value(response, "failure_count")
    if failure_count is None:
        return False
    try:
        return int(failure_count) > 0
    except TypeError, ValueError:
        return False


def _bencode_extract_info_value(data: bytes) -> bytes | None:
    """Given a bencoded torrent file, return the raw bytes of the ``info`` key's value."""
    if not data or data[0:1] != b"d":
        return None
    cur: int = 1  # skip leading 'd'
    while cur < len(data):
        if data[cur : cur + 1] == b"e":
            return None  # reached end without finding 'info'
        # Parse key
        key: bytes | None
        nxt: int | None
        key, nxt = _bencode_read_string(data, cur)
        if key is None or nxt is None:
            return None
        cur = nxt
        val_start: int = cur
        # Skip over the value (any type)
        nxt = _bencode_skip_value(data, cur)
        if nxt is None:
            return None
        cur = nxt
        if key == b"info":
            return data[val_start:cur]
    return None


def _bencode_read_string(data: bytes, pos: int) -> tuple[bytes | None, int | None]:
    """Read a bencoded byte-string starting at *pos*. Returns ``(value, next_pos)``."""
    colon = data.find(b":", pos)
    if colon == -1:
        return None, None
    try:
        length = int(data[pos:colon])
    except ValueError:
        return None, None
    start = colon + 1
    end = start + length
    if end > len(data):
        return None, None
    return data[start:end], end


def _bencode_skip_value(data: bytes, pos: int) -> int | None:
    """Skip over one bencoded value at *pos* and return the position after it."""
    if pos >= len(data):
        return None
    ch = data[pos : pos + 1]
    if ch == b"d":
        cur: int = pos + 1
        while cur < len(data) and data[cur : cur + 1] != b"e":
            # skip key
            _key, nxt = _bencode_read_string(data, cur)
            if nxt is None:
                return None
            cur = nxt
            # skip value
            nxt = _bencode_skip_value(data, cur)
            if nxt is None:
                return None
            cur = nxt
        return cur + 1  # skip 'e'
    elif ch == b"l":
        cur = pos + 1
        while cur < len(data) and data[cur : cur + 1] != b"e":
            nxt = _bencode_skip_value(data, cur)
            if nxt is None:
                return None
            cur = nxt
        return cur + 1  # skip 'e'
    elif ch == b"i":
        end = data.find(b"e", pos)
        return end + 1 if end != -1 else None
    elif ch in b"0123456789":
        _, nxt = _bencode_read_string(data, pos)
        return nxt
    return None


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
        return {
            "hash": getattr(torrent, "hash", None),
            "name": getattr(torrent, "name", None),
            "size": getattr(torrent, "size", None),
            "progress": getattr(torrent, "progress", None),
            "state": getattr(torrent, "state", None),
            "category": getattr(torrent, "category", None),
            "ratio": getattr(torrent, "ratio", None),
            "added_on": getattr(torrent, "added_on", None),
            "completed_on": getattr(torrent, "completed_on", None),
            "save_path": getattr(torrent, "save_path", None),
            "download_location": getattr(torrent, "download_location", None),
            "seeding_time": getattr(torrent, "seeding_time", None),
            "eta": getattr(torrent, "eta", None),
            "dlspeed": getattr(torrent, "dlspeed", None),
        }

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
            info_hash = _parse_magnet_info_hash(magnet_uri)
        elif torrent_path:
            info_hash = _torrent_file_info_hash(torrent_path)

        # ── 2. Check if already in qBittorrent (idempotent) ──
        if info_hash:
            existing = await self.get_torrent_info(info_hash)
            if existing:
                logger.info("Torrent already in qBittorrent (hash=%s), skipping add", info_hash)
                return info_hash

        # ── 3. Add to qBittorrent ──
        try:
            await self.ensure_category_exists(category.value)

            if magnet_uri:
                result = await asyncio.to_thread(
                    self.client.torrents_add,
                    urls=magnet_uri,
                    category=category.value,
                    is_paused=is_paused,
                    download_path=download_path,
                    ratio_limit=ratio_limit,
                    seeding_time_limit=seeding_time_limit,
                )
            elif torrent_path:
                with open(torrent_path, "rb") as f:
                    torrent_data = f.read()
                result = await asyncio.to_thread(
                    self.client.torrents_add,
                    torrent_files=[torrent_data],
                    category=category.value,
                    is_paused=is_paused,
                    download_path=download_path,
                    ratio_limit=ratio_limit,
                    seeding_time_limit=seeding_time_limit,
                )
            else:
                raise ValueError("Either torrent_path or magnet_uri must be provided")

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
                info_hash = _parse_magnet_info_hash(payload.magnet_uri)
            elif payload.torrent_path:
                info_hash = _torrent_file_info_hash(payload.torrent_path)

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
                result = "Ok."
                for chunk_start in range(0, len(items), _BULK_ADD_CHUNK_SIZE):
                    chunk = items[chunk_start : chunk_start + _BULK_ADD_CHUNK_SIZE]
                    if is_magnet_group:
                        urls = "\n".join(p.magnet_uri or "" for p, _ in chunk)
                        chunk_result = await asyncio.to_thread(
                            self.client.torrents_add,
                            urls=urls,
                            category=category,
                            is_paused=is_paused,
                            download_path=download_path,
                            ratio_limit=ratio_limit,
                            seeding_time_limit=seeding_time_limit,
                        )
                    else:
                        torrent_files = []
                        for p, _ in chunk:
                            if not p.torrent_path:
                                raise ValueError("Missing torrent_path for file bulk add")
                            with open(p.torrent_path, "rb") as file_handle:
                                torrent_files.append(file_handle.read())
                        chunk_result = await asyncio.to_thread(
                            self.client.torrents_add,
                            torrent_files=torrent_files,
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
                        result = chunk_result
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
                continue

            if result != "Ok.":
                continue

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

    async def get_torrents_by_category(self, category: str) -> list[dict]:
        """Get all torrents in a category.

        Args:
            category: The category name to filter by.

        Returns:
            A list of dicts containing torrent information.
        """
        try:
            torrents = await asyncio.to_thread(
                self.client.torrents_info,
                category=category,
            )
            return [self._serialize_torrent(t) for t in torrents]
        except Exception:
            return []

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
        unfinished: list[dict] = []
        for torrent in torrents:
            progress = torrent.get("progress")
            if isinstance(progress, int | float | str):
                try:
                    is_complete = float(progress) >= 1.0
                except ValueError:
                    # Keep entries with malformed progress visible rather than
                    # accidentally concealing an active qBit download.
                    is_complete = False
            else:
                # Keep entries with unknown progress visible rather than
                # accidentally concealing an active qBit download.
                is_complete = False
            if not is_complete:
                unfinished.append(torrent)
        return unfinished

    async def get_completed_torrents(self) -> list[dict]:
        """Get completed torrents from qBittorrent."""
        try:
            torrents = await asyncio.to_thread(
                self.client.torrents_info,
                status_filter="completed",
            )
            return [self._serialize_torrent(t) for t in torrents]
        except Exception:
            return []

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
        name_lower = name_fragment.lower()
        name_norm = _normalize_name(name_fragment)
        torrents = await self.get_all_active_torrents()
        for torrent in torrents:
            qname = torrent.get("name") or ""
            qname_lower = qname.lower()
            if name_lower in qname_lower or name_norm in _normalize_name(qname):
                return torrent["progress"]
        return None

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
        name_norm = _normalize_name(name_fragment)
        for torrent in torrents:
            qname = torrent.get("name") or ""
            qname_lower = qname.lower()
            if name_lower in qname_lower or name_norm in _normalize_name(qname):
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
