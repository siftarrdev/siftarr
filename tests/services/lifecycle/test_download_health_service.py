"""Focused tests for current-state download health observations."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.siftarr.models import Base
from app.siftarr.models.download_health import (
    HEALTH_STATUS_AMBIGUOUS,
    HEALTH_STATUS_RECOVERY_ELIGIBLE,
    HEALTH_STATUS_SUSPENDED,
    HEALTH_STATUS_WARNING,
    DownloadHealth,
)
from app.siftarr.models.episode import Episode
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService
from app.siftarr.services.lifecycle.download_health_service import (
    DownloadHealthService,
    DownloadRecoveryNotEligible,
)


class FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush = AsyncMock()

    def add(self, value: Any) -> None:
        self.added.append(value)


def staged(
    *, staged_id: int = 1, title: str = "Example Movie 2025", info_hash: str | None = "a" * 40
):
    return SimpleNamespace(
        id=staged_id,
        request_id=None,
        title=title,
        info_hash=info_hash,
        target_scope=None,
        created_at=None,
    )


def attempt(*, staged_id: int = 1, title: str = "Example Movie 2025") -> DownloadHealth:
    return DownloadHealth(
        id=10,
        staged_torrent_id=staged_id,
        request_id=None,
        title=title,
        first_observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def service_for(
    staged_row: Any, health: DownloadHealth | None, torrents: list[dict]
) -> tuple[Any, FakeDb]:
    db = FakeDb()
    qbit: Any = AsyncMock()
    service: Any = DownloadHealthService(cast(AsyncSession, db), cast(QbittorrentService, qbit))
    mock_service = cast(Any, service)
    mock_service._load_approved_rows = AsyncMock(return_value=[(staged_row, None, health)])
    mock_service._historical_start_times = AsyncMock(return_value={})
    qbit.get_all_active_torrents_or_raise = AsyncMock(return_value=torrents)
    return service, db


class DatabaseQbit:
    def __init__(self, torrents: list[dict] | None = None, error: Exception | None = None) -> None:
        self.torrents = torrents or []
        self.error = error
        self.delete_torrent = AsyncMock()

    async def get_all_active_torrents_or_raise(self) -> list[dict]:
        if self.error is not None:
            raise self.error
        return self.torrents


async def _recovery_database(
    *,
    title: str = "Example Show S01E01",
    request_status: RequestStatus = RequestStatus.DOWNLOADING,
    staged_status: str = "approved",
    episode_count: int = 1,
) -> tuple[Any, AsyncSession, Request, StagedTorrent, DownloadHealth, datetime]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    db = session_maker()
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)
    request = Request(
        external_id=f"health-{id(db)}",
        media_type=MediaType.TV,
        title="Example Show",
        status=request_status,
    )
    season = Season(season_number=1, status=RequestStatus.DOWNLOADING)
    season.episodes = [
        Episode(episode_number=number, status=RequestStatus.DOWNLOADING)
        for number in range(1, episode_count + 1)
    ]
    request.seasons = [season]
    db.add(request)
    await db.flush()
    staged_row = StagedTorrent(
        request_id=request.id,
        torrent_path="",
        json_path="",
        original_filename=title,
        title=title,
        size=100,
        indexer="test",
        magnet_url=f"magnet:?xt=urn:btih:{'a' * 40}",
        info_hash="a" * 40,
        status=staged_status,
    )
    db.add(staged_row)
    await db.flush()
    health = DownloadHealth(
        staged_torrent_id=staged_row.id,
        request_id=request.id,
        title=title,
        info_hash="a" * 40,
        status=HEALTH_STATUS_RECOVERY_ELIGIBLE,
        first_observed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(minutes=5),
        last_progress=0,
        last_progress_at=now - timedelta(hours=25),
        progress_started_at=now - timedelta(hours=25),
        recovery_eligible_at=now - timedelta(hours=1),
    )
    db.add(health)
    await db.commit()
    return engine, db, request, staged_row, health, now


async def _close_recovery_database(engine: Any, db: AsyncSession) -> None:
    await db.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_warning_and_recovery_boundaries_are_persisted_once():
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)
    row = staged()
    health = attempt()
    service, db = service_for(
        row,
        health,
        [
            {
                "hash": row.info_hash,
                "name": row.title,
                "progress": 0,
                "state": "downloading",
                "added_on": (now - timedelta(hours=6)).timestamp(),
            }
        ],
    )
    with patch("app.siftarr.services.lifecycle.download_health_service.get_settings") as settings:
        settings.return_value.download_stall_warning_hours = 6
        settings.return_value.download_stall_recovery_hours = 24
        settings.return_value.release_failure_cooldown_hours = 24
        result = await service.observe(now)
        assert result["problems"][0]["status"] == HEALTH_STATUS_WARNING
        event_count = len(db.added)
        await service.observe(now + timedelta(minutes=1))
        assert len(db.added) == event_count
        result = await service.observe(now + timedelta(hours=18))

    assert result["problems"][0]["status"] == HEALTH_STATUS_RECOVERY_ELIGIBLE
    assert [entry.event_type for entry in db.added] == [
        "download_health_warning",
        "download_health_recovery_eligible",
    ]


@pytest.mark.asyncio
async def test_paused_time_is_not_counted_as_stall():
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)
    row = staged()
    health = attempt()
    service, _db = service_for(
        row,
        health,
        [
            {
                "hash": row.info_hash,
                "name": row.title,
                "progress": 0,
                "state": "pausedDL",
                "added_on": (now - timedelta(days=7)).timestamp(),
            }
        ],
    )
    with patch("app.siftarr.services.lifecycle.download_health_service.get_settings"):
        first = await service.observe(now)
        assert health.status == HEALTH_STATUS_SUSPENDED
        service.qbittorrent.get_all_active_torrents_or_raise.return_value = [
            {
                "hash": row.info_hash,
                "name": row.title,
                "progress": 0,
                "state": "downloading",
                "added_on": (now - timedelta(days=7)).timestamp(),
            }
        ]
        second = await service.observe(now + timedelta(hours=12))

    assert first["problems"] == []
    assert second["problems"] == []


@pytest.mark.asyncio
async def test_first_partial_observation_does_not_infer_added_on_stall_age():
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)
    row = staged()
    service, _db = service_for(
        row,
        attempt(),
        [
            {
                "hash": row.info_hash,
                "name": row.title,
                "progress": 0.4,
                "state": "downloading",
                "added_on": (now - timedelta(days=30)).timestamp(),
            }
        ],
    )
    with patch("app.siftarr.services.lifecycle.download_health_service.get_settings"):
        result = await service.observe(now)
    assert result["problems"] == []


@pytest.mark.asyncio
async def test_ambiguous_title_is_not_matched_by_name():
    row = staged(info_hash=None)
    health = attempt()
    service, _db = service_for(
        row,
        health,
        [
            {"hash": "1", "name": row.title, "progress": 0, "state": "downloading"},
            {"hash": "2", "name": row.title, "progress": 0, "state": "downloading"},
        ],
    )
    with patch("app.siftarr.services.lifecycle.download_health_service.get_settings"):
        result = await service.observe(datetime(2026, 9, 6, 12, tzinfo=UTC))
    assert health.status == HEALTH_STATUS_AMBIGUOUS
    assert result["problems"][0]["replacement_available"] is False


@pytest.mark.asyncio
async def test_outage_does_not_turn_existing_state_into_missing():
    row = staged()
    health = attempt()
    health.status = HEALTH_STATUS_WARNING
    service, _db = service_for(row, health, [])
    service.qbittorrent.get_all_active_torrents_or_raise = AsyncMock(
        side_effect=ConnectionError("qBittorrent offline")
    )
    service.list_problems = AsyncMock(return_value=[])

    result = await service.observe(datetime(2026, 9, 6, 12, tzinfo=UTC))

    assert result["qbit_available"] is False
    assert health.status == HEALTH_STATUS_WARNING
    assert health.missing_since is None


@pytest.mark.asyncio
async def test_missing_torrent_is_a_problem_but_not_completion_evidence():
    row = staged()
    health = attempt()
    service, db = service_for(row, health, [])

    result = await service.observe(datetime(2026, 9, 6, 12, tzinfo=UTC))

    assert health.status == "missing"
    assert result["problems"][0]["replacement_available"] is True
    assert any(entry.event_type == "download_health_missing" for entry in db.added)


@pytest.mark.asyncio
async def test_direct_approved_row_is_discovered_and_hash_is_stored():
    row = staged(info_hash=None)
    row.source_release_id = 99
    health = None
    db = FakeDb()
    qbit: Any = AsyncMock()
    service: Any = DownloadHealthService(cast(AsyncSession, db), cast(QbittorrentService, qbit))
    mock_service = cast(Any, service)
    mock_service._load_approved_rows = AsyncMock(return_value=[(row, None, health)])
    mock_service._historical_start_times = AsyncMock(return_value={})
    qbit.get_all_active_torrents_or_raise = AsyncMock(
        return_value=[
            {
                "hash": "B" * 40,
                "name": row.title,
                "progress": 0.25,
                "state": "downloading",
            }
        ]
    )

    await service.observe(datetime(2026, 9, 6, 12, tzinfo=UTC))

    created = next(value for value in db.added if isinstance(value, DownloadHealth))
    assert created.staged_torrent_id == row.id
    assert created.info_hash == "b" * 40


@pytest.mark.asyncio
async def test_recovery_requires_confirmation_and_does_not_call_qbit_delete():
    qbit: Any = AsyncMock()
    service: Any = DownloadHealthService(
        cast(AsyncSession, FakeDb()), cast(QbittorrentService, qbit)
    )

    with pytest.raises(DownloadRecoveryNotEligible, match="confirmation"):
        await service.recover(10, confirm=False)
    qbit.delete_torrent.assert_not_called()


def test_reset_request_scope_only_resets_covered_tv_episodes():
    episode_one = SimpleNamespace(status=RequestStatus.DOWNLOADING, episode_number=1)
    episode_two = SimpleNamespace(status=RequestStatus.DOWNLOADING, episode_number=2)
    season = SimpleNamespace(season_number=1, episodes=[episode_one, episode_two], status=None)
    request = SimpleNamespace(
        id=42,
        media_type=MediaType.TV,
        status=RequestStatus.DOWNLOADING,
        next_retry_at=datetime.now(UTC),
        rejection_reason="old",
        seasons=[season],
    )

    coverage = DownloadHealthService._reset_request_scope(cast(Any, request), "Show S01E01 1080p")

    assert coverage["episodes"] == ["S01E01"]
    assert episode_one.status == RequestStatus.PENDING
    assert episode_two.status == RequestStatus.DOWNLOADING
    assert request.next_retry_at is None
    assert request.rejection_reason is None


@pytest.mark.asyncio
async def test_recovery_reobserves_resumed_torrent_before_reset():
    engine, db, request, staged_row, health, now = await _recovery_database()
    qbit = DatabaseQbit(
        [
            {
                "hash": staged_row.info_hash,
                "name": staged_row.title,
                "progress": 0.5,
                "state": "downloading",
            }
        ]
    )
    try:
        with (
            patch(
                "app.siftarr.services.lifecycle.download_health_service.get_settings",
                return_value=SimpleNamespace(
                    download_stall_warning_hours=6,
                    download_stall_recovery_hours=24,
                    release_failure_cooldown_hours=24,
                ),
            ),
            pytest.raises(DownloadRecoveryNotEligible, match="no longer eligible"),
        ):
            await DownloadHealthService(db, cast(QbittorrentService, qbit)).recover(
                health.id, confirm=True, now=now
            )
        assert staged_row.status == "approved"
        assert request.status == RequestStatus.DOWNLOADING
        qbit.delete_torrent.assert_not_called()
    finally:
        await _close_recovery_database(engine, db)


@pytest.mark.asyncio
async def test_recovery_rejects_paused_and_unavailable_current_torrents():
    qbits = tuple(
        DatabaseQbit(
            [
                {
                    "hash": "a" * 40,
                    "name": "Example Show S01E01",
                    "progress": 0,
                    "state": state,
                }
            ]
        )
        for state in ("pausedDL", "queuedDL", "checkingDL")
    ) + (
        DatabaseQbit(
            [
                {
                    "hash": "a" * 40,
                    "name": "Example Show S01E01",
                    "progress": 1,
                    "state": "uploading",
                }
            ]
        ),
        DatabaseQbit(error=ConnectionError("offline")),
    )
    for qbit in qbits:
        engine, db, _request, staged_row, health, now = await _recovery_database()
        try:
            with (
                patch(
                    "app.siftarr.services.lifecycle.download_health_service.get_settings",
                    return_value=SimpleNamespace(
                        download_stall_warning_hours=6,
                        download_stall_recovery_hours=24,
                        release_failure_cooldown_hours=24,
                    ),
                ),
                pytest.raises(DownloadRecoveryNotEligible),
            ):
                await DownloadHealthService(db, cast(QbittorrentService, qbit)).recover(
                    health.id, confirm=True, now=now
                )
            assert staged_row.status == "approved"
            qbit.delete_torrent.assert_not_called()
        finally:
            await _close_recovery_database(engine, db)


@pytest.mark.asyncio
async def test_recovery_rejects_terminal_request_and_nonapproved_stage():
    for request_status, staged_status in (
        (RequestStatus.COMPLETED, "approved"),
        (RequestStatus.DOWNLOADING, "discarded"),
    ):
        engine, db, _request, staged_row, health, now = await _recovery_database(
            request_status=request_status,
            staged_status=staged_status,
        )
        qbit = DatabaseQbit([])
        try:
            with pytest.raises(DownloadRecoveryNotEligible):
                await DownloadHealthService(db, cast(QbittorrentService, qbit)).recover(
                    health.id, confirm=True, now=now
                )
            assert staged_row.status == staged_status
            qbit.delete_torrent.assert_not_called()
        finally:
            await _close_recovery_database(engine, db)


@pytest.mark.asyncio
async def test_recovery_resets_all_covered_multi_episode_episodes_only():
    engine, db, _request, staged_row, health, now = await _recovery_database(
        title="Example Show S01E01-E03",
        episode_count=4,
    )
    qbit = DatabaseQbit([])
    try:
        with patch(
            "app.siftarr.services.lifecycle.download_health_service.get_settings",
            return_value=SimpleNamespace(
                download_stall_warning_hours=6,
                download_stall_recovery_hours=24,
                release_failure_cooldown_hours=24,
            ),
        ):
            result = await DownloadHealthService(db, cast(QbittorrentService, qbit)).recover(
                health.id, confirm=True, disposition="cooldown", now=now
            )
        refreshed = (await db.execute(select(Episode).order_by(Episode.episode_number))).scalars()
        statuses = [episode.status for episode in refreshed]
        assert statuses == [
            RequestStatus.PENDING,
            RequestStatus.PENDING,
            RequestStatus.PENDING,
            RequestStatus.DOWNLOADING,
        ]
        affected = cast(dict[str, Any], result["affected_coverage"])
        assert affected["episodes"] == ["S01E01", "S01E02", "S01E03"]
        assert staged_row.status == "replaced"
        qbit.delete_torrent.assert_not_called()
    finally:
        await _close_recovery_database(engine, db)
