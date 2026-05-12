"""Write-only instrumentation helpers for stats metrics."""

import inspect
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models import (
    Request,
    StatsReleaseFact,
    StatsRuleOutcome,
    StatsTimingEvent,
)
from app.siftarr.models.release import Release
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.releases.release_storage import get_release_persistence_key

logger = logging.getLogger(__name__)


def resolution_bucket(resolution: str | None) -> str | None:
    """Map selected-release resolution into stats buckets."""
    if resolution is None:
        return None
    normalized = resolution.strip().lower()
    if normalized in {"2160p", "4k", "uhd"}:
        return "4K"
    if normalized == "1080p":
        return "1080p"
    return "other"


async def record_timing_event(
    db: AsyncSession,
    *,
    event_name: str,
    request_id: int | None,
    activity_log_id: int | None = None,
    duration_ms: float | None = None,
    details: dict | None = None,
) -> None:
    """Persist a compact timing event without failing the caller."""
    try:
        entry = StatsTimingEvent(
            request_id=request_id,
            activity_log_id=activity_log_id,
            event_name=event_name,
            correlation_id=str(request_id) if request_id is not None else None,
            duration_ms=duration_ms,
            details=json.dumps(details) if details is not None else None,
        )
        add_result = db.add(entry)
        if inspect.isawaitable(add_result):
            await add_result
        await db.flush()
    except Exception:
        logger.exception("Failed to record stats timing event: %s", event_name)


async def record_request_to_approval_timing(
    db: AsyncSession,
    *,
    request_id: int | None,
    approved_at: datetime | None = None,
) -> None:
    if request_id is None:
        return
    result = await db.execute(select(Request).where(Request.id == request_id))
    request = result.scalar_one_or_none()
    if request is None or request.created_at is None:
        return
    approval_time = approved_at or datetime.now(UTC)
    request_created = request.created_at
    if request_created.tzinfo is None:
        request_created = request_created.replace(tzinfo=UTC)
    if approval_time.tzinfo is None:
        approval_time = approval_time.replace(tzinfo=UTC)
    await record_timing_event(
        db,
        event_name="request_to_approval",
        request_id=request_id,
        duration_ms=(approval_time - request_created).total_seconds() * 1000,
    )


async def record_selected_release_fact(
    db: AsyncSession,
    *,
    request_id: int | None,
    title: str,
    indexer: str | None,
    resolution: str | None,
    selection_source: str,
    release_id: int | None = None,
    staged_torrent_id: int | None = None,
    approved_at: datetime | None = None,
) -> None:
    """Persist an immutable fact for a newly approved selected release."""
    if request_id is None:
        return
    approved_time = approved_at or datetime.now(UTC)
    try:
        fact = StatsReleaseFact(
            request_id=request_id,
            release_id=release_id,
            staged_torrent_id=staged_torrent_id,
            title=title,
            indexer=indexer,
            resolution=resolution,
            resolution_bucket=resolution_bucket(resolution),
            selection_source=selection_source,
            approved_at=approved_time,
        )
        db.add(fact)
        await db.flush()
        await record_request_to_approval_timing(
            db,
            request_id=request_id,
            approved_at=approved_time,
        )
    except Exception:
        logger.exception("Failed to record selected release stats fact")


async def record_staged_release_fact(db: AsyncSession, torrent: StagedTorrent) -> None:
    """Record approval facts for a staged torrent."""
    release_id: int | None = None
    resolution: str | None = None
    if torrent.request_id is not None:
        result = await db.execute(
            select(Release).where(
                Release.request_id == torrent.request_id,
                Release.title == torrent.title,
            )
        )
        release = result.scalars().first()
        if release is not None:
            release_id = release.id
            resolution = release.resolution
    await record_selected_release_fact(
        db,
        request_id=torrent.request_id,
        title=torrent.title,
        indexer=torrent.indexer,
        resolution=resolution,
        selection_source=torrent.selection_source,
        release_id=release_id,
        staged_torrent_id=torrent.id,
    )


async def record_release_fact(db: AsyncSession, release: Release, *, selection_source: str) -> None:
    """Record approval facts for a direct-send stored release."""
    await record_selected_release_fact(
        db,
        request_id=release.request_id,
        title=release.title,
        indexer=release.indexer,
        resolution=release.resolution,
        selection_source=selection_source,
        release_id=release.id,
    )


async def record_rule_outcomes(
    db: AsyncSession,
    *,
    request_id: int,
    evaluations: list[ReleaseEvaluation],
    stored_releases_by_key: dict[str, Release],
) -> None:
    """Persist per-rule outcomes for the current evaluation run."""
    try:
        for evaluation in evaluations:
            key = get_release_persistence_key(
                title=evaluation.release.title,
                info_hash=evaluation.release.info_hash,
            )
            stored = stored_releases_by_key.get(key)
            for match in evaluation.matches:
                outcome = "matched" if match.matched else "not_matched"
                if evaluation.rejection_reason and match.matched:
                    outcome = "failed"
                elif evaluation.passed:
                    outcome = "passed" if match.matched else "not_matched"
                db.add(
                    StatsRuleOutcome(
                        request_id=request_id,
                        release_id=stored.id if stored is not None else None,
                        release_title=evaluation.release.title,
                        rule_id=match.rule_id,
                        rule_name=match.rule_name,
                        matched=1 if match.matched else 0,
                        outcome=outcome,
                        score_delta=match.score_delta,
                        rejection_reason=evaluation.rejection_reason,
                    )
                )
        await db.flush()
    except Exception:
        logger.exception("Failed to record stats rule outcomes for request_id=%s", request_id)
