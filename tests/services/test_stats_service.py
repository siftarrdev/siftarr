from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models import (
    Base,
    Request,
    Rule,
    StatsReleaseFact,
    StatsRuleOutcome,
    StatsTimingEvent,
)
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.models.rule import RuleType
from app.siftarr.services.stats_service import StatsRangeError, StatsService, build_stats_range


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def test_range_validation_all_and_custom():
    assert build_stats_range("all").start is None
    custom = build_stats_range("custom", start="2026-05-01", end="2026-05-02")
    assert custom.start.date().isoformat() == "2026-05-01"
    assert custom.end.date().isoformat() == "2026-05-03"
    with pytest.raises(StatsRangeError):
        build_stats_range("custom", start="2026-05-03", end="2026-05-02")


@pytest.mark.asyncio
async def test_empty_state_payload(session_maker):
    async with session_maker() as session:
        payload = await StatsService(session).get_stats(build_stats_range("all"))

    assert payload["empty"] is True
    assert payload["cards"]["total_requests"] == 0
    assert payload["charts"]["resolution_split"] == []


@pytest.mark.asyncio
async def test_stats_aggregate_cards_splits_outcomes_and_timings(session_maker):
    created = datetime(2026, 5, 1, 12, 0)
    async with session_maker() as session:
        request = Request(
            external_id="1",
            media_type=MediaType.MOVIE,
            title="Movie",
            status=RequestStatus.COMPLETED,
            created_at=created,
        )
        session.add(request)
        await session.flush()
        session.add(Rule(name="Rule", rule_type=RuleType.SCORER, pattern="x", is_enabled=True))
        session.add_all(
            [
                StatsReleaseFact(
                    request_id=request.id,
                    title="Movie.2160p",
                    indexer="IndexerA",
                    resolution="2160p",
                    resolution_bucket="4K",
                    selection_source="autostage",
                    approved_at=created,
                ),
                StatsRuleOutcome(
                    request_id=request.id,
                    release_title="Movie.2160p",
                    rule_name="Rule",
                    matched=1,
                    outcome="passed",
                    created_at=created,
                ),
                StatsRuleOutcome(
                    request_id=999,
                    release_title="Other",
                    rule_name="Rule",
                    matched=1,
                    outcome="failed",
                    created_at=created,
                ),
                StatsTimingEvent(
                    request_id=request.id,
                    event_name="search_completed",
                    duration_ms=1200,
                    created_at=created,
                ),
                StatsTimingEvent(
                    request_id=request.id,
                    event_name="request_to_approval",
                    duration_ms=60000,
                    created_at=created,
                ),
            ]
        )
        await session.commit()

        payload = await StatsService(session).get_stats(build_stats_range("all"))

    assert payload["empty"] is False
    assert payload["cards"]["total_requests"] == 1
    assert payload["cards"]["downloads_processed"] == 1
    assert payload["cards"]["evaluated_requests"] == 2
    assert payload["cards"]["approval_rate"] == 50.0
    assert payload["cards"]["avg_search_ms"] == 1200.0
    assert payload["charts"]["resolution_split"] == [{"label": "4K", "value": 1}]
    assert payload["charts"]["source_split"] == [{"label": "IndexerA", "value": 1}]
    assert {row["label"]: row["value"] for row in payload["charts"]["rule_outcomes"]} == {
        "failed": 1,
        "passed": 1,
    }


@pytest.mark.asyncio
async def test_custom_range_filters_event_dates(session_maker):
    async with session_maker() as session:
        for rid, day in [(1, 1), (2, 10)]:
            session.add(
                Request(
                    id=rid,
                    external_id=str(rid),
                    media_type=MediaType.MOVIE,
                    title=str(rid),
                    status=RequestStatus.COMPLETED,
                    created_at=datetime(2026, 5, day),
                )
            )
        await session.commit()
        payload = await StatsService(session).get_stats(
            build_stats_range("custom", start="2026-05-02", end="2026-05-31")
        )

    assert payload["cards"]["total_requests"] == 1
