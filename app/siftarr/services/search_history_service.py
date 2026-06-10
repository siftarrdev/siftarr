"""Helpers for recording compact search-run history."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models._base import utc_now
from app.siftarr.models.search_history import SearchRun, SearchRunCandidate
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.releases.release_serializers import compact_candidate_snapshot
from app.siftarr.services.releases.release_storage import get_release_persistence_key


class SearchHistoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def start_run(
        self,
        request_id: int,
        *,
        trigger: str = "automatic",
        source: str = "prowlarr",
        search_mode: str | None = None,
        scope: dict[str, object] | None = None,
    ) -> SearchRun:
        run = SearchRun(
            request_id=request_id,
            trigger=trigger,
            source=source,
            search_mode=search_mode,
            scope=scope,
            status="running",
            outcome=None,
        )
        self.db.add(run)
        await self.db.flush()
        return run

    @staticmethod
    def normalize_failures(failures: Iterable[object] | None) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for failure in failures or []:
            if isinstance(failure, dict):
                reason = str(failure.get("reason") or failure.get("message") or failure)
                category = str(failure.get("category") or "failure")
            else:
                reason = str(failure)
                category = "failure"
            normalized.append({"reason": reason[:500], "category": category})
        return normalized[:20]

    @staticmethod
    def summarize_counts(
        evaluations: Iterable[ReleaseEvaluation],
        *,
        staged: int = 0,
        sent: int = 0,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        items = list(evaluations)
        counts: dict[str, object] = {
            "total": len(items),
            "passed": sum(1 for item in items if item.passed),
            "rejected": sum(1 for item in items if not item.passed),
            "staged": staged,
            "sent": sent,
        }
        if extra:
            counts.update(extra)
        return counts

    @staticmethod
    def winner_summary(winners: Iterable[ReleaseEvaluation] | None) -> dict[str, object] | None:
        winner_list = list(winners or [])
        if not winner_list:
            return None
        return {
            "count": len(winner_list),
            "releases": [compact_candidate_snapshot(w) for w in winner_list[:5]],
        }

    async def finalize_run(
        self,
        run: SearchRun,
        *,
        evaluations: list[ReleaseEvaluation] | None = None,
        stored_releases_by_key: dict[str, Any] | None = None,
        winners: list[ReleaseEvaluation] | None = None,
        failures: Iterable[object] | None = None,
        outcome: str | None = None,
        status: str = "completed",
        counts: dict[str, object] | None = None,
        error: str | None = None,
    ) -> SearchRun:
        evaluations = evaluations or []
        stored_releases_by_key = stored_releases_by_key or {}
        run.status = status
        run.outcome = outcome or ("failed" if error else "completed")
        run.finished_at = utc_now()
        run.error = error[:2000] if error else None
        run.failure_summaries = self.normalize_failures(failures)
        run.counts = counts or self.summarize_counts(evaluations)
        run.winner_summary = self.winner_summary(winners)
        for evaluation in evaluations[:200]:
            key = get_release_persistence_key(
                title=evaluation.release.title, info_hash=evaluation.release.info_hash
            )
            stored = stored_releases_by_key.get(key)
            stored_id = getattr(stored, "id", None)
            snapshot = compact_candidate_snapshot(evaluation, stored_release_id=stored_id)
            self.db.add(
                SearchRunCandidate(
                    search_run_id=run.id,
                    request_id=run.request_id,
                    stored_release_id=stored_id,
                    title=evaluation.release.title,
                    status=str(snapshot["status"]),
                    score=int(snapshot["score"]),
                    summary=snapshot,
                    rule_evidence=snapshot["rule_evidence"],
                    parse_metadata=snapshot["parse_metadata"],
                    rejection_reason=evaluation.rejection_reason[:500]
                    if evaluation.rejection_reason
                    else None,
                )
            )
        await self.db.commit()
        return run

    async def fail_run(self, run: SearchRun, error: object) -> SearchRun:
        return await self.finalize_run(
            run,
            failures=[error],
            outcome="failed",
            status="failed",
            error=str(error),
        )
