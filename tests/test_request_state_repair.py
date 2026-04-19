"""Tests for the one-off request-state repair command."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models._base import Base
from app.siftarr.models.episode import Episode
from app.siftarr.models.release import Release
from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.models.season import Season
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.repair_request_state import format_summary, repair_request_state
from app.siftarr.routers import dashboard
from app.siftarr.services import release_selection_service
from app.siftarr.services.download_completion_service import DownloadCompletionService
from app.siftarr.services.episode_sync_service import EpisodeSyncService
from app.siftarr.services.plex_polling_service import PollDecision


class _FakeOverseerrService:
    def __init__(
        self,
        _settings,
        details_by_tmdb: Mapping[int, Mapping[str, object] | None] | None = None,
    ) -> None:
        self._details_by_tmdb = details_by_tmdb or {}

    async def get_media_details(
        self, media_type: str, external_id: int
    ) -> Mapping[str, object] | None:
        if media_type != "tv":
            return None
        return self._details_by_tmdb.get(external_id)

    async def close(self) -> None:
        return None


async def _create_database(db_path: Path) -> str:
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return database_url


async def _seed_repair_fixture(database_url: str) -> None:
    engine = create_async_engine(database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as session:
        ongoing_finished = Request(
            external_id="tv-finished",
            media_type=MediaType.TV,
            tmdb_id=101,
            title="Ongoing Finished",
            status=RequestStatus.COMPLETED,
        )
        finished_season = Season(
            request=ongoing_finished,
            season_number=1,
            status=RequestStatus.COMPLETED,
            synced_at=datetime(2026, 4, 17, tzinfo=UTC),
        )
        finished_season.episodes = [
            Episode(
                episode_number=1,
                title="Ep 1",
                air_date=date(2026, 4, 1),
                status=RequestStatus.AVAILABLE,
            ),
            Episode(
                episode_number=2,
                title="Ep 2",
                air_date=date(2026, 4, 8),
                status=RequestStatus.AVAILABLE,
            ),
        ]

        stale_downloading = Request(
            external_id="tv-stale-downloading",
            media_type=MediaType.TV,
            tmdb_id=202,
            title="Stale Downloading",
            status=RequestStatus.DOWNLOADING,
        )
        stale_season = Season(
            request=stale_downloading,
            season_number=1,
            status=RequestStatus.COMPLETED,
            synced_at=datetime(2026, 4, 17, tzinfo=UTC),
        )
        stale_season.episodes = [
            Episode(
                episode_number=1,
                title="Ep 1",
                air_date=date(2026, 4, 1),
                status=RequestStatus.AVAILABLE,
            )
        ]

        duplicate_request = Request(
            external_id="tv-duplicate",
            media_type=MediaType.TV,
            tmdb_id=303,
            title="Duplicate Stage",
            status=RequestStatus.STAGED,
        )
        duplicate_season = Season(
            request=duplicate_request,
            season_number=1,
            status=RequestStatus.PENDING,
            synced_at=datetime(2026, 4, 17, tzinfo=UTC),
        )
        duplicate_season.episodes = [
            Episode(
                episode_number=1,
                title="Ep 1",
                air_date=date(2026, 4, 20),
                status=RequestStatus.PENDING,
            )
        ]

        stale_movie = Request(
            external_id="movie-stale",
            media_type=MediaType.MOVIE,
            title="Stale Movie",
            status=RequestStatus.DOWNLOADING,
        )

        session.add_all([ongoing_finished, stale_downloading, duplicate_request, stale_movie])
        await session.flush()

        session.add_all(
            [
                StagedTorrent(
                    request_id=stale_downloading.id,
                    torrent_path="/tmp/stale-approved.torrent",
                    json_path="/tmp/stale-approved.json",
                    original_filename="stale-approved",
                    title="Stale Approved",
                    size=1,
                    indexer="IndexerA",
                    score=90,
                    status="approved",
                    selection_source="rule",
                ),
                StagedTorrent(
                    request_id=duplicate_request.id,
                    torrent_path="/tmp/duplicate-old.torrent",
                    json_path="/tmp/duplicate-old.json",
                    original_filename="duplicate-old",
                    title="Auto Pick",
                    size=1,
                    indexer="IndexerA",
                    score=50,
                    status="staged",
                    selection_source="rule",
                    created_at=datetime(2026, 4, 17, 10, 0, tzinfo=UTC),
                ),
                StagedTorrent(
                    request_id=duplicate_request.id,
                    torrent_path="/tmp/duplicate-new.torrent",
                    json_path="/tmp/duplicate-new.json",
                    original_filename="duplicate-new",
                    title="Manual Pick",
                    size=1,
                    indexer="IndexerB",
                    score=70,
                    status="staged",
                    selection_source="manual",
                    created_at=datetime(2026, 4, 17, 11, 0, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_request_state_dry_run_summarizes_without_mutating(tmp_path):
    """Dry-run should report the intended fixes and leave the database unchanged."""
    database_url = await _create_database(tmp_path / "repair-dry-run.db")
    await _seed_repair_fixture(database_url)

    details_by_tmdb = {
        101: {
            "firstAirDate": "2025-01-01",
            "status": "Returning Series",
            "nextEpisodeToAir": {"airDate": "2026-05-01"},
        },
        202: {
            "firstAirDate": "2025-01-01",
            "status": "Ended",
        },
        303: {
            "firstAirDate": "2026-04-20",
            "status": "Returning Series",
        },
    }

    summary = await repair_request_state(
        apply=False,
        database_url=database_url,
        overseerr_factory=lambda settings: _FakeOverseerrService(settings, details_by_tmdb),
    )

    assert summary.request_status_updates == 3
    assert summary.season_status_updates == 2
    assert summary.aggregate_request_repairs == 1
    assert summary.stale_workflow_request_repairs == 2
    assert summary.unreleased_request_repairs == 1
    assert summary.staged_torrents_retired == 2
    assert summary.duplicate_torrents_retired == 1
    assert summary.stale_active_torrents_retired == 1

    rendered = format_summary(summary)
    assert "Request-state repair (dry-run)" in rendered
    assert "ongoing TV reclassified to unreleased: 1" in rendered
    assert (
        "request 1 (Ongoing Finished): completed -> unreleased [reclassified to unreleased]"
        in rendered
    )

    engine = create_async_engine(database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        requests = {
            request.external_id: request
            for request in (await session.execute(select(Request))).scalars().all()
        }
        assert requests["tv-finished"].status == RequestStatus.COMPLETED
        assert requests["tv-stale-downloading"].status == RequestStatus.DOWNLOADING
        assert requests["movie-stale"].status == RequestStatus.DOWNLOADING

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_request_state_apply_updates_requests_and_torrents(tmp_path):
    """Apply mode should persist request repairs and retire bad active staged torrents."""
    database_url = await _create_database(tmp_path / "repair-apply.db")
    await _seed_repair_fixture(database_url)

    details_by_tmdb = {
        101: {
            "firstAirDate": "2025-01-01",
            "status": "Returning Series",
            "nextEpisodeToAir": {"airDate": "2026-05-01"},
        },
        202: {
            "firstAirDate": "2025-01-01",
            "status": "Ended",
        },
        303: {
            "firstAirDate": "2026-04-20",
            "status": "Returning Series",
        },
    }

    summary = await repair_request_state(
        apply=True,
        database_url=database_url,
        overseerr_factory=lambda settings: _FakeOverseerrService(settings, details_by_tmdb),
    )

    assert summary.request_status_updates == 3
    assert summary.staged_torrents_retired == 2

    engine = create_async_engine(database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        requests = {
            request.external_id: request
            for request in (await session.execute(select(Request))).scalars().all()
        }
        assert requests["tv-finished"].status == RequestStatus.UNRELEASED
        assert requests["tv-stale-downloading"].status == RequestStatus.AVAILABLE
        assert requests["movie-stale"].status == RequestStatus.PENDING

        staged_torrents = {
            torrent.title: torrent
            for torrent in (await session.execute(select(StagedTorrent))).scalars().all()
        }
        assert staged_torrents["Stale Approved"].status == "discarded"
        assert staged_torrents["Auto Pick"].status == "replaced"
        assert staged_torrents["Auto Pick"].replaced_by_id == staged_torrents["Manual Pick"].id
        assert staged_torrents["Auto Pick"].replacement_reason == (
            "Retired duplicate active staged torrent during request-state repair"
        )
        assert staged_torrents["Manual Pick"].status == "staged"

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_request_state_combined_lifecycle_repairs_and_summaries(tmp_path):
    """Repair should summarize the cross-flow problem areas from the combined lifecycle."""
    database_url = await _create_database(tmp_path / "repair-lifecycle.db")
    await _seed_repair_fixture(database_url)

    details_by_tmdb = {
        101: {
            "firstAirDate": "2025-01-01",
            "status": "Returning Series",
            "nextEpisodeToAir": {"airDate": "2026-05-01"},
        },
        202: {
            "firstAirDate": "2025-01-01",
            "status": "Ended",
        },
        303: {
            "firstAirDate": "2026-04-20",
            "status": "Returning Series",
        },
    }

    summary = await repair_request_state(
        apply=True,
        database_url=database_url,
        overseerr_factory=lambda settings: _FakeOverseerrService(settings, details_by_tmdb),
    )

    rendered = format_summary(summary)
    assert (
        "request 1 (Ongoing Finished): completed -> unreleased [reclassified to unreleased]"
        in rendered
    )
    assert (
        "request 2 (Stale Downloading): downloading -> available "
        "[repaired stale staged/downloading workflow]" in rendered
    )
    assert (
        "staged torrent 2 for request 3: repaired duplicate active selection, replaced by 3"
        in rendered
    )


@pytest.mark.asyncio
async def test_combined_lifecycle_auto_stage_replace_complete_and_repair_to_unreleased(
    tmp_path, monkeypatch
):
    """Combined lifecycle should hide staged rows after Plex reconcile and repair finished ongoing TV back to unreleased."""
    database_url = await _create_database(tmp_path / "combined-lifecycle.db")
    engine = create_async_engine(database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    class _PersistingStagingService:
        def __init__(self, db):
            self.db = db

        async def save_release(self, release, request, score=0, selection_source="rule"):
            slug = release.title.replace("/", "_")
            staged = StagedTorrent(
                request_id=request.id,
                torrent_path=str(tmp_path / f"{slug}.torrent"),
                json_path=str(tmp_path / f"{slug}.json"),
                original_filename=slug,
                title=release.title,
                size=release.size,
                indexer=release.indexer,
                score=score,
                magnet_url=release.magnet_url,
                selection_source=selection_source,
                status="staged",
            )
            self.db.add(staged)
            await self.db.commit()
            await self.db.refresh(staged)
            return staged

    async with session_maker() as session:
        request = Request(
            external_id="tv-combined",
            media_type=MediaType.TV,
            tmdb_id=404,
            title="Combined Lifecycle",
            status=RequestStatus.PENDING,
        )
        season = Season(
            request=request,
            season_number=1,
            status=RequestStatus.PENDING,
            synced_at=datetime(2026, 4, 17, tzinfo=UTC),
        )
        season.episodes = [
            Episode(
                episode_number=1,
                title="Ep 1",
                air_date=date(2026, 4, 1),
                status=RequestStatus.PENDING,
            ),
            Episode(
                episode_number=2,
                title="Ep 2",
                air_date=date(2026, 5, 1),
                status=RequestStatus.UNRELEASED,
            ),
        ]
        auto_release = Release(
            request=request,
            title="Combined.Lifecycle.S01.1080p.WEB-DL",
            size=10,
            seeders=20,
            leechers=1,
            download_url="https://example.test/auto.torrent",
            magnet_url="magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709",
            indexer="IndexerA",
            score=80,
            passed_rules=True,
        )
        manual_release = Release(
            request=request,
            title="Combined.Lifecycle.S01.REPACK.2160p.WEB-DL",
            size=20,
            seeders=30,
            leechers=1,
            download_url="https://example.test/manual.torrent",
            magnet_url="magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709",
            indexer="IndexerB",
            score=95,
            passed_rules=True,
        )
        session.add_all([request, season, auto_release, manual_release])
        await session.commit()
        await session.refresh(request)
        await session.refresh(auto_release)
        await session.refresh(manual_release)

        queue_service = AsyncMock()
        monkeypatch.setattr(
            release_selection_service,
            "get_effective_settings",
            AsyncMock(return_value=MagicMock(staging_mode_enabled=True)),
        )
        monkeypatch.setattr(
            release_selection_service,
            "PendingQueueService",
            MagicMock(return_value=queue_service),
        )
        monkeypatch.setattr(
            release_selection_service,
            "StagingService",
            _PersistingStagingService,
        )

        auto_result = await release_selection_service.use_releases(
            session,
            request,
            [auto_release],
            selection_source="rule",
        )
        manual_result = await release_selection_service.use_releases(
            session,
            request,
            [manual_release],
            selection_source="manual",
        )

        staged_torrents = (
            (await session.execute(select(StagedTorrent).order_by(StagedTorrent.id.asc())))
            .scalars()
            .all()
        )
        auto_stage, manual_stage = staged_torrents
        assert auto_result["action"] == "auto_staged"
        assert manual_result["action"] == "replaced_active_selection"
        assert auto_stage.status == "replaced"
        assert manual_stage.status == "staged"

        manual_stage.status = "approved"
        request.status = RequestStatus.DOWNLOADING
        await session.commit()

        class _FakePlexPolling:
            def __init__(self, db):
                self.db = db

            async def _check_tv(self, full_request):
                return PollDecision(
                    request_id=full_request.id,
                    reason="All released episodes found on Plex",
                    requested_episode_count=2,
                    completed_episodes=frozenset({(1, 1)}),
                    episode_availability={(1, 1): True, (1, 2): False},
                )

            async def _apply_decision(self, full_request, decision):
                await EpisodeSyncService(self.db).reconcile_existing_seasons_from_plex(
                    full_request,
                    full_request.seasons,
                    decision.episode_availability,
                )

        qbit = AsyncMock()
        qbit.get_torrent_info.return_value = None
        completed = await DownloadCompletionService(
            session,
            qbit,
            _FakePlexPolling(session),
        ).check_downloading_requests()
        await session.refresh(request)

        assert completed == 1
        assert request.status == RequestStatus.PARTIALLY_AVAILABLE

        fake_overseerr = AsyncMock()
        fake_overseerr.get_media_details.return_value = {
            "nextEpisodeToAir": {"airDate": "2026-05-01"}
        }
        fake_overseerr.close.return_value = None
        monkeypatch.setattr(
            dashboard,
            "get_effective_settings",
            AsyncMock(
                return_value=MagicMock(
                    overseerr_url="http://overseerr.test",
                    staging_mode_enabled=True,
                    qbittorrent_url="http://qbit.test",
                )
            ),
        )
        monkeypatch.setattr(
            dashboard,
            "PendingQueueService",
            lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
        )
        monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: fake_overseerr)

        response = await dashboard.dashboard(MagicMock(), db=session)
        context = response.context
        assert context["staged_torrents"] == []
        assert any(req.id == request.id for req in context["unreleased_requests"])

        request.status = RequestStatus.COMPLETED
        await session.commit()

    summary = await repair_request_state(
        apply=True,
        database_url=database_url,
        overseerr_factory=lambda settings: _FakeOverseerrService(
            settings,
            {
                404: {
                    "firstAirDate": "2025-01-01",
                    "status": "Returning Series",
                    "nextEpisodeToAir": {"airDate": "2026-05-01"},
                }
            },
        ),
    )

    async with session_maker() as session:
        repaired_request = await session.scalar(
            select(Request).where(Request.external_id == "tv-combined")
        )
        assert repaired_request is not None
        assert repaired_request.status == RequestStatus.UNRELEASED

        fake_overseerr = AsyncMock()
        fake_overseerr.get_media_details.return_value = {
            "nextEpisodeToAir": {"airDate": "2026-05-01"}
        }
        fake_overseerr.close.return_value = None
        monkeypatch.setattr(
            dashboard,
            "get_effective_settings",
            AsyncMock(
                return_value=MagicMock(
                    overseerr_url="http://overseerr.test",
                    staging_mode_enabled=True,
                    qbittorrent_url="http://qbit.test",
                )
            ),
        )
        monkeypatch.setattr(
            dashboard,
            "PendingQueueService",
            lambda db: AsyncMock(get_all_pending=AsyncMock(return_value=[])),
        )
        monkeypatch.setattr(dashboard, "OverseerrService", lambda settings: fake_overseerr)

        response = await dashboard.dashboard(MagicMock(), db=session)
        context = response.context
        assert context["staged_torrents"] == []
        assert any(req.id == repaired_request.id for req in context["unreleased_requests"])
        assert all(req.id != repaired_request.id for req in context["completed_requests"])
        assert any(
            note
            == "request 1 (Combined Lifecycle): completed -> unreleased [reclassified to unreleased]"
            for note in summary.notes
        )

    await engine.dispose()
