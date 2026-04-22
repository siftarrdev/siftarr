"""Tests for TVDecisionService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus
from app.siftarr.services.prowlarr_service import ProwlarrRelease, ProwlarrSearchResult
from app.siftarr.services.rule_engine import ReleaseEvaluation
from app.siftarr.services.tv_decision_service import TVDecisionService


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


class TestProcessRequest:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

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

        pack_result = ProwlarrSearchResult(releases=[pack_release], query_time_ms=100)

        service.prowlarr.search_by_tvdbid = AsyncMock(side_effect=[pack_result])

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [_passing_eval(pack_release, score=80)]

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            await service.process_request(1)

        assert service.prowlarr.search_by_tvdbid.await_count == 1

    @pytest.mark.asyncio
    async def test_episode_fallback_only_searches_aired_db_episodes(self, service, mock_db):
        request = _make_request(seasons=[1])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        service._settings.max_episode_discovery = 2
        service.prowlarr.search_by_tvdbid = AsyncMock(
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
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            await service.process_request(1)

        searched_episodes = [
            call.kwargs.get("episode")
            for call in service.prowlarr.search_by_tvdbid.await_args_list
            if call.kwargs.get("episode") is not None
        ]
        assert searched_episodes == [1, 2]

    @pytest.mark.asyncio
    async def test_no_aired_or_explicit_episode_targets_skips_episode_fallback(
        self, service, mock_db
    ):
        request = _make_request(seasons=[1])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        service.prowlarr.search_by_tvdbid = AsyncMock(
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
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            await service.process_request(1)

        assert service.prowlarr.search_by_tvdbid.await_count == 1

    @pytest.mark.asyncio
    async def test_multi_season_requests_include_broad_pack_search(self, service, mock_db):
        request = _make_request(
            seasons=[1, 2],
            episodes={1: [1], 2: [1]},
        )
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        broad_pack = _make_release(title="Show.S01-S02.1080p")

        broad_pack_result = ProwlarrSearchResult(releases=[broad_pack], query_time_ms=100)
        service.prowlarr.search_by_tvdbid = AsyncMock(
            side_effect=[
                broad_pack_result,
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(broad_pack, score=90),
        ]

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            await service.process_request(1)

        first_call = service.prowlarr.search_by_tvdbid.await_args_list[0]
        assert first_call.kwargs["season"] is None
        assert first_call.kwargs.get("episode") is None
        assert service.prowlarr.search_by_tvdbid.await_count == 1

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

        pack_result = ProwlarrSearchResult(releases=[pack_release], query_time_ms=100)
        ep_result = ProwlarrSearchResult(releases=[ep_release], query_time_ms=100)

        service.prowlarr.search_by_tvdbid = AsyncMock(side_effect=[pack_result, ep_result])

        rule_engine = MagicMock()
        pack_eval = _passing_eval(pack_release, score=80)
        ep_eval = _passing_eval(ep_release, score=50)
        rule_engine.evaluate.side_effect = [pack_eval, ep_eval]

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert "Show.S01.1080p" in selected_titles

    @pytest.mark.asyncio
    async def test_single_season_request_rejects_broad_pack_from_season_search(
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

        service.prowlarr.search_by_tvdbid = AsyncMock(
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

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.S01E01.1080p"]

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

        service.prowlarr.search_by_tvdbid = AsyncMock(
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

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
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

        service.prowlarr.search_by_tvdbid = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[], query_time_ms=100),
                ProwlarrSearchResult(releases=[season_one_pack], query_time_ms=100),
                ProwlarrSearchResult(releases=[], query_time_ms=100),
                ProwlarrSearchResult(releases=[season_two_episode], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.side_effect = [
            _passing_eval(season_one_pack, score=80),
            _passing_eval(season_two_episode, score=55),
        ]

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.S01.1080p", "Show.S02E01.1080p"]

    @pytest.mark.asyncio
    async def test_single_season_request_rejects_complete_series_from_season_search(
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

        service.prowlarr.search_by_tvdbid = AsyncMock(
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

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            result = await service.process_request(1)

        selected_titles = [r["title"] for r in result.get("selected_releases", [])]
        assert selected_titles == ["Show.S01E01.1080p"]

    @pytest.mark.asyncio
    async def test_episode_discovery_range_respected(self, service, mock_db):
        request = _make_request(seasons=[1])
        mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=request))
        mock_db.commit = AsyncMock()

        empty_result = ProwlarrSearchResult(releases=[], query_time_ms=100)
        service.prowlarr.search_by_tvdbid = AsyncMock(return_value=empty_result)
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
                    "app.siftarr.services.tv_decision_service.store_search_results",
                    new_callable=AsyncMock,
                ),
                patch(
                    "app.siftarr.services.tv_decision_service.PendingQueueService",
                    lambda db: MagicMock(add_to_queue=AsyncMock()),
                ),
            ):
                await service.process_request(1)

        searched_episodes = [
            call.kwargs.get("episode")
            for call in service.prowlarr.search_by_tvdbid.await_args_list
            if call.kwargs.get("episode") is not None
        ]
        assert searched_episodes == [1, 2, 3]

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
        service.prowlarr.search_by_tvdbid = AsyncMock(
            side_effect=[
                ProwlarrSearchResult(releases=[pack_release], query_time_ms=100),
                ProwlarrSearchResult(releases=[], query_time_ms=100),
            ]
        )

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _passing_eval(pack_release, score=80)

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.use_releases", new_callable=AsyncMock
            ) as mock_use,
            patch.object(
                service, "_update_episode_status", new_callable=AsyncMock
            ) as mock_update_episode,
            patch.object(
                service, "_update_season_status", new_callable=AsyncMock
            ) as mock_update_season,
        ):
            mock_use.return_value = {"status": "downloading", "message": "ok"}
            await service.process_request(1)

        assert mock_update_episode.await_args_list[0].args == (
            1,
            1,
            None,
            RequestStatus.DOWNLOADING,
        )
        assert mock_update_season.await_args_list[0].args == (1, 1, RequestStatus.DOWNLOADING)
        assert all(
            call.args[-1] != RequestStatus.SEARCHING for call in mock_update_episode.await_args_list
        )
        assert all(
            call.args[-1] != RequestStatus.SEARCHING for call in mock_update_season.await_args_list
        )

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

        service.prowlarr.search_by_tvdbid = AsyncMock(return_value=search_result)

        rule_engine = MagicMock()
        rule_engine.evaluate.return_value = _failing_eval(release)

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            result = await service.process_request(1)

        assert result["status"] == "pending"

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
        service.prowlarr.search_by_tvdbid = AsyncMock(return_value=error_result)

        rule_engine = MagicMock()

        with (
            patch.object(
                service, "_get_rule_engine", new_callable=AsyncMock, return_value=rule_engine
            ),
            patch(
                "app.siftarr.services.tv_decision_service.store_search_results",
                new_callable=AsyncMock,
            ),
            patch(
                "app.siftarr.services.tv_decision_service.PendingQueueService",
                lambda db: MagicMock(add_to_queue=AsyncMock()),
            ),
        ):
            result = await service.process_request(1)

        assert result["status"] == "pending"
        assert "Timeout" in result.get("search_errors", [])
