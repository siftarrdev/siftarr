"""Helpers for recording compact search-run history."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models._base import utc_now
from app.siftarr.models.release import Release
from app.siftarr.models.search_history import SearchRun, SearchRunCandidate
from app.siftarr.models.staged_torrent import StagedTorrent
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

    async def list_runs(
        self,
        request_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        status: str | None = None,
        outcome: str | None = None,
        source: str | None = None,
        search_mode: str | None = None,
        started_after: object | None = None,
        started_before: object | None = None,
    ) -> dict[str, object]:
        filters = [SearchRun.request_id == request_id]
        if status:
            filters.append(SearchRun.status == status)
        if outcome:
            filters.append(SearchRun.outcome == outcome)
        if source:
            filters.append(SearchRun.source == source)
        if search_mode:
            filters.append(SearchRun.search_mode == search_mode)
        if started_after:
            filters.append(SearchRun.started_at >= started_after)
        if started_before:
            filters.append(SearchRun.started_at <= started_before)

        total = await self.db.scalar(select(func.count(SearchRun.id)).where(*filters))
        runs = (
            (
                await self.db.execute(
                    select(SearchRun)
                    .where(*filters)
                    .order_by(SearchRun.started_at.desc(), SearchRun.id.desc())
                    .offset(max(offset, 0))
                    .limit(min(max(limit, 1), 100))
                )
            )
            .scalars()
            .all()
        )
        candidate_rows = {}
        if runs:
            run_ids = [run.id for run in runs]
            candidates = (
                (
                    await self.db.execute(
                        select(SearchRunCandidate)
                        .where(SearchRunCandidate.search_run_id.in_(run_ids))
                        .order_by(SearchRunCandidate.score.desc(), SearchRunCandidate.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            for candidate in candidates:
                candidate_rows.setdefault(candidate.search_run_id, []).append(candidate)
        return {
            "request_id": request_id,
            "offset": max(offset, 0),
            "limit": min(max(limit, 1), 100),
            "total": total or 0,
            "runs": [self.serialize_run(run, candidate_rows.get(run.id, [])) for run in runs],
        }

    @staticmethod
    def serialize_run(run: SearchRun, candidates: list[SearchRunCandidate]) -> dict[str, object]:
        return {
            "id": run.id,
            "request_id": run.request_id,
            "trigger": run.trigger,
            "source": run.source,
            "search_mode": run.search_mode,
            "scope": run.scope,
            "status": run.status,
            "outcome": run.outcome,
            "counts": run.counts or {},
            "winner_summary": run.winner_summary,
            "failure_summaries": run.failure_summaries or [],
            "error": run.error,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "candidates": [SearchHistoryService.serialize_candidate(c) for c in candidates[:25]],
        }

    @staticmethod
    def serialize_candidate(candidate: SearchRunCandidate) -> dict[str, object]:
        summary = dict(candidate.summary or {})
        summary.update(
            {
                "id": candidate.id,
                "search_run_id": candidate.search_run_id,
                "request_id": candidate.request_id,
                "title": candidate.title,
                "status": candidate.status,
                "score": candidate.score,
                "stored_release_id": candidate.stored_release_id,
                "rule_evidence": candidate.rule_evidence or summary.get("rule_evidence") or {},
                "parse_metadata": candidate.parse_metadata or summary.get("parse_metadata") or {},
                "rejection_reason": candidate.rejection_reason or summary.get("rejection_reason"),
                "source": "search_history",
            }
        )
        return summary

    async def staged_alternatives(self, staged_id: int) -> dict[str, object] | None:
        selected = await self.db.get(StagedTorrent, staged_id)
        if not selected or selected.request_id is None:
            return None
        request_id = selected.request_id
        staged = (
            (
                await self.db.execute(
                    select(StagedTorrent).where(StagedTorrent.request_id == request_id)
                )
            )
            .scalars()
            .all()
        )
        releases = (
            (await self.db.execute(select(Release).where(Release.request_id == request_id)))
            .scalars()
            .all()
        )
        candidates = (
            (
                await self.db.execute(
                    select(SearchRunCandidate)
                    .where(SearchRunCandidate.request_id == request_id)
                    .order_by(SearchRunCandidate.created_at.desc(), SearchRunCandidate.score.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        items = [self._staged_payload(t, t.id == staged_id) for t in staged]
        items.extend(self._release_payload(r) for r in releases)
        items.extend(self.serialize_candidate(c) for c in candidates)
        return {
            "staged_id": staged_id,
            "request_id": request_id,
            "selected": self._staged_payload(selected, True),
            "alternatives": items,
        }

    @staticmethod
    def _staged_payload(torrent: StagedTorrent, selected: bool) -> dict[str, object]:
        return {
            "id": torrent.id,
            "title": torrent.title,
            "size_bytes": torrent.size,
            "score": torrent.score,
            "indexer": torrent.indexer,
            "status": torrent.status,
            "selected": selected,
            "active": torrent.status in {"staged", "approved"},
            "selection_source": torrent.selection_source,
            "source": "staged",
        }

    @staticmethod
    def _release_payload(release: Release) -> dict[str, object]:
        return {
            "id": release.id,
            "title": release.title,
            "size_bytes": release.size,
            "seeders": release.seeders,
            "indexer": release.indexer,
            "resolution": release.resolution,
            "codec": release.codec,
            "score": release.score,
            "status": "passed" if release.passed_rules else "rejected",
            "rejection_reason": release.rejection_reason,
            "rule_evidence": release.rule_evidence or {},
            "parse_metadata": release.parse_metadata or {},
            "source": "stored_release",
        }
