"""Tests for release selection helpers."""

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
from app.siftarr.services import release_selection_service
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.rule_engine import ReleaseEvaluation


class TestReleaseSelectionService:
    """Focused tests for staging-mode release selection."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def request_record(self):
        """Create a mock request record."""
        request = MagicMock(spec=Request)
        request.id = 7
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.PENDING
        return request

    @pytest.fixture
    def selected_release(self):
        """Create a mock user-selected release."""
        release = MagicMock()
        release.id = 100
        release.title = "User Pick"
        release.score = 50
        release.size = 1_500_000_000
        release.seeders = 25
        release.leechers = 3
        release.indexer = "Indexer A"
        release.magnet_url = "magnet:?xt=urn:btih:userpick"
        release.download_url = "https://example.com/user-pick.torrent"
        release.info_hash = None
        release.publish_date = None
        release.resolution = None
        release.codec = None
        release.release_group = None
        return release

    @pytest.mark.asyncio
    async def test_use_releases_marks_manual_selection_source(
        self,
        mock_db,
        request_record,
        selected_release,
    ):
        """Manual release picks should stage as manual selections."""
        settings = MagicMock(staging_mode_enabled=True)
        queue_service = AsyncMock()
        staging_service = AsyncMock()
        staged_record = MagicMock(id=33)
        staging_service.save_release.return_value = staged_record

        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = existing_result

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "StagingService",
                MagicMock(return_value=staging_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="manual",
            )

        assert result["status"] == "staged"
        assert result["action"] == "manual_staged"
        assert result["message"] == "Manually staged 1 release(s) for approval."
        staging_service.save_release.assert_awaited_once()
        assert staging_service.save_release.await_args.kwargs["selection_source"] == "manual"
        queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)

    @pytest.mark.asyncio
    async def test_use_releases_keeps_existing_staged_release(
        self, mock_db, request_record, selected_release
    ):
        """Already staged releases should be reused instead of staged again."""
        settings = MagicMock(staging_mode_enabled=True)
        queue_service = AsyncMock()
        staging_service = AsyncMock()

        existing_stage = MagicMock()
        existing_stage.id = 44
        existing_stage.title = selected_release.title
        existing_result = MagicMock()
        existing_result.scalars.return_value.all.return_value = [existing_stage]
        mock_db.execute.return_value = existing_result

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "StagingService",
                MagicMock(return_value=staging_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="rule",
            )

        assert result["status"] == "staged"
        assert result["action"] == "auto_staged"
        assert result["message"] == "Auto-staged 1 release(s) for approval."
        assert result["staged_ids"] == [existing_stage.id]
        staging_service.save_release.assert_not_awaited()
        queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)

    @pytest.mark.asyncio
    async def test_use_releases_replaces_existing_active_stage_for_manual_selection(
        self, mock_db, request_record, selected_release
    ):
        """Manual selections should retire the current active staged torrent instead of duplicating it."""
        settings = MagicMock(staging_mode_enabled=True)
        queue_service = AsyncMock()
        staging_service = AsyncMock()

        existing_stage = StagedTorrent(
            id=44,
            request_id=request_record.id,
            torrent_path="/tmp/existing.torrent",
            json_path="/tmp/existing.json",
            original_filename="existing",
            title="Auto Pick",
            size=1,
            indexer="Indexer A",
            score=100,
            status="staged",
            selection_source="rule",
        )
        replacement_stage = StagedTorrent(
            id=55,
            request_id=request_record.id,
            torrent_path="/tmp/replacement.torrent",
            json_path="/tmp/replacement.json",
            original_filename="replacement",
            title=selected_release.title,
            size=selected_release.size,
            indexer=selected_release.indexer,
            score=selected_release.score,
            status="staged",
            selection_source="manual",
        )
        staging_service.save_release.return_value = replacement_stage

        active_result = MagicMock()
        active_result.scalars.return_value.all.return_value = [existing_stage]
        mock_db.execute.return_value = active_result

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "StagingService",
                MagicMock(return_value=staging_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="manual",
            )

        assert result["status"] == "staged"
        assert result["action"] == "replaced_active_selection"
        assert result["message"] == "Replaced the active staged selection with 1 release(s)."
        assert result["staged_ids"] == [replacement_stage.id]
        assert existing_stage.status == "replaced"
        assert existing_stage.replaced_by_id == replacement_stage.id
        assert existing_stage.replaced_at is not None
        assert existing_stage.replacement_reason == (
            "Manually replaced staged selection from request details"
        )
        queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)

    @pytest.mark.asyncio
    async def test_use_releases_reuses_existing_manual_pick_and_retires_auto_pick(
        self, mock_db, request_record, selected_release
    ):
        """Selecting an already-staged manual pick should still replace the active auto pick."""
        settings = MagicMock(staging_mode_enabled=True)
        queue_service = AsyncMock()
        staging_service = AsyncMock()

        auto_stage = StagedTorrent(
            id=44,
            request_id=request_record.id,
            torrent_path="/tmp/auto.torrent",
            json_path="/tmp/auto.json",
            original_filename="auto",
            title="Auto Pick",
            size=1,
            indexer="Indexer A",
            score=100,
            status="staged",
            selection_source="rule",
        )
        manual_stage = StagedTorrent(
            id=55,
            request_id=request_record.id,
            torrent_path="/tmp/manual.torrent",
            json_path="/tmp/manual.json",
            original_filename="manual",
            title=selected_release.title,
            size=selected_release.size,
            indexer=selected_release.indexer,
            score=selected_release.score,
            status="staged",
            selection_source="manual",
        )

        active_result = MagicMock()
        active_result.scalars.return_value.all.return_value = [auto_stage, manual_stage]
        mock_db.execute.return_value = active_result

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "StagingService",
                MagicMock(return_value=staging_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="manual",
            )

        assert result["status"] == "staged"
        assert result["action"] == "replaced_active_selection"
        assert result["message"] == "Replaced the active staged selection with 1 release(s)."
        assert result["staged_ids"] == [manual_stage.id]
        assert auto_stage.status == "replaced"
        assert auto_stage.replaced_by_id == manual_stage.id
        assert manual_stage.status == "staged"
        staging_service.save_release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persist_manual_release_upserts_existing_release(self, mock_db, request_record):
        """Manual picks should upsert a stored release for reuse by normal selection flow."""
        existing_release = Release(
            id=1,
            request_id=request_record.id,
            title="User Pick",
            size=1,
            seeders=1,
            leechers=0,
            download_url="https://old.example/release.torrent",
            indexer="OldIndexer",
            score=1,
            passed_rules=False,
        )
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = existing_release
        mock_db.execute.return_value = query_result
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        release = ProwlarrRelease(
            title="User Pick",
            size=10,
            seeders=25,
            leechers=3,
            download_url="https://example.com/user-pick.torrent",
            magnet_url="magnet:?xt=urn:btih:userpick",
            info_hash="abc123",
            indexer="Indexer A",
        )
        evaluation = ReleaseEvaluation(release=release, passed=True, total_score=50, matches=[])

        stored = await release_selection_service.persist_manual_release(
            mock_db,
            request_record,
            release,
            evaluation,
        )

        assert stored is existing_release
        assert existing_release.download_url == release.download_url
        assert existing_release.magnet_url == release.magnet_url
        assert existing_release.info_hash == release.info_hash
        assert existing_release.score == 50
        assert existing_release.passed_rules is True
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing_release)

    @pytest.mark.asyncio
    async def test_use_releases_skips_duplicate_direct_send(
        self, mock_db, request_record, selected_release
    ):
        """Direct-send path should not re-send releases already marked as downloaded."""
        settings = MagicMock(staging_mode_enabled=False)
        queue_service = AsyncMock()
        qbittorrent_service = AsyncMock()
        selected_release.is_downloaded = True
        mock_db.commit = AsyncMock()

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                release_selection_service,
                "get_effective_settings",
                AsyncMock(return_value=settings),
            )
            monkeypatch.setattr(
                release_selection_service,
                "PendingQueueService",
                MagicMock(return_value=queue_service),
            )
            monkeypatch.setattr(
                release_selection_service,
                "QbittorrentService",
                MagicMock(return_value=qbittorrent_service),
            )

            result = await release_selection_service.use_releases(
                mock_db,
                request_record,
                [selected_release],
                selection_source="manual",
            )

        assert result["status"] == "downloading"
        assert result["already_sent_titles"] == [selected_release.title]
        qbittorrent_service.add_torrent.assert_not_awaited()
        queue_service.remove_from_queue.assert_awaited_once_with(request_record.id)

    @pytest.mark.asyncio
    async def test_persist_manual_release_requires_download_source(self, mock_db, request_record):
        """Manual picks without a download source should be rejected early."""
        release = ProwlarrRelease(
            title="User Pick",
            size=10,
            seeders=1,
            leechers=0,
            download_url="",
            magnet_url=None,
            indexer="Indexer A",
        )
        evaluation = ReleaseEvaluation(release=release, passed=True, total_score=10, matches=[])

        with pytest.raises(RuntimeError, match="no usable download source"):
            await release_selection_service.persist_manual_release(
                mock_db,
                request_record,
                release,
                evaluation,
            )

    @pytest.mark.asyncio
    async def test_store_search_results_persists_multi_season_coverage(self, mock_db):
        """Multi-season packs should persist exact covered seasons."""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        release = ProwlarrRelease(
            title="Show.S01-S03.2160p.WEB-DL",
            size=30 * 1024 * 1024 * 1024,
            seeders=50,
            leechers=4,
            download_url="https://example.test/show-s01-s03.torrent",
            indexer="IndexerA",
        )
        evaluation = ReleaseEvaluation(release=release, passed=True, total_score=95, matches=[])

        await release_selection_service.store_search_results(mock_db, 12, [evaluation])

        stored_record = mock_db.add.call_args.args[0]
        assert stored_record.request_id == 12
        assert stored_record.season_number == 1
        assert stored_record.episode_number is None
        assert stored_record.season_coverage == "1,2,3"
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(stored_record)

    @pytest.mark.asyncio
    async def test_store_search_results_persists_complete_series_marker(self, mock_db):
        """Complete-series releases should persist a reusable broad coverage marker."""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        release = ProwlarrRelease(
            title="Show.Complete.Series.1080p.BluRay",
            size=42 * 1024 * 1024 * 1024,
            seeders=77,
            leechers=2,
            download_url="https://example.test/show-complete-series.torrent",
            indexer="IndexerB",
        )
        evaluation = ReleaseEvaluation(release=release, passed=True, total_score=88, matches=[])

        await release_selection_service.store_search_results(mock_db, 33, [evaluation])

        stored_record = mock_db.add.call_args.args[0]
        assert stored_record.request_id == 33
        assert stored_record.season_number is None
        assert stored_record.episode_number is None
        assert stored_record.season_coverage == "*"

    @pytest.mark.asyncio
    async def test_clear_release_search_cache_deletes_releases_and_detaches_episode_links(self):
        """Cache clearing should remove stored releases without leaving stale episode links."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_maker = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_maker() as session:
            request = Request(
                external_id="tv-1",
                media_type=MediaType.TV,
                title="Foundation",
                status=RequestStatus.PENDING,
            )
            season = Season(season_number=1, status=RequestStatus.PENDING, request=request)
            kept_episode = Episode(season=season, episode_number=1, status=RequestStatus.PENDING)
            detached_episode = Episode(
                season=season, episode_number=2, status=RequestStatus.PENDING
            )
            release = Release(
                request=request,
                title="Foundation.S01.1080p.WEB-DL",
                size=1,
                seeders=10,
                leechers=1,
                download_url="https://example.test/release.torrent",
                indexer="IndexerA",
                score=10,
                passed_rules=True,
            )

            session.add_all([request, season, kept_episode, detached_episode, release])
            await session.flush()
            detached_episode.release_id = release.id
            await session.commit()

            result = await release_selection_service.clear_release_search_cache(session)

            assert result == {"deleted_releases": 1, "detached_episode_refs": 1}

            remaining_releases = (await session.execute(select(Release))).scalars().all()
            assert remaining_releases == []

            refreshed_detached_episode = await session.get(Episode, detached_episode.id)
            refreshed_kept_episode = await session.get(Episode, kept_episode.id)
            assert refreshed_detached_episode is not None
            assert refreshed_detached_episode.release_id is None
            assert refreshed_kept_episode is not None
            assert refreshed_kept_episode.release_id is None

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_store_search_results_replaces_request_releases_without_stale_episode_links(self):
        """Re-searching a request should detach old episode links before replacing releases."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_maker = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_maker() as session:
            request = Request(
                external_id="tv-2",
                media_type=MediaType.TV,
                title="Severance",
                status=RequestStatus.PENDING,
            )
            season = Season(season_number=1, status=RequestStatus.PENDING, request=request)
            episode = Episode(season=season, episode_number=1, status=RequestStatus.PENDING)
            old_release = Release(
                request=request,
                title="Severance.S01E01.720p.WEB-DL",
                size=1,
                seeders=8,
                leechers=1,
                download_url="https://example.test/old-release.torrent",
                indexer="IndexerOld",
                score=5,
                passed_rules=True,
            )

            session.add_all([request, season, episode, old_release])
            await session.flush()
            episode.release_id = old_release.id
            await session.commit()

            new_release = ProwlarrRelease(
                title="Severance.S01E01.1080p.WEB-DL",
                size=2,
                seeders=20,
                leechers=1,
                download_url="https://example.test/new-release.torrent",
                indexer="IndexerNew",
            )
            evaluation = ReleaseEvaluation(
                release=new_release,
                passed=True,
                total_score=15,
                matches=[],
            )

            stored_records = await release_selection_service.store_search_results(
                session,
                request.id,
                [evaluation],
            )

            assert list(stored_records) == ["Severance.S01E01.1080p.WEB-DL"]

            refreshed_episode = await session.get(Episode, episode.id)
            assert refreshed_episode is not None
            assert refreshed_episode.release_id is None

            request_releases = (
                (await session.execute(select(Release).where(Release.request_id == request.id)))
                .scalars()
                .all()
            )
            assert len(request_releases) == 1
            assert request_releases[0].title == "Severance.S01E01.1080p.WEB-DL"

        await engine.dispose()
