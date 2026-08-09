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
    STAGED_STATUS_APPROVED,
    StagedTorrent,
)
from app.siftarr.services.releases.release_parser import parse_release_title_identity
from app.siftarr.services.utils.torrent_identity import normalize_torrent_name

UNMANAGED_GROUP_TITLE = "Unmanaged"

_TOTAL_FIELDS = ("dlspeed", "upspeed", "downloaded", "uploaded", "size")

#: Match tiers from most to least confident.  Used to label rows/groups in the
#: UI and to pick the weakest tier when summarizing a group.
MATCH_TIERS = ("hash", "name", "staged", "title")


def _hash_index(torrents: list[StagedTorrent]) -> dict[str, StagedTorrent]:
    return {torrent.info_hash.lower(): torrent for torrent in torrents if torrent.info_hash}


def _name_index(torrents: list[StagedTorrent]) -> dict[str, StagedTorrent | None]:
    """Normalized-title index; ambiguous names map to ``None`` (never matched)."""
    index: dict[str, StagedTorrent | None] = {}
    for torrent in torrents:
        name = normalize_torrent_name(torrent.title)
        if not name:
            continue
        index[name] = torrent if name not in index else None
    return index


def build_request_title_index(
    request_info: dict[int, tuple[str | None, Any]],
) -> dict[str, int | None]:
    """Map normalized request titles to a request id (``None`` when ambiguous)."""
    index: dict[str, int | None] = {}
    for request_id, (title, _media_type) in request_info.items():
        normalized = normalize_torrent_name(title)
        if len(normalized) < 2:
            continue
        index[normalized] = request_id if normalized not in index else None
    return index


def _title_matched_request_id(name: str, title_index: dict[str, int | None]) -> int | None:
    """Resolve a qBit torrent name to a request via its parsed release title.

    Deliberately conservative: only an exact normalized-title equality counts,
    and normalized titles owned by more than one request are ignored.
    """
    if not title_index:
        return None
    parsed = parse_release_title_identity(name)
    normalized = normalize_torrent_name(parsed)
    if len(normalized) < 2:
        return None
    return title_index.get(normalized)


def match_qbit_torrents(
    qbit_torrents: list[dict],
    managed_torrents: list[StagedTorrent],
    fallback_torrents: list[StagedTorrent] | None = None,
    title_index: dict[str, int | None] | None = None,
) -> list[dict]:
    """Best-effort match of live qBit torrents to Siftarr requests.

    Tiers, first hit wins:

    1. ``hash``  - info hash equals an approved staged torrent's hash
    2. ``name``  - normalized name equals a unique approved staged torrent title
    3. ``staged`` - same, against staged rows in any other status
    4. ``title`` - parsed release title equals a unique request title (no
       managed row, so the UI must not offer managed actions)
    """
    approved_by_hash = _hash_index(managed_torrents)
    approved_by_name = _name_index(managed_torrents)
    fallback = fallback_torrents or []
    fallback_by_hash = _hash_index(fallback)
    fallback_by_name = _name_index(fallback)
    titles = title_index or {}

    matched: list[dict] = []
    for qbit_torrent in qbit_torrents:
        torrent_hash = str(qbit_torrent.get("hash") or "").lower()
        name = str(qbit_torrent.get("name") or "")
        normalized_name = normalize_torrent_name(name)

        managed: StagedTorrent | None = None
        match_type: str | None = None
        request_id: int | None = None

        if (candidate := approved_by_hash.get(torrent_hash)) is not None:
            managed, match_type = candidate, "hash"
        elif (candidate := approved_by_name.get(normalized_name)) is not None:
            managed, match_type = candidate, "name"
        elif (
            candidate := fallback_by_hash.get(torrent_hash) or fallback_by_name.get(normalized_name)
        ) is not None:
            managed, match_type = candidate, "staged"
        elif (request_id := _title_matched_request_id(name, titles)) is not None:
            match_type = "title"

        matched.append(
            {
                **qbit_torrent,
                "managed_torrent": managed,
                "match_type": match_type,
                "matched_request_id": (
                    getattr(managed, "request_id", None) if managed is not None else request_id
                ),
            }
        )
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
            "added_on",
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
    row["match"] = torrent.get("match_type")
    return row


def _numeric(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return value


def _empty_totals() -> dict[str, int | float]:
    return dict.fromkeys(_TOTAL_FIELDS, 0)


def _tier_rank(tier: str) -> int:
    """Rank a tier by confidence: lower is stronger.

    Unrecognized tiers sort as weakest rather than strongest, so adding a tier
    to the matcher without updating :data:`MATCH_TIERS` degrades a group's
    reported confidence instead of silently overstating it.
    """
    return MATCH_TIERS.index(tier) if tier in MATCH_TIERS else len(MATCH_TIERS)


def _group_match_tier(rows: list[dict]) -> str | None:
    """Weakest tier present in a group, so the UI can label its confidence."""
    tiers: list[str] = [tier for row in rows if (tier := row.get("match"))]
    if not tiers:
        return None
    return max(tiers, key=_tier_rank)


def group_matched_torrents(
    matched: list[dict],
    request_info: dict[int, tuple[str | None, Any]],
) -> list[dict]:
    """Group matched qBit torrents by owning request id.

    Torrents with no managed match (or a managed row without a request) collapse
    into a single ``Unmanaged`` group.  Rows and groups default to qBittorrent's
    newest-added-first order, including the unmanaged group.
    """
    grouped_rows: dict[int, list[dict]] = {}
    unmanaged_rows: list[dict] = []

    for torrent in matched:
        managed = torrent.get("managed_torrent")
        request_id = (
            getattr(managed, "request_id", None)
            if managed is not None
            else torrent.get("matched_request_id")
        )
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
            "match": _group_match_tier(rows),
        }

    def _added_on_sort_value(row: dict) -> float:
        value = row.get("added_on")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return float("inf")
        return -value

    def _sort_rows(rows: list[dict]) -> None:
        rows.sort(
            key=lambda row: (
                _added_on_sort_value(row),
                str(row.get("name") or row.get("hash") or "").casefold(),
            )
        )

    for rows in grouped_rows.values():
        _sort_rows(rows)
    _sort_rows(unmanaged_rows)

    ordered = [
        _build(
            request_id,
            request_info.get(request_id, (None, None))[0] or f"Request {request_id}",
            request_info.get(request_id, (None, None))[1],
            rows,
        )
        for request_id, rows in grouped_rows.items()
    ]
    # A group's first torrent is its best sort key, which keeps grouping from
    # defeating the global newest-first ordering.
    ordered.sort(
        key=lambda group: (
            _added_on_sort_value(group["torrents"][0]),
            str(group["title"]).casefold(),
        )
    )
    if unmanaged_rows:
        ordered.append(_build(None, UNMANAGED_GROUP_TITLE, None, unmanaged_rows))
        ordered.sort(
            key=lambda group: (
                _added_on_sort_value(group["torrents"][0]),
                str(group["title"]).casefold(),
            )
        )

    return ordered


async def load_match_candidates(
    db: AsyncSession, *, exclude_terminal_requests: bool
) -> tuple[list[StagedTorrent], list[StagedTorrent]]:
    """Return (approved staged torrents, lower-confidence staged torrents)."""
    query = select(StagedTorrent).where(StagedTorrent.request_id.is_not(None))
    result = await db.execute(query)
    candidates = list(result.scalars().all())
    request_ids = {torrent.request_id for torrent in candidates if torrent.request_id is not None}
    request_statuses: dict[int, RequestStatus] = {}
    if request_ids:
        request_result = await db.execute(
            select(RequestModel.id, RequestModel.status).where(RequestModel.id.in_(request_ids))
        )
        for request_id, status in request_result.all():
            request_statuses[request_id] = status

    def _eligible(torrent: StagedTorrent) -> bool:
        return torrent.request_id is not None and (
            not exclude_terminal_requests
            or not is_terminal_request_status(request_statuses.get(torrent.request_id))
        )

    approved = [
        torrent
        for torrent in candidates
        if torrent.status == STAGED_STATUS_APPROVED and _eligible(torrent)
    ]
    fallback = [
        torrent
        for torrent in candidates
        if torrent.status != STAGED_STATUS_APPROVED and _eligible(torrent)
    ]
    return approved, fallback


async def load_all_request_info(db: AsyncSession) -> dict[int, tuple[str | None, Any]]:
    """Fetch display title and media type for every request in one query."""
    result = await db.execute(select(RequestModel.id, RequestModel.title, RequestModel.media_type))
    return {row[0]: (row[1], row[2]) for row in result.all()}


async def build_grouped_torrent_payload(
    db: AsyncSession,
    qbit_torrents: list[dict],
    *,
    exclude_terminal_requests: bool,
) -> dict:
    """Match, group and serialize a live qBit torrent list for the API."""
    managed, fallback = await load_match_candidates(
        db, exclude_terminal_requests=exclude_terminal_requests
    )
    request_info = await load_all_request_info(db)
    matched = match_qbit_torrents(
        qbit_torrents,
        managed,
        fallback_torrents=fallback,
        title_index=build_request_title_index(request_info),
    )
    return {
        "groups": group_matched_torrents(matched, request_info),
        "qbit_unavailable": False,
    }
