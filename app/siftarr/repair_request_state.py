"""One-off repair command for request and staged-torrent state."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.siftarr.config import Settings, get_settings
from app.siftarr.models.request import (
    ACTIVE_STAGING_WORKFLOW_STATUSES,
    MediaType,
    Request,
    RequestStatus,
    is_active_staging_workflow_status,
)
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.episode_sync_service import (
    _derive_request_status_from_seasons,
    _derive_season_status,
)
from app.siftarr.services.overseerr_service import OverseerrService
from app.siftarr.services.unreleased_service import classify_request_release_verdict

_ACTIVE_TORRENT_STATUSES = ("staged", "approved")
_RECLASSIFIABLE_STATUSES = {
    RequestStatus.COMPLETED,
    RequestStatus.AVAILABLE,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.UNRELEASED,
}
_UNRELEASED_REDIRECTABLE_STATUSES = {
    RequestStatus.RECEIVED,
    RequestStatus.SEARCHING,
    RequestStatus.PENDING,
    RequestStatus.UNRELEASED,
    RequestStatus.AVAILABLE,
    RequestStatus.PARTIALLY_AVAILABLE,
    RequestStatus.COMPLETED,
}


@dataclass(slots=True)
class RepairSummary:
    """Aggregated repair outcome."""

    apply: bool
    inspected_requests: int = 0
    inspected_active_torrents: int = 0
    request_status_updates: int = 0
    season_status_updates: int = 0
    aggregate_request_repairs: int = 0
    stale_workflow_request_repairs: int = 0
    unreleased_request_repairs: int = 0
    staged_torrents_retired: int = 0
    duplicate_torrents_retired: int = 0
    stale_active_torrents_retired: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return "apply" if self.apply else "dry-run"


def _timestamp_now() -> datetime:
    return datetime.now(UTC)


def _created_sort_value(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _pick_active_torrent_to_keep(torrents: list[StagedTorrent]) -> StagedTorrent:
    """Pick the canonical active torrent for a request repair pass."""

    def priority(torrent: StagedTorrent) -> tuple[int, int, float, int]:
        return (
            1 if torrent.status == "approved" else 0,
            1 if torrent.selection_source == "manual" else 0,
            _created_sort_value(getattr(torrent, "created_at", None)),
            torrent.id,
        )

    return max(torrents, key=priority)


def _format_request_change(
    request: Request, old_status: RequestStatus, new_status: RequestStatus
) -> str:
    return f"request {request.id} ({request.title}): {old_status.value} -> {new_status.value}"


def _repair_reason_for_request_change(
    *,
    aggregate_changed: bool,
    stale_workflow_changed: bool,
    unreleased_changed: bool,
) -> str:
    """Describe the primary repair reason for a request status change."""
    if unreleased_changed:
        return "reclassified to unreleased"
    if stale_workflow_changed:
        return "repaired stale staged/downloading workflow"
    if aggregate_changed:
        return "repaired aggregate season/request availability"
    return "repaired request state"


class RequestStateRepairService:
    """Repairs known bad request/staged-torrent state in-place."""

    def __init__(self, db: AsyncSession, overseerr: OverseerrService | Any) -> None:
        self.db = db
        self.overseerr = overseerr

    async def run(self, *, apply: bool) -> RepairSummary:
        summary = RepairSummary(apply=apply)
        requests_result = await self.db.execute(
            select(Request)
            .options(selectinload(Request.seasons).selectinload(Season.episodes))
            .order_by(Request.id.asc())
        )
        requests = list(requests_result.scalars().all())
        summary.inspected_requests = len(requests)

        staged_result = await self.db.execute(
            select(StagedTorrent)
            .where(
                StagedTorrent.request_id.is_not(None),
                StagedTorrent.status.in_(_ACTIVE_TORRENT_STATUSES),
            )
            .order_by(StagedTorrent.created_at.asc(), StagedTorrent.id.asc())
        )
        active_torrents = list(staged_result.scalars().all())
        summary.inspected_active_torrents = len(active_torrents)

        active_torrents_by_request_id: dict[int, list[StagedTorrent]] = {}
        for torrent in active_torrents:
            if torrent.request_id is None:
                continue
            active_torrents_by_request_id.setdefault(torrent.request_id, []).append(torrent)

        now = _timestamp_now()
        for request in requests:
            request_active_torrents = active_torrents_by_request_id.get(request.id, [])
            final_status = await self._repair_request_status(
                request,
                request_active_torrents,
                summary,
                now=now,
            )
            await self._repair_active_torrents(
                request,
                request_active_torrents,
                final_status,
                summary,
                now=now,
            )

        await self.db.flush()
        if apply:
            await self.db.commit()
        else:
            await self.db.rollback()
        return summary

    async def _repair_request_status(
        self,
        request: Request,
        active_torrents: list[StagedTorrent],
        summary: RepairSummary,
        *,
        now: datetime,
    ) -> RequestStatus:
        original_status = request.status
        candidate_status = request.status

        aggregate_changed = False
        stale_workflow_changed = False
        unreleased_changed = False

        if request.media_type == MediaType.TV:
            seasons = list(request.seasons)
            for season in seasons:
                derived_season_status = _derive_season_status(list(season.episodes))
                if season.status != derived_season_status:
                    season.status = derived_season_status
                    summary.season_status_updates += 1

            derived_request_status = (
                _derive_request_status_from_seasons(seasons) if seasons else None
            )

            if derived_request_status is not None:
                if (
                    request.status in _RECLASSIFIABLE_STATUSES
                    and derived_request_status != candidate_status
                ):
                    candidate_status = derived_request_status
                    aggregate_changed = True
                elif request.status in ACTIVE_STAGING_WORKFLOW_STATUSES and (
                    derived_request_status
                    in {
                        RequestStatus.AVAILABLE,
                        RequestStatus.PARTIALLY_AVAILABLE,
                        RequestStatus.UNRELEASED,
                    }
                    or not active_torrents
                ):
                    candidate_status = derived_request_status
                    stale_workflow_changed = True

            media_details = None
            if request.tmdb_id is not None:
                media_details = await self.overseerr.get_media_details("tv", request.tmdb_id)

            verdict = classify_request_release_verdict(
                request,
                media_details=media_details,
                local_episodes=[episode for season in seasons for episode in season.episodes],
                has_empty_seasons=any(not season.episodes for season in seasons),
            )
            if (
                verdict == "unreleased"
                and candidate_status in _UNRELEASED_REDIRECTABLE_STATUSES
                and candidate_status != RequestStatus.UNRELEASED
            ):
                candidate_status = RequestStatus.UNRELEASED
                unreleased_changed = True

        elif request.status in ACTIVE_STAGING_WORKFLOW_STATUSES and not active_torrents:
            candidate_status = RequestStatus.PENDING
            stale_workflow_changed = True

        if candidate_status != original_status:
            request.status = candidate_status
            request.updated_at = now
            summary.request_status_updates += 1
            summary.notes.append(
                f"{_format_request_change(request, original_status, candidate_status)} "
                f"[{_repair_reason_for_request_change(aggregate_changed=aggregate_changed, stale_workflow_changed=stale_workflow_changed, unreleased_changed=unreleased_changed)}]"
            )

        if aggregate_changed:
            summary.aggregate_request_repairs += 1
        if stale_workflow_changed:
            summary.stale_workflow_request_repairs += 1
        if unreleased_changed:
            summary.unreleased_request_repairs += 1

        return candidate_status

    async def _repair_active_torrents(
        self,
        request: Request,
        active_torrents: list[StagedTorrent],
        final_status: RequestStatus,
        summary: RepairSummary,
        *,
        now: datetime,
    ) -> None:
        if not active_torrents:
            return

        if not is_active_staging_workflow_status(final_status):
            for torrent in active_torrents:
                torrent.status = "discarded"
                summary.staged_torrents_retired += 1
                summary.stale_active_torrents_retired += 1
                summary.notes.append(
                    f"staged torrent {torrent.id} for request {request.id}: repaired stale active torrent after request became {final_status.value}"
                )
            return

        if len(active_torrents) <= 1:
            return

        keeper = _pick_active_torrent_to_keep(active_torrents)
        for torrent in active_torrents:
            if torrent.id == keeper.id:
                continue
            torrent.status = "replaced"
            torrent.replaced_by_id = keeper.id
            torrent.replaced_at = now
            torrent.replacement_reason = (
                "Retired duplicate active staged torrent during request-state repair"
            )
            summary.staged_torrents_retired += 1
            summary.duplicate_torrents_retired += 1
            summary.notes.append(
                f"staged torrent {torrent.id} for request {request.id}: repaired duplicate active selection, replaced by {keeper.id}"
            )


async def repair_request_state(
    *,
    apply: bool,
    database_url: str | None = None,
    settings: Settings | None = None,
    overseerr_factory: Callable[[Settings], OverseerrService | Any] | None = None,
) -> RepairSummary:
    """Run the one-off repair against the configured database."""
    active_settings = settings or get_settings()
    engine = create_async_engine(
        database_url or active_settings.database_url, echo=False, future=True
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    overseerr_cls = overseerr_factory or OverseerrService
    overseerr = overseerr_cls(active_settings)

    try:
        async with session_maker() as session:
            summary = await RequestStateRepairService(session, overseerr).run(apply=apply)
    finally:
        await overseerr.close()
        await engine.dispose()

    return summary


def format_summary(summary: RepairSummary) -> str:
    """Render a human-readable summary for CLI usage."""
    lines = [
        f"Request-state repair ({summary.mode})",
        f"- inspected requests: {summary.inspected_requests}",
        f"- inspected active staged torrents: {summary.inspected_active_torrents}",
        f"- request status updates: {summary.request_status_updates}",
        f"- season status updates: {summary.season_status_updates}",
        f"- aggregate TV request repairs: {summary.aggregate_request_repairs}",
        f"- stale staged/downloading request repairs: {summary.stale_workflow_request_repairs}",
        f"- ongoing TV reclassified to unreleased: {summary.unreleased_request_repairs}",
        f"- staged torrents retired: {summary.staged_torrents_retired}",
        f"- duplicate active torrents retired: {summary.duplicate_torrents_retired}",
        f"- stale active torrents retired: {summary.stale_active_torrents_retired}",
    ]
    if summary.notes:
        lines.append("- changes:")
        lines.extend(f"  - {note}" for note in summary.notes)
    else:
        lines.append("- changes: none")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Inspect without mutating the database"
    )
    mode.add_argument("--apply", action="store_true", help="Apply the repair in-place")
    return parser


async def _amain() -> int:
    args = _build_parser().parse_args()
    apply = bool(args.apply)
    summary = await repair_request_state(apply=apply)
    print(format_summary(summary))
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
