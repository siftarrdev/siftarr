"""Shared helpers for the movie/TV decision pipeline.

Extracts patterns that are identical (or nearly identical) across
``MovieDecisionService`` and ``TVDecisionService`` so each decision
service only owns its media-type‑specific logic.

Callers should import the helpers needed and compose them with
their own search/evaluation flow.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models.activity_log import EventType
from app.siftarr.models.rule import Rule
from app.siftarr.services.activity_log_service import ActivityLogService
from app.siftarr.services.pending_queue_service import PendingQueueService
from app.siftarr.services.rule_engine import ReleaseEvaluation, RuleEngine

logger = logging.getLogger(__name__)


# ── Rule engine ───────────────────────────────────────────────────────


async def build_rule_engine(
    db: AsyncSession,
    media_type: str,
) -> RuleEngine:
    """Load rules from the database and build a :class:`RuleEngine`.

    Args:
        db: Active database session.
        media_type: ``"movie"`` or ``"tv"`` — used to scope rules.

    Returns:
        A configured :class:`RuleEngine`.
    """
    result = await db.execute(select(Rule))
    rules = list(result.scalars().all())
    return RuleEngine.from_db_rules(rules=rules, media_type=media_type)


# ── Activity logging ──────────────────────────────────────────────────


async def log_rule_evaluation(
    db: AsyncSession,
    request_id: int,
    **details: object,
) -> None:
    """Log a :attr:`EventType.RULE_EVALUATION` activity entry."""
    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.RULE_EVALUATION,
        request_id=request_id,
        details=details,
    )


async def log_release_staged(
    db: AsyncSession,
    request_id: int,
    **details: object,
) -> None:
    """Log a :attr:`EventType.RELEASE_STAGED` activity entry."""
    activity_log = ActivityLogService(db)
    await activity_log.log(
        EventType.RELEASE_STAGED,
        request_id=request_id,
        details=details,
    )


# ── Pending queue ─────────────────────────────────────────────────────


async def add_to_pending_queue(
    db: AsyncSession,
    request_id: int,
    *,
    error_message: str | None = None,
) -> None:
    """Add a request to the pending/retry queue."""
    queue_service = PendingQueueService(db)
    await queue_service.add_to_queue(request_id, error_message=error_message)


# ── Release selection helpers ─────────────────────────────────────────


def get_best_passing(
    all_evaluated: Sequence[ReleaseEvaluation],
) -> ReleaseEvaluation | None:
    """Return the highest‑scoring passing evaluation, or *None*."""
    passed = [e for e in all_evaluated if e.passed]
    if not passed:
        return None
    passed.sort(key=lambda e: e.total_score, reverse=True)
    return passed[0]


def collect_rejection_reasons(
    all_evaluated: Sequence[ReleaseEvaluation],
    *,
    max_reasons: int = 5,
) -> list[str]:
    """Return up to *max_reasons* unique non‑None rejection reasons."""
    seen: set[str] = set()
    reasons: list[str] = []
    for e in all_evaluated:
        if e.rejection_reason and e.rejection_reason not in seen:
            seen.add(e.rejection_reason)
            reasons.append(e.rejection_reason)
            if len(reasons) >= max_reasons:
                break
    return reasons
