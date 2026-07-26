"""Shared helpers for matching qBittorrent torrents to Siftarr requests.

The dashboard "Torrent Status" tab renders live qBittorrent state grouped by the
Siftarr request that owns each torrent.  Nothing here is persisted: every call
reflects only what qBittorrent currently reports.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.request import Request as RequestModel
from app.siftarr.models.request import RequestStatus, is_terminal_request_status
from app.siftarr.models.staged_torrent import (
    ACTIVE_STAGED_STATUSES,
    STAGED_STATUS_APPROVED,
    StagedTorrent,
)
from app.siftarr.services.utils.torrent_identity import normalize_torrent_name

UNMANAGED_GROUP_TITLE = "Unmanaged"

_TOTAL_FIELDS = ("dlspeed", "upspeed", "downloaded", "uploaded", "size")


def match_qbit_torrents(
    qbit_torrents: list[dict], managed_torrents: list[StagedTorrent]
) -> list[dict]:
    """Attach a managed torrent only when its hash or unique name matches qBit."""
    by_hash = {
        torrent.info_hash.lower(): torrent for torrent in managed_torrents if torrent.info_hash
    }
    by_name: dict[str, StagedTorrent | None] = {}
    for torrent in managed_torrents:
        name = normalize_torrent_name(torrent.title)
        if not name:
            continue
        by_name[name] = torrent if name not in by_name else None

    matched: list[dict] = []
    for qbit_torrent in qbit_torrents:
        torrent_hash = str(qbit_torrent.get("hash") or "").lower()
        managed = by_hash.get(torrent_hash)
        if managed is None:
            managed = by_name.get(normalize_torrent_name(str(qbit_torrent.get("name") or "")))
        matched.append({**qbit_torrent, "managed_torrent": managed})
    return matched


def serialize_qbit_download(torrent: dict) -> dict:
    """Return qBit metadata plus the minimum, server-authorized action data."""
    managed = torrent.get("managed_torrent")
    row = {
        key: torrent.get(key)
        for key in (
            "hash",
            "name",
            "size",
            "progress",
            "state",
            "category",
            "eta",
            "ratio",
            "dlspeed",
            "upspeed",
            "downloaded",
            "uploaded",
        )
    }
    if managed is not None:
        row["managed"] = {
            "id": managed.id,
            "request_id": managed.request_id,
            "move_status": managed.move_status or "pending",
            "moved_path": managed.moved_path,
        }
    else:
        row["managed"] = None
    return row


def _numeric(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return value


def _empty_totals() -> dict[str, int | float]:
    return dict.fromkeys(_TOTAL_FIELDS, 0)


def group_matched_torrents(
    matched: list[dict],
    request_info: dict[int, tuple[str | None, Any]],
) -> list[dict]:
    """Group matched qBit torrents by owning request id.

    Torrents with no managed match (or a managed row without a request) collapse
    into a single trailing ``Unmanaged`` group.
    """
    grouped_rows: dict[int, list[dict]] = {}
    unmanaged_rows: list[dict] = []

    for torrent in matched:
        managed = torrent.get("managed_torrent")
        request_id = getattr(managed, "request_id", None) if managed is not None else None
        row = serialize_qbit_download(torrent)
        if request_id is None:
            unmanaged_rows.append(row)
            continue
        grouped_rows.setdefault(request_id, []).append(row)

    def _build(request_id: int | None, title: str, media_type: Any, rows: list[dict]) -> dict:
        totals = _empty_totals()
        for row in rows:
            for field in _TOTAL_FIELDS:
                totals[field] += _numeric(row.get(field))
        return {
            "request_id": request_id,
            "title": title,
            "media_type": getattr(media_type, "value", media_type),
            "unmanaged": request_id is None,
            "count": len(rows),
            "torrents": rows,
            "totals": totals,
        }

    ordered = [
        _build(
            request_id,
            request_info.get(request_id, (None, None))[0] or f"Request {request_id}",
            request_info.get(request_id, (None, None))[1],
            rows,
        )
        for request_id, rows in grouped_rows.items()
    ]
    ordered.sort(key=lambda group: str(group["title"]).casefold())
    if unmanaged_rows:
        ordered.append(_build(None, UNMANAGED_GROUP_TITLE, None, unmanaged_rows))

    return ordered


async def load_managed_torrents(
    db: AsyncSession, *, exclude_terminal_requests: bool
) -> tuple[list[StagedTorrent], dict[int, RequestStatus]]:
    """Return approved staged torrents eligible for qBit matching."""
    result = await db.execute(
        select(StagedTorrent).where(StagedTorrent.status.in_(ACTIVE_STAGED_STATUSES))
    )
    candidates = list(result.scalars().all())
    request_ids = {torrent.request_id for torrent in candidates if torrent.request_id is not None}
    request_statuses: dict[int, RequestStatus] = {}
    if request_ids:
        request_result = await db.execute(
            select(RequestModel.id, RequestModel.status).where(RequestModel.id.in_(request_ids))
        )
        for request_id, status in request_result.all():
            request_statuses[request_id] = status
    managed = [
        torrent
        for torrent in candidates
        if torrent.status == STAGED_STATUS_APPROVED
        and torrent.request_id is not None
        and (
            not exclude_terminal_requests
            or not is_terminal_request_status(request_statuses.get(torrent.request_id))
        )
    ]
    return managed, request_statuses


async def load_request_info(
    db: AsyncSession, request_ids: set[int]
) -> dict[int, tuple[str | None, Any]]:
    """Fetch display title and media type for the given requests in one query."""
    if not request_ids:
        return {}
    result = await db.execute(
        select(RequestModel.id, RequestModel.title, RequestModel.media_type).where(
            RequestModel.id.in_(request_ids)
        )
    )
    return {row[0]: (row[1], row[2]) for row in result.all()}


async def build_grouped_torrent_payload(
    db: AsyncSession,
    qbit_torrents: list[dict],
    *,
    exclude_terminal_requests: bool,
) -> dict:
    """Match, group and serialize a live qBit torrent list for the API."""
    managed, _ = await load_managed_torrents(
        db, exclude_terminal_requests=exclude_terminal_requests
    )
    matched = match_qbit_torrents(qbit_torrents, managed)
    request_ids = {
        torrent["managed_torrent"].request_id
        for torrent in matched
        if torrent.get("managed_torrent") is not None
        and torrent["managed_torrent"].request_id is not None
    }
    request_info = await load_request_info(db, request_ids)
    return {
        "groups": group_matched_torrents(matched, request_info),
        "qbit_unavailable": False,
    }
