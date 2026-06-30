"""Stats aggregation service for the Stats API/UI."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.models import (
    ActivityLog,
    EventType,
    Release,
    Request,
    Rule,
    StagedTorrent,
    StatsReleaseFact,
    StatsRuleOutcome,
    StatsTimingEvent,
)
from app.siftarr.models.staged_torrent import STAGED_STATUS_APPROVED

PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90}
STATS_CACHE_TTL_SECONDS = 30
_STATS_CACHE: dict[tuple[str, str | None, str | None], tuple[datetime, dict[str, Any]]] = {}

TIMING_LABELS = {
    "search_completed": "Search duration",
    "request_to_approval": "Request to approval",
}


@dataclass(frozen=True)
class StatsRange:
    key: str
    label: str
    start: datetime | None
    end: datetime | None


class StatsRangeError(ValueError):
    """Raised for invalid stats range input."""


def _parse_date(value: str | None, field: str) -> date:
    if not value:
        raise StatsRangeError(f"{field} is required for custom ranges")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StatsRangeError(f"{field} must be YYYY-MM-DD") from exc


def build_stats_range(
    range_key: str = "30d",
    *,
    start: str | None = None,
    end: str | None = None,
    now: datetime | None = None,
) -> StatsRange:
    """Validate and normalize all-time, preset, and custom date ranges."""
    current = now or datetime.now(UTC)
    current_date = current.date()
    if range_key == "all":
        return StatsRange("all", "All time", None, None)
    if range_key in PRESET_DAYS:
        days = PRESET_DAYS[range_key]
        start_date = current_date - timedelta(days=days - 1)
        return StatsRange(
            range_key,
            f"Last {days} days",
            datetime.combine(start_date, time.min),
            datetime.combine(current_date + timedelta(days=1), time.min),
        )
    if range_key == "custom":
        start_date = _parse_date(start, "start")
        end_date = _parse_date(end, "end")
        if start_date > end_date:
            raise StatsRangeError("start must be before or equal to end")
        return StatsRange(
            "custom",
            f"{start_date.isoformat()} to {end_date.isoformat()}",
            datetime.combine(start_date, time.min),
            datetime.combine(end_date + timedelta(days=1), time.min),
        )
    raise StatsRangeError("range must be one of all, 7d, 30d, 90d, custom")


def _apply_range(stmt: Any, column: Any, stats_range: StatsRange) -> Any:
    if stats_range.start is not None:
        stmt = stmt.where(column >= stats_range.start)
    if stats_range.end is not None:
        stmt = stmt.where(column < stats_range.end)
    return stmt


def _series(rows: list[Any]) -> list[dict[str, Any]]:
    return [{"label": row[0] or "Unknown", "value": int(row[1] or 0)} for row in rows]


def _stats_cache_key(stats_range: StatsRange) -> tuple[str, str | None, str | None]:
    return (
        stats_range.key,
        stats_range.start.isoformat() if stats_range.start else None,
        stats_range.end.isoformat() if stats_range.end else None,
    )


def clear_stats_cache() -> None:
    _STATS_CACHE.clear()


class StatsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_stats(self, stats_range: StatsRange) -> dict[str, Any]:
        key = _stats_cache_key(stats_range)
        now = datetime.now(UTC)
        cached = _STATS_CACHE.get(key)
        if cached is not None:
            cached_at, payload = cached
            if (now - cached_at).total_seconds() < STATS_CACHE_TTL_SECONDS:
                return deepcopy(payload)
            _STATS_CACHE.pop(key, None)
        payload = await self._compute_stats(stats_range)
        if not payload.get("empty"):
            _STATS_CACHE[key] = (now, deepcopy(payload))
        return deepcopy(payload)

    async def _compute_stats(self, stats_range: StatsRange) -> dict[str, Any]:
        total_requests = await self._scalar_count(
            _apply_range(select(func.count(Request.id)), Request.created_at, stats_range)
        )
        total_rules = await self._scalar_count(select(func.count(Rule.id)))
        enabled_rules = await self._scalar_count(
            select(func.count(Rule.id)).where(Rule.is_enabled.is_(True))
        )
        downloads_processed = await self._scalar_count(
            _apply_range(
                select(func.count(distinct(StatsReleaseFact.request_id))),
                StatsReleaseFact.approved_at,
                stats_range,
            )
        )
        release_fact_count = downloads_processed
        downloads_source = "stats"
        if release_fact_count == 0:
            downloads_processed = await self._historical_downloads_processed(stats_range)
            downloads_source = "historical" if downloads_processed else "unavailable"
        evaluated_requests = await self._scalar_count(
            _apply_range(
                select(func.count(distinct(StatsRuleOutcome.request_id))),
                StatsRuleOutcome.created_at,
                stats_range,
            )
        )
        rule_outcomes_source = "stats"
        if evaluated_requests == 0:
            evaluated_requests = await self._historical_evaluated_requests(stats_range)
            rule_outcomes_source = "historical" if evaluated_requests else "unavailable"
        approval_rate = None
        if evaluated_requests and downloads_source != "unavailable":
            approval_rate = round((downloads_processed / evaluated_requests) * 100, 1)

        resolution_split = await self._group_counts(
            _apply_range(
                select(
                    StatsReleaseFact.resolution_bucket, func.count(StatsReleaseFact.id)
                ).group_by(StatsReleaseFact.resolution_bucket),
                StatsReleaseFact.approved_at,
                stats_range,
            )
        )
        resolution_source = "stats"
        if release_fact_count == 0:
            resolution_split = await self._historical_resolution_split(stats_range)
            resolution_source = "historical" if resolution_split else "unavailable"
        source_split = await self._group_counts(
            _apply_range(
                select(StatsReleaseFact.indexer, func.count(StatsReleaseFact.id)).group_by(
                    StatsReleaseFact.indexer
                ),
                StatsReleaseFact.approved_at,
                stats_range,
            )
        )
        source_source = "stats"
        if release_fact_count == 0:
            source_split = await self._historical_source_split(stats_range)
            source_source = "historical" if source_split else "unavailable"
        rule_outcomes = await self._group_counts(
            _apply_range(
                select(StatsRuleOutcome.outcome, func.count(StatsRuleOutcome.id)).group_by(
                    StatsRuleOutcome.outcome
                ),
                StatsRuleOutcome.created_at,
                stats_range,
            )
        )
        if rule_outcomes_source != "stats":
            rule_outcomes = []
        processing_times = await self._processing_times(stats_range)
        downloads_series = await self._daily_counts(
            _apply_range(
                select(func.date(StatsReleaseFact.approved_at), func.count(StatsReleaseFact.id))
                .group_by(func.date(StatsReleaseFact.approved_at))
                .order_by(func.date(StatsReleaseFact.approved_at)),
                StatsReleaseFact.approved_at,
                stats_range,
            ),
            stats_range,
        )
        downloads_series_source = "stats"
        if release_fact_count == 0:
            downloads_series = await self._historical_downloads_series(stats_range)
            downloads_series_source = (
                "historical" if self._point_series_has_data(downloads_series) else "unavailable"
            )
        failures_series = await self._daily_counts(
            _apply_range(
                select(func.date(StatsRuleOutcome.created_at), func.count(StatsRuleOutcome.id))
                .where(StatsRuleOutcome.outcome == "failed")
                .group_by(func.date(StatsRuleOutcome.created_at))
                .order_by(func.date(StatsRuleOutcome.created_at)),
                StatsRuleOutcome.created_at,
                stats_range,
            ),
            stats_range,
        )
        rule_rejection_trends = await self._rule_rejection_trends(stats_range)
        indexer_behavior = await self._indexer_behavior(stats_range)
        indexer_behavior_source = "stats"
        if release_fact_count == 0:
            indexer_behavior = await self._historical_indexer_behavior(stats_range)
            indexer_behavior_source = "historical" if indexer_behavior else "unavailable"

        has_activity = any(
            [
                total_requests,
                downloads_processed,
                evaluated_requests,
                resolution_split,
                source_split,
            ]
        )
        return {
            "range": {
                "key": stats_range.key,
                "label": stats_range.label,
                "start": stats_range.start.date().isoformat() if stats_range.start else None,
                "end": (stats_range.end.date() - timedelta(days=1)).isoformat()
                if stats_range.end
                else None,
            },
            "cards": {
                "total_requests": total_requests,
                "downloads_processed": downloads_processed,
                "approval_rate": approval_rate,
                "evaluated_requests": evaluated_requests,
                "total_rules": total_rules,
                "enabled_rules": enabled_rules,
                "avg_search_ms": self._timing_average(processing_times, "search_completed"),
                "avg_request_to_approval_ms": self._timing_average(
                    processing_times, "request_to_approval"
                ),
            },
            "charts": {
                "resolution_split": resolution_split,
                "source_split": source_split,
                "rule_outcomes": rule_outcomes,
                "processing_times": processing_times,
                "time_series": {
                    "downloads": downloads_series,
                    "failures": failures_series,
                    "rule_rejections": rule_rejection_trends,
                    "indexer_behavior": indexer_behavior,
                },
            },
            "availability": {
                "downloads_processed": downloads_source,
                "approval_rate": "available" if approval_rate is not None else "unavailable",
                "evaluated_requests": rule_outcomes_source,
                "resolution_split": resolution_source,
                "source_split": source_source,
                "rule_outcomes": "stats" if rule_outcomes_source == "stats" else "unavailable",
                "processing_times": "stats" if processing_times else "unavailable",
                "downloads_series": downloads_series_source,
                "failures_series": "stats" if rule_outcomes_source == "stats" else "unavailable",
                "rule_rejections_series": "stats"
                if rule_outcomes_source == "stats"
                else "unavailable",
                "indexer_behavior_series": indexer_behavior_source,
            },
            "empty": not has_activity,
        }

    async def _scalar_count(self, stmt: Any) -> int:
        result = await self.db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def _group_counts(self, stmt: Any) -> list[dict[str, Any]]:
        result = await self.db.execute(stmt)
        return _series(list(result.all()))

    async def _processing_times(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        stmt = (
            select(
                StatsTimingEvent.event_name,
                func.avg(StatsTimingEvent.duration_ms),
                func.count(StatsTimingEvent.id),
            )
            .where(StatsTimingEvent.duration_ms.is_not(None))
            .where(StatsTimingEvent.event_name.in_(TIMING_LABELS))
            .group_by(StatsTimingEvent.event_name)
        )
        stmt = _apply_range(stmt, StatsTimingEvent.created_at, stats_range)
        result = await self.db.execute(stmt)
        return [
            {
                "key": key,
                "label": TIMING_LABELS.get(key, key),
                "avg_ms": round(float(avg or 0), 1),
                "count": int(count or 0),
            }
            for key, avg, count in result.all()
        ]

    def _range_days(self, stats_range: StatsRange, labels: list[str]) -> list[str]:
        if stats_range.start is None or stats_range.end is None:
            return labels
        days = []
        current = stats_range.start.date()
        end_date = stats_range.end.date()
        while current < end_date:
            days.append(current.isoformat())
            current += timedelta(days=1)
        return days

    async def _daily_counts(self, stmt: Any, stats_range: StatsRange) -> list[dict[str, Any]]:
        result = await self.db.execute(stmt)
        counts = {str(day): int(count or 0) for day, count in result.all() if day is not None}
        labels = self._range_days(stats_range, sorted(counts))
        return [{"date": day, "label": day, "value": counts.get(day, 0)} for day in labels]

    async def _rule_rejection_trends(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        stmt = (
            select(
                func.date(StatsRuleOutcome.created_at),
                StatsRuleOutcome.rule_name,
                func.count(StatsRuleOutcome.id),
            )
            .where(StatsRuleOutcome.outcome == "failed")
            .group_by(func.date(StatsRuleOutcome.created_at), StatsRuleOutcome.rule_name)
            .order_by(func.date(StatsRuleOutcome.created_at), StatsRuleOutcome.rule_name)
        )
        stmt = _apply_range(stmt, StatsRuleOutcome.created_at, stats_range)
        result = await self.db.execute(stmt)
        rows = [(str(day), rule or "Unknown", int(count or 0)) for day, rule, count in result.all()]
        return self._multi_series(rows, stats_range)

    async def _indexer_behavior(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        stmt = (
            select(
                func.date(StatsReleaseFact.approved_at),
                StatsReleaseFact.indexer,
                func.count(StatsReleaseFact.id),
            )
            .group_by(func.date(StatsReleaseFact.approved_at), StatsReleaseFact.indexer)
            .order_by(func.date(StatsReleaseFact.approved_at), StatsReleaseFact.indexer)
        )
        stmt = _apply_range(stmt, StatsReleaseFact.approved_at, stats_range)
        result = await self.db.execute(stmt)
        rows = [
            (str(day), indexer or "Unknown", int(count or 0))
            for day, indexer, count in result.all()
        ]
        return self._multi_series(rows, stats_range)

    def _multi_series(
        self, rows: list[tuple[str, str, int]], stats_range: StatsRange
    ) -> list[dict[str, Any]]:
        labels = self._range_days(stats_range, sorted({day for day, _, _ in rows}))
        totals: dict[str, int] = {}
        values: dict[tuple[str, str], int] = {}
        for day, name, count in rows:
            totals[name] = totals.get(name, 0) + count
            values[(name, day)] = count
        names = sorted(totals, key=lambda name: (-totals[name], name))[:5]
        return [
            {
                "label": name,
                "points": [
                    {"date": day, "label": day, "value": values.get((name, day), 0)}
                    for day in labels
                ],
            }
            for name in names
        ]

    @staticmethod
    def _point_series_has_data(rows: list[dict[str, Any]]) -> bool:
        return any((row.get("value") or 0) > 0 for row in rows)

    async def _historical_downloads_processed(self, stats_range: StatsRange) -> int:
        return await self._scalar_count(
            _apply_range(
                select(func.count(distinct(StagedTorrent.request_id))).where(
                    StagedTorrent.status == STAGED_STATUS_APPROVED,
                    StagedTorrent.request_id.is_not(None),
                ),
                StagedTorrent.updated_at,
                stats_range,
            )
        )

    async def _historical_evaluated_requests(self, stats_range: StatsRange) -> int:
        return await self._scalar_count(
            _apply_range(
                select(func.count(distinct(ActivityLog.request_id))).where(
                    ActivityLog.event_type == EventType.RULE_EVALUATION.value,
                    ActivityLog.request_id.is_not(None),
                ),
                ActivityLog.created_at,
                stats_range,
            )
        )

    async def _historical_source_split(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        return await self._group_counts(
            _apply_range(
                select(StagedTorrent.indexer, func.count(StagedTorrent.id))
                .where(StagedTorrent.status == STAGED_STATUS_APPROVED)
                .group_by(StagedTorrent.indexer),
                StagedTorrent.updated_at,
                stats_range,
            )
        )

    async def _historical_resolution_split(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        stmt = (
            select(Release.resolution, func.count(distinct(StagedTorrent.id)))
            .join(
                Release,
                (Release.request_id == StagedTorrent.request_id)
                & (Release.title == StagedTorrent.title),
            )
            .where(StagedTorrent.status == STAGED_STATUS_APPROVED)
            .group_by(Release.resolution)
        )
        rows = await self._group_counts(_apply_range(stmt, StagedTorrent.updated_at, stats_range))
        return [row for row in rows if row["label"] != "Unknown"]

    async def _historical_downloads_series(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        return await self._daily_counts(
            _apply_range(
                select(
                    func.date(StagedTorrent.updated_at),
                    func.count(distinct(StagedTorrent.request_id)),
                )
                .where(
                    StagedTorrent.status == STAGED_STATUS_APPROVED,
                    StagedTorrent.request_id.is_not(None),
                )
                .group_by(func.date(StagedTorrent.updated_at))
                .order_by(func.date(StagedTorrent.updated_at)),
                StagedTorrent.updated_at,
                stats_range,
            ),
            stats_range,
        )

    async def _historical_indexer_behavior(self, stats_range: StatsRange) -> list[dict[str, Any]]:
        stmt = (
            select(
                func.date(StagedTorrent.updated_at),
                StagedTorrent.indexer,
                func.count(StagedTorrent.id),
            )
            .where(StagedTorrent.status == STAGED_STATUS_APPROVED)
            .group_by(func.date(StagedTorrent.updated_at), StagedTorrent.indexer)
            .order_by(func.date(StagedTorrent.updated_at), StagedTorrent.indexer)
        )
        stmt = _apply_range(stmt, StagedTorrent.updated_at, stats_range)
        result = await self.db.execute(stmt)
        rows = [
            (str(day), indexer or "Unknown", int(count or 0))
            for day, indexer, count in result.all()
        ]
        return self._multi_series(rows, stats_range)

    @staticmethod
    def _timing_average(processing_times: list[dict[str, Any]], key: str) -> float | None:
        for row in processing_times:
            if row["key"] == key:
                return row["avg_ms"]
        return None
