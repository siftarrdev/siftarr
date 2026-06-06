"""Tests for TVDecisionService."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.decisions.rule_engine import ReleaseEvaluation
from app.siftarr.services.decisions.tv_decision_service import TVDecisionService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult


def _make_release(title="Test.S01E05.1080p", size=1000000000, seeders=10, info_hash=None):
    return ProwlarrRelease(
        title=title,
        size=size,
        seeders=seeders,
        leechers=1,
        download_url="http://example.com/test",
        magnet_url=None,
        info_hash=info_hash,
        indexer="test",
    )


def _make_request(**overrides):
    request = MagicMock(spec=Request)
    request.id = overrides.get("id", 1)
    request.media_type = overrides.get("media_type", MediaType.TV)
    request.tvdb_id = overrides.get("tvdb_id", 12345)
    request.tmdb_id = overrides.get("tmdb_id")
    request.title = overrides.get("title", "Test Show")
    request.year = overrides.get("year", 2024)
    request.status = overrides.get("status", RequestStatus.PENDING)

    seasons_data = overrides.get("seasons", [1])
    episodes_data = overrides.get("episodes", {})

    seasons = []
    for season_num in seasons_data:
        season = MagicMock()
        season.season_number = season_num
        eps = []
        for ep_num in episodes_data.get(season_num, []):
            ep = MagicMock()
            ep.episode_number = ep_num
            eps.append(ep)
        season.episodes = eps
        seasons.append(season)
    request.seasons = seasons
    return request


def _passing_eval(release, score=50):
    return ReleaseEvaluation(
        release=release,
        passed=True,
        total_score=score,
        matches=[],
        rejection_reason=None,
    )


def _failing_eval(release, rejection_reason="Excluded"):
    return ReleaseEvaluation(
        release=release,
        passed=False,
        total_score=-100,
        matches=[],
        rejection_reason=rejection_reason,
    )


def _mock_staging(return_value: dict | None = None):
    """Create a mock StagingService instance with a use_releases AsyncMock."""
    instance = AsyncMock()
    instance.use_releases = AsyncMock(
        return_value=return_value or {"status": "downloading", "message": "ok"}
    )
    return instance


class TestProcessRequest:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        return db

    @pytest.fixture
    def service(self, mock_db):
        prowlarr = AsyncMock()
        prowlarr.search_tv_episode_exact = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=0)
        )
        qbittorrent = AsyncMock()
        return TVDecisionService(mock_db, prowlarr, qbittorrent)

    @pytest.mark.asyncio
    async def test_no_tvdb_id_returns_error(self, service, mock_db):
        request = _make_request(tvdb_id=None)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        result = await service.process_request(1)

        assert result["status"] == "error"
        assert "TVDB" in result["message"]
        request.status = RequestStatus.FAILED

    @pytest.mark.asyncio
    async def test_no_seasons_specified_returns_error(self, service, mock_db):
        request = _make_request(seasons=[])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        with patch.object(service, "_get_rule_engine", new_callable=AsyncMock) as mock_rule:
            mock_rule.return_value = MagicMock()
            result = await service.process_request(1)

        assert result["status"] == "error"
        assert "No seasons" in result["message"]

    @pytest.mark.asyncio
    async def test_season_sweep_uses_overseerr_imdb_id(self, service):
        request = _make_request(tmdb_id=987, seasons=[1])
        release = _make_release("Test.Show.S01.1080p")
        engine = MagicMock(evaluate=MagicMock(return_value=_passing_eval(release)))
        service.prowlarr.search_tv_season_sweep.return_value = ProwlarrSearchResult(
            releases=[release], query_time_ms=1
        )

        with patch(
            "app.siftarr.services.decisions.tv_decision_service.OverseerrService"
        ) as overseerr_cls:
            overseerr_cls.return_value.get_media_details = AsyncMock(
                return_value={"externalIds": {"imdbId": "tt1234567"}}
            )
            await service._search_season_sweeps_and_evaluate(request, engine, [1])

        service.prowlarr.search_tv_season_sweep.assert_awaited_once_with(
            title="Test Show",
            season=1,
            imdbid="tt1234567",
            tvdbid=12345,
            request_id=1,
        )

    @pytest.mark.asyncio
    async def test_request_not_found_returns_error(self, service, mock_db):
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))

        result = await service.process_request(999)

        assert result["status"] == "error"
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_non_tv_request_returns_error(self, service, mock_db):
        request = _make_request(media_type=MediaType.MOVIE)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))

        result = await service.process_request(1)

        assert result["status"] == "error"
        assert "not TV" in result["message"]

    @pytest.mark.asyncio
    async def test_default_search_checks_pack_then_exact_actionable_episodes(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1], episodes={1: [1, 2]})
        today = date.today()
        request.seasons[0].episodes[0].status = RequestStatus.COMPLETED
        request.seasons[0].episodes[0].air_date = today - timedelta(days=1)
        request.seasons[0].episodes[1].status = RequestStatus.PENDING
        request.seasons[0].episodes[1].air_date = today - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        ep2 = _make_release(title="Show.S01E02.1080p", info_hash="s01e02")
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[ep2], query_time_ms=10)
        )
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=10)
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(ep2, score=60)
        stored_ep2 = MagicMock(title="Show.S01E02.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s01e02": stored_ep2},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        service.prowlarr.search_tv_episode_exact.assert_awaited_once_with(
            title="Test Show", season=1, episode=2, request_id=1
        )
        service.prowlarr.search_tv_season_sweep.assert_awaited_once()
        assert [r["title"] for r in result["selected_releases"]] == ["Show.S01E02.1080p"]

    @pytest.mark.asyncio
    async def test_default_search_stages_pack_without_exact_fallback(self, service, mock_db):
        request = _make_request(seasons=[1], episodes={1: [1, 2]})
        today = date.today()
        for episode in request.seasons[0].episodes:
            episode.status = RequestStatus.PENDING
            episode.air_date = today - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack = _make_release(title="Show.S01.1080p", info_hash="s01pack")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[pack], query_time_ms=10)
        )
        service.prowlarr.search_tv_episode_exact = AsyncMock()

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(pack, score=70)
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s01pack": MagicMock(title="Show.S01.1080p")},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        service.prowlarr.search_tv_episode_exact.assert_not_awaited()
        assert [r["title"] for r in result["selected_releases"]] == ["Show.S01.1080p"]

    @pytest.mark.asyncio
    async def test_default_search_falls_back_to_exact_when_pack_rejected_by_size(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1], episodes={1: [1, 2]})
        today = date.today()
        for episode in request.seasons[0].episodes:
            episode.status = RequestStatus.PENDING
            episode.air_date = today - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack = _make_release(title="Show.S01.2160p", info_hash="s01pack")
        ep1 = _make_release(title="Show.S01E01.1080p", info_hash="s01e01")
        ep2 = _make_release(title="Show.S01E02.1080p", info_hash="s01e02")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[pack], query_time_ms=10)
        )
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[ep1], query_time_ms=10),
                ProwlarrSearchResult(releases=[ep2], query_time_ms=10),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _failing_eval(pack, rejection_reason="Size exceeds per-season limit"),
            _passing_eval(ep1, score=60),
            _passing_eval(ep2, score=55),
        ]
        stored_ep1 = MagicMock(title="Show.S01E01.1080p")
        stored_ep2 = MagicMock(title="Show.S01E02.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s01e01": stored_ep1, "s01e02": stored_ep2},
            ) as store_mock,
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
            patch.object(service, "_set_episode_status", new_callable=AsyncMock) as set_episode,
        ):
            result = await service.process_request(1)

        assert service.prowlarr.search_tv_episode_exact.await_count == 2
        assert [
            call.kwargs for call in service.prowlarr.search_tv_episode_exact.await_args_list
        ] == [
            {"title": "Test Show", "season": 1, "episode": 1, "request_id": 1},
            {"title": "Test Show", "season": 1, "episode": 2, "request_id": 1},
        ]
        assert store_mock.await_args is not None
        assert [e.release.title for e in store_mock.await_args.args[2]] == [
            "Show.S01.2160p",
            "Show.S01E01.1080p",
            "Show.S01E02.1080p",
        ]
        assert [r["title"] for r in result["selected_releases"]] == [
            "Show.S01E01.1080p",
            "Show.S01E02.1080p",
        ]
        mock_staging.use_releases.assert_awaited_once_with(
            request, [stored_ep1, stored_ep2], selection_source="rule"
        )
        assert set_episode.await_args_list[0].args == (1, 1, 1, RequestStatus.DOWNLOADING)
        assert set_episode.await_args_list[1].args == (1, 1, 2, RequestStatus.DOWNLOADING)

    @pytest.mark.asyncio
    async def test_exact_episode_fallback_does_not_dedup_wrong_target_results(self, service):
        ep1 = _make_release(title="Show.S01E01.1080p", info_hash="s01e01")
        ep2 = _make_release(title="Show.S01E02.1080p", info_hash="s01e02")
        ep3 = _make_release(title="Show.S01E03.1080p", info_hash="s01e03")
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[ep1, ep2, ep3], query_time_ms=10)
        )
        request = _make_request(seasons=[1], episodes={1: [1, 2, 3]})
        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = lambda release: _passing_eval(release, score=60)

        _, passing, _ = await service._search_exact_episode_fallbacks_and_evaluate(
            request,
            rule_engine,
            [(1, 1), (1, 2), (1, 3)],
            seen_keys=set(),
        )

        assert [
            (season, episode, evaluation.release.title) for season, episode, evaluation in passing
        ] == [
            (1, 1, "Show.S01E01.1080p"),
            (1, 2, "Show.S01E02.1080p"),
            (1, 3, "Show.S01E03.1080p"),
        ]

    @pytest.mark.asyncio
    async def test_default_search_partial_pack_coverage_falls_back_for_uncovered_episode(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1, 2], episodes={1: [1], 2: [1]})
        today = date.today()
        for season in request.seasons:
            for episode in season.episodes:
                episode.status = RequestStatus.PENDING
                episode.air_date = today - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack = _make_release(title="Show.S01.1080p", info_hash="s01pack")
        ep = _make_release(title="Show.S02E01.1080p", info_hash="s02e01")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[pack], query_time_ms=10),
                ProwlarrSearchResult(releases=[], query_time_ms=10),
            ]
        )
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[ep], query_time_ms=10)
        )
        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(pack, score=80),
            _passing_eval(ep, score=65),
        ]
        stored_pack = MagicMock(title="Show.S01.1080p")
        stored_ep = MagicMock(title="Show.S02E01.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s01pack": stored_pack, "s02e01": stored_ep},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
            patch.object(service, "_set_episodes_for_season", new_callable=AsyncMock) as set_season,
            patch.object(service, "_set_episode_status", new_callable=AsyncMock) as set_episode,
        ):
            result = await service.process_request(1)

        service.prowlarr.search_tv_episode_exact.assert_awaited_once_with(
            title="Test Show", season=2, episode=1, request_id=1
        )
        assert [r["title"] for r in result["selected_releases"]] == [
            "Show.S01.1080p",
            "Show.S02E01.1080p",
        ]
        mock_staging.use_releases.assert_awaited_once_with(
            request, [stored_pack, stored_ep], selection_source="rule"
        )
        set_season.assert_awaited_once_with(1, 1, RequestStatus.DOWNLOADING)
        set_episode.assert_awaited_once_with(1, 2, 1, RequestStatus.DOWNLOADING)

    @pytest.mark.asyncio
    async def test_full_search_refreshes_available_episode_but_does_not_stage_it(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1], episodes={1: [1, 2]})
        today = date.today()
        request.seasons[0].episodes[0].status = RequestStatus.COMPLETED
        request.seasons[0].episodes[0].air_date = today - timedelta(days=1)
        request.seasons[0].episodes[1].status = RequestStatus.PENDING
        request.seasons[0].episodes[1].air_date = today - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        ep1 = _make_release(title="Show.S01E01.1080p", info_hash="s01e01")
        ep2 = _make_release(title="Show.S01E02.1080p", info_hash="s01e02")
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[ep1], query_time_ms=10),
                ProwlarrSearchResult(releases=[ep2], query_time_ms=10),
            ]
        )
        service.prowlarr.search_tv_packs_broad = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=10)
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(ep1, score=70),
            _passing_eval(ep2, score=60),
        ]
        stored_ep2 = MagicMock(title="Show.S01E02.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s01e02": stored_ep2},
            ) as store_mock,
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1, search_mode="full")

        assert service.prowlarr.search_tv_episode_exact.await_count == 2
        service.prowlarr.search_tv_packs_broad.assert_awaited_once()
        assert store_mock.await_args is not None
        stored_titles = [e.release.title for e in store_mock.await_args.args[2]]
        assert stored_titles == ["Show.S01E01.1080p", "Show.S01E02.1080p"]
        assert [r["title"] for r in result["selected_releases"]] == ["Show.S01E02.1080p"]

    @pytest.mark.asyncio
    async def test_full_search_all_available_refreshes_without_pending_or_staging(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1], episodes={1: [1]}, status=RequestStatus.COMPLETED)
        request.seasons[0].episodes[0].status = RequestStatus.COMPLETED
        request.seasons[0].episodes[0].air_date = date.today() - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        ep1 = _make_release(title="Show.S01E01.1080p", info_hash="s01e01")
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[ep1], query_time_ms=10)
        )
        service.prowlarr.search_tv_packs_broad = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=10)
        )
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(ep1, score=70)
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.add_to_pending_queue",
                new_callable=AsyncMock,
            ) as pending_mock,
        ):
            result = await service.process_request(1, search_mode="full")

        assert result["status"] == "completed"
        mock_staging.use_releases.assert_not_awaited()
        pending_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_search_broad_pack_can_stage_only_actionable_coverage(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1, 2], episodes={1: [1], 2: [1]})
        today = date.today()
        request.seasons[0].episodes[0].status = RequestStatus.COMPLETED
        request.seasons[0].episodes[0].air_date = today - timedelta(days=1)
        request.seasons[1].episodes[0].status = RequestStatus.PENDING
        request.seasons[1].episodes[0].air_date = today - timedelta(days=1)
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack = _make_release(title="Show.S02.1080p", info_hash="s02-pack")
        service.prowlarr.search_tv_episode_exact = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=10)
        )
        service.prowlarr.search_tv_packs_broad = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[pack], query_time_ms=10)
        )
        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(pack, score=80)
        stored_pack = MagicMock(title="Show.S02.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s02-pack": stored_pack},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
            patch.object(service, "_set_episodes_for_season", new_callable=AsyncMock) as set_season,
        ):
            result = await service.process_request(1, search_mode="full")

        assert [r["title"] for r in result["selected_releases"]] == ["Show.S02.1080p"]
        set_season.assert_awaited_once_with(1, 2, RequestStatus.DOWNLOADING)

    def test_actionable_targets_exclude_completed_staged_downloading_and_future(self, service):
        request = _make_request(seasons=[1, 2, 3], episodes={1: [1, 2], 2: [1], 3: [1, 2]})
        today = date.today()
        statuses = {
            (1, 1): RequestStatus.COMPLETED,
            (1, 2): RequestStatus.STAGED,
            (2, 1): RequestStatus.DOWNLOADING,
            (3, 1): RequestStatus.PENDING,
            (3, 2): RequestStatus.PENDING,
        }
        air_dates = {
            (3, 1): today - timedelta(days=1),
            (3, 2): today + timedelta(days=7),
        }
        for season in request.seasons:
            for episode in season.episodes:
                key = (season.season_number, episode.episode_number)
                episode.status = statuses[key]
                episode.air_date = air_dates.get(key, today - timedelta(days=1))

        seasons, episodes = service._get_actionable_targets(request)

        assert seasons == [3]
        assert episodes == {3: [1]}

    def test_actionable_pack_coverage_rejects_completed_requested_seasons(self, service):
        complete_series = _passing_eval(_make_release("Show.Complete.Series.1080p"))
        broad_requested_pack = _passing_eval(_make_release("Show.S01-S03.1080p"))
        season_three_pack = _passing_eval(_make_release("Show.S03.1080p"))

        all_requested = {1, 2, 3}
        actionable = {3}

        assert (
            service._get_actionable_pack_coverage(
                complete_series,
                actionable,
                all_requested,
            )
            == set()
        )
        assert (
            service._get_actionable_pack_coverage(
                broad_requested_pack,
                actionable,
                all_requested,
            )
            == set()
        )
        assert service._get_actionable_pack_coverage(
            season_three_pack,
            actionable,
            all_requested,
        ) == {3}
