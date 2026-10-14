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

from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation

logger = logging.getLogger(__name__)


# ── Release selection helpers ─────────────────────────────────────────


def release_rank_key(evaluation: ReleaseEvaluation) -> tuple[int, int, str, str]:
    """Sort quality-adjusted scores first, then reported swarm size consistently."""
    return (
        -evaluation.total_score,
        -max(0, evaluation.release.seeders),
        evaluation.release.title.casefold(),
        evaluation.release.indexer.casefold(),
    )


def get_best_passing(
    all_evaluated: Sequence[ReleaseEvaluation],
) -> ReleaseEvaluation | None:
    """Return the highest‑scoring passing evaluation, with deterministic tie-breaks."""
    passed = [e for e in all_evaluated if e.passed]
    if not passed:
        return None
    passed.sort(key=release_rank_key)
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
