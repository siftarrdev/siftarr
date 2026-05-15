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


def _failing_eval(release):
    return ReleaseEvaluation(
        release=release,
        passed=False,
        total_score=-100,
        matches=[],
        rejection_reason="Excluded",
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
    async def test_single_season_pack_selection_skips_episode_fallback(self, service, mock_db):
        request = _make_request(
            seasons=[1],
            episodes={1: [1, 2]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack_release = _make_release(title="Show.S01.1080p")
        stored_pack_release = MagicMock()
        stored_pack_release.title = "Show.S01.1080p"

        pack_result = ProwlarrSearchResult(releases=[pack_release], query_time_ms=100)

        service.prowlarr.search_tv_season_sweep = AsyncMock(side_effect=[pack_result])

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [_passing_eval(pack_release, score=80)]

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"Show.S01.1080p": stored_pack_release},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            await service.process_request(1)

        assert service.prowlarr.search_tv_season_sweep.await_count == 1

    @pytest.mark.asyncio
    async def test_episode_fallback_uses_season_sweep_not_episode_searches(self, service, mock_db):
        request = _make_request(seasons=[1])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        service._settings.max_episode_discovery = 2
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=100)
        )

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=MagicMock()
            ),
            patch.object(
                service,
                "_get_aired_db_episodes_for_season",
                new_callable=AsyncMock,
                return_value=[1, 2, 3],
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.decisions.decision_pipeline.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            await service.process_request(1)

        assert service.prowlarr.search_tv_season_sweep.await_count == 1
        await_args = service.prowlarr.search_tv_season_sweep.await_args
        assert await_args is not None
        assert await_args.kwargs["season"] == 1

    @pytest.mark.asyncio
    async def test_no_aired_or_explicit_episode_targets_skips_episode_fallback(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(releases=[], query_time_ms=100)
        )

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=MagicMock()
            ),
            patch.object(
                service,
                "_get_aired_db_episodes_for_season",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.decisions.decision_pipeline.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            await service.process_request(1)

        assert service.prowlarr.search_tv_season_sweep.await_count == 1

    @pytest.mark.asyncio
    async def test_multi_season_requests_search_each_requested_season_once(self, service, mock_db):
        request = _make_request(
            seasons=[1, 2],
            episodes={1: [1], 2: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        broad_pack = _make_release(title="Show.S01-S02.1080p")
        stored_broad_pack = MagicMock()
        stored_broad_pack.title = "Show.S01-S02.1080p"

        broad_pack_result = ProwlarrSearchResult(releases=[broad_pack], query_time_ms=100)
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[broad_pack_result, ProwlarrSearchResult(releases=[], query_time_ms=100)]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(broad_pack, score=90),
        ]

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"Show.S01-S02.1080p": stored_broad_pack},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            await service.process_request(1)

        searched_seasons = [
            call.kwargs["season"]
            for call in service.prowlarr.search_tv_season_sweep.await_args_list
        ]
        assert searched_seasons == [1, 2]

    @pytest.mark.asyncio
    async def test_limited_sweep_no_longer_triggers_exact_episode_fallback(self, service, mock_db):
        request = _make_request(seasons=[2, 3], episodes={2: [1, 2]})
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        sweep_ep2 = _make_release(title="Show.S02E02.1080p", info_hash="s02e02-sweep")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(
                releases=[sweep_ep2], query_time_ms=100, hit_limit=True
            )
        )
        service.prowlarr.search_by_tvdbid = AsyncMock()

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [_passing_eval(sweep_ep2, score=50)]
        stored_ep2 = MagicMock(title="Show.S02E02.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s02e02-sweep": stored_ep2},
            ) as store_mock,
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            await service.process_request(1)

        assert [
            c.kwargs["season"] for c in service.prowlarr.search_tv_season_sweep.await_args_list
        ] == [2]
        service.prowlarr.search_by_tvdbid.assert_not_awaited()
        store_call = store_mock.await_args
        assert store_call is not None
        stored_titles = [evaluation.release.title for evaluation in store_call.args[2]]
        assert stored_titles == ["Show.S02E02.1080p"]

    @pytest.mark.asyncio
    async def test_limited_sweep_skips_exact_fallback_when_episode_present(self, service, mock_db):
        request = _make_request(seasons=[2], episodes={2: [1]})
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        sweep_ep1 = _make_release(title="Show.S02E01.1080p", info_hash="s02e01-sweep")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(
                releases=[sweep_ep1], query_time_ms=100, hit_limit=True
            )
        )
        service.prowlarr.search_by_tvdbid = AsyncMock()

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(sweep_ep1, score=50)
        stored_ep1 = MagicMock(title="Show.S02E01.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"s02e01-sweep": stored_ep1},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            await service.process_request(1)

        service.prowlarr.search_by_tvdbid.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_selects_only_passing_s02_exact_releases_from_sweep(self, service, mock_db):
        request = _make_request(seasons=[2], episodes={2: [1, 2, 3]})
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        sweep_ep1 = _make_release(title="Show.S02E01.1080p", info_hash="s02e01-sweep")
        failed_sweep_ep2 = _make_release(title="Show.S02E02.720p", info_hash="s02e02-failed")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            return_value=ProwlarrSearchResult(
                releases=[sweep_ep1, failed_sweep_ep2], query_time_ms=100, hit_limit=True
            )
        )
        service.prowlarr.search_by_tvdbid = AsyncMock()

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(sweep_ep1, score=70),
            _failing_eval(failed_sweep_ep2),
        ]
        stored_ep1 = MagicMock(title="Show.S02E01.1080p")
        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={
                    "s02e01-sweep": stored_ep1,
                },
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        selected_titles = [release["title"] for release in result["selected_releases"]]
        assert selected_titles == ["Show.S02E01.1080p"]
        staged_releases = mock_staging.use_releases.await_args.args[1]
        assert staged_releases == [stored_ep1]
        service.prowlarr.search_by_tvdbid.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_season_packs_preferred_over_episodes(self, service, mock_db):
        request = _make_request(
            seasons=[1],
            episodes={1: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack_release = _make_release(title="Show.S01.1080p")
        ep_release = _make_release(title="Show.S01E01.1080p")

        pack_result = ProwlarrSearchResult(releases=[pack_release, ep_release], query_time_ms=100)

        service.prowlarr.search_tv_season_sweep = AsyncMock(return_value=pack_result)

        rule_engine = MagicMock()
        pack_eval = _passing_eval(pack_release, score=80)
        ep_eval = _passing_eval(ep_release, score=50)
        rule_engine.evaluate.side_effect = [pack_eval, ep_eval]
        stored_pack_release = MagicMock(title="Show.S01.1080p")
        stored_ep_release = MagicMock(title="Show.S01E01.1080p")

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={
                    "Show.S01.1080p": stored_pack_release,
                    "Show.S01E01.1080p": stored_ep_release,
                },
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.S01.1080p"]
        staged_releases = mock_staging.use_releases.await_args.args[1]
        assert staged_releases == [stored_pack_release]

    @pytest.mark.asyncio
    async def test_single_season_request_can_select_multi_season_pack_from_season_sweep(
        self, service, mock_db
    ):
        request = _make_request(
            seasons=[1],
            episodes={1: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        broad_pack = _make_release(title="Show.S01-S07.1080p", info_hash="season-broad-pack")
        episode_release = _make_release(title="Show.S01E01.1080p", info_hash="season-episode")

        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[broad_pack], query_time_ms=100),
                ProwlarrSearchResult(releases=[episode_release], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(broad_pack, score=95),
            _passing_eval(episode_release, score=50),
        ]
        stored_episode_release = MagicMock()
        stored_episode_release.title = "Show.S01E01.1080p"

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"season-episode": stored_episode_release},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.S01-S07.1080p"]

    @pytest.mark.asyncio
    async def test_multi_season_request_accepts_broad_pack_from_broad_search(
        self, service, mock_db
    ):
        request = _make_request(
            seasons=[1, 2],
            episodes={1: [1], 2: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        complete_series = _make_release(
            title="Show.Complete.Series.1080p", info_hash="broad-complete-series"
        )
        season_one_episode = _make_release(title="Show.S01E01.1080p", info_hash="broad-s01e01")
        season_two_episode = _make_release(title="Show.S02E01.1080p", info_hash="broad-s02e01")

        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[complete_series], query_time_ms=100),
                ProwlarrSearchResult(releases=[], query_time_ms=100),
                ProwlarrSearchResult(releases=[season_one_episode], query_time_ms=100),
                ProwlarrSearchResult(releases=[], query_time_ms=100),
                ProwlarrSearchResult(releases=[season_two_episode], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(complete_series, score=95),
            _passing_eval(season_one_episode, score=50),
            _passing_eval(season_two_episode, score=45),
        ]
        stored_complete_series = MagicMock()
        stored_complete_series.title = "Show.Complete.Series.1080p"

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"broad-complete-series": stored_complete_series},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.Complete.Series.1080p"]

    @pytest.mark.asyncio
    async def test_episode_fallback_used_for_uncovered_season_when_season_search_returns_broad_pack(
        self, service, mock_db
    ):
        request = _make_request(
            seasons=[1, 2],
            episodes={1: [1], 2: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        season_one_pack = _make_release(title="Show.S01.1080p", info_hash="fallback-s01-pack")
        season_two_episode = _make_release(title="Show.S02E01.1080p", info_hash="fallback-s02e01")

        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[season_one_pack], query_time_ms=100),
                ProwlarrSearchResult(releases=[season_two_episode], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(season_one_pack, score=80),
            _passing_eval(season_two_episode, score=55),
        ]
        stored_season_one_pack = MagicMock()
        stored_season_one_pack.title = "Show.S01.1080p"
        stored_season_two_episode = MagicMock()
        stored_season_two_episode.title = "Show.S02E01.1080p"

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={
                    "fallback-s01-pack": stored_season_one_pack,
                    "fallback-s02e01": stored_season_two_episode,
                },
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.S01.1080p", "Show.S02E01.1080p"]

    @pytest.mark.asyncio
    async def test_single_season_request_can_select_complete_series_from_season_sweep(
        self, service, mock_db
    ):
        request = _make_request(
            seasons=[1],
            episodes={1: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        complete_series = _make_release(
            title="Show.Complete.Series.1080p", info_hash="season-complete-series"
        )
        episode_release = _make_release(title="Show.S01E01.1080p", info_hash="complete-fallback")

        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[complete_series], query_time_ms=100),
                ProwlarrSearchResult(releases=[episode_release], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(complete_series, score=95),
            _passing_eval(episode_release, score=50),
        ]
        stored_episode_release = MagicMock()
        stored_episode_release.title = "Show.S01E01.1080p"

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"complete-fallback": stored_episode_release},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.Complete.Series.1080p"]

    @pytest.mark.asyncio
    async def test_episode_discovery_range_respected(self, service, mock_db):
        request = _make_request(seasons=[1])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        empty_result = ProwlarrSearchResult(releases=[], query_time_ms=100)
        service.prowlarr.search_tv_season_sweep = AsyncMock(return_value=empty_result)
        service._settings.max_episode_discovery = 3

        with patch.object(service, "_get_rule_engine", new_callable=AsyncMock) as mock_rule:
            mock_rule.return_value = MagicMock()
            with (
                patch.object(
                    service,
                    "_get_aired_db_episodes_for_season",
                    new_callable=AsyncMock,
                    return_value=[1, 2, 3, 4],
                ),
                patch(
                    "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                    new_callable=AsyncMock,
                ),
                patch(
                    "app.siftarr.services.decisions.decision_pipeline.PendingQueueService",
                    lambda db: MagicMock(add_to_queue=AsyncMock()),
                ),
            ):
                await service.process_request(1)

        assert service.prowlarr.search_tv_season_sweep.await_count == 1

    @pytest.mark.asyncio
    async def test_status_updates_only_apply_final_action_status(self, service, mock_db):
        request = _make_request(seasons=[1], episodes={1: [1]})
        stored_release = MagicMock()
        stored_release.title = "Show.S01.1080p"

        execute_results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=request)),
            MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[stored_release]))
                )
            ),
        ]
        mock_db.execute = AsyncMock(side_effect=execute_results)
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack_release = _make_release(title="Show.S01.1080p")
        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[pack_release], query_time_ms=100),
                ProwlarrSearchResult(releases=[], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(pack_release, score=80)

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"Show.S01.1080p": stored_release},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
            patch.object(
                service, "_set_episodes_for_season", new_callable=AsyncMock
            ) as mock_set_episodes,
        ):
            await service.process_request(1)

        assert mock_set_episodes.await_args_list[0].args == (
            1,
            1,
            RequestStatus.DOWNLOADING,
        )
        assert all(
            call.args[-1] != RequestStatus.SEARCHING for call in mock_set_episodes.await_args_list
        )

    @pytest.mark.asyncio
    async def test_selected_tv_releases_use_persisted_dedupe_key_not_title(self, service, mock_db):
        request = _make_request(seasons=[1], episodes={1: [1, 2]})
        stored_release = MagicMock()
        stored_release.id = 500
        stored_release.title = "Show.S01.1080p"
        stored_release.info_hash = "selected-hash"

        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        selected_pack = _make_release(title="Show.S01.1080p", info_hash="selected-hash")
        duplicate_title_other_hash = _make_release(title="Show.S01.1080p", info_hash="other-hash")

        service.prowlarr.search_tv_season_sweep = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(
                    releases=[selected_pack, duplicate_title_other_hash], query_time_ms=100
                )
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(selected_pack, score=80),
            _passing_eval(duplicate_title_other_hash, score=70),
        ]

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={
                    "selected-hash": stored_release,
                    "other-hash": MagicMock(id=501, title="Show.S01.1080p", info_hash="other-hash"),
                },
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            await service.process_request(1)

        assert mock_staging.use_releases.await_args is not None
        selected_releases = mock_staging.use_releases.await_args.args[1]
        assert selected_releases == [stored_release]

    @pytest.mark.asyncio
    async def test_no_passing_releases_goes_to_pending(self, service, mock_db):
        request = _make_request(
            seasons=[1],
            episodes={1: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        release = _make_release()
        search_result = ProwlarrSearchResult(releases=[release], query_time_ms=100)

        service.prowlarr.search_tv_season_sweep = AsyncMock(return_value=search_result)

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _failing_eval(release)

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.decisions.decision_pipeline.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            result = await service.process_request(1)

        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_search_episodes_false_skips_episode_search(self, service, mock_db):
        """When search_episodes=False, individual episodes should not be searched."""
        request = _make_request(
            seasons=[1, 2],
            episodes={1: [1, 2], 2: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        empty_result = ProwlarrSearchResult(releases=[], query_time_ms=100)
        service.prowlarr.search_tv_season_sweep = AsyncMock(return_value=empty_result)

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=MagicMock()
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.decisions.decision_pipeline.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            await service.process_request(1, search_episodes=False)

        episode_calls = [
            call
            for call in service.prowlarr.search_tv_season_sweep.await_args_list
            if call.kwargs.get("episode") is not None
        ]
        assert len(episode_calls) == 0

    @pytest.mark.asyncio
    async def test_search_episodes_false_with_pack_found(self, service, mock_db):
        """When search_episodes=False and a pack is found, it should be used without episode search."""
        request = _make_request(
            seasons=[1],
            episodes={1: [1, 2]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        pack_release = _make_release(title="Show.S01.1080p")
        stored_pack_release = MagicMock()
        stored_pack_release.title = "Show.S01.1080p"

        pack_result = ProwlarrSearchResult(releases=[pack_release], query_time_ms=100)
        service.prowlarr.search_tv_season_sweep = AsyncMock(side_effect=[pack_result])

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [_passing_eval(pack_release, score=80)]

        mock_staging = _mock_staging()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
                return_value={"Show.S01.1080p": stored_pack_release},
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.StagingService",
                return_value=mock_staging,
            ),
        ):
            result = await service.process_request(1, search_episodes=False)

        assert service.prowlarr.search_tv_season_sweep.await_count == 1
        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert "Show.S01.1080p" in selected_titles

    @pytest.mark.asyncio
    async def test_search_errors_are_collected(self, service, mock_db):
        request = _make_request(
            seasons=[1],
            episodes={1: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        error_result = ProwlarrSearchResult(releases=[], query_time_ms=0, error="Timeout")
        service.prowlarr.search_tv_season_sweep = AsyncMock(return_value=error_result)

        rule_engine = MagicMock()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.decisions.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.decisions.decision_pipeline.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            result = await service.process_request(1)

        assert result["status"] == "pending"
        assert "Timeout" in result.get("search_errors", [])

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
