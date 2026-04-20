"""Tests for improved download detection (Phase 3C)."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if sys.version_info < (3, 11):  # noqa: UP036
    pytest.skip("Requires Python 3.11+ for StrEnum", allow_module_level=True)

from app.siftarr.models.request import MediaType, Request, RequestStatus  # noqa: E402
from app.siftarr.models.staged_torrent import StagedTorrent  # noqa: E402


def _make_torrent(
    id: int = 1,
    request_id: int | None = 1,
    magnet_url: str | None = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&dn=test",
    title: str = "Test Torrent",
    status: str = "approved",
) -> MagicMock:
    t = MagicMock(spec=StagedTorrent)
    t.id = id
    t.request_id = request_id
    t.magnet_url = magnet_url
    t.title = title
    t.status = status
    return t


def _make_request(
    id: int = 1,
    status: RequestStatus = RequestStatus.DOWNLOADING,
    media_type: MediaType = MediaType.MOVIE,
    tmdb_id: int | None = 12345,
    tvdb_id: int | None = None,
) -> MagicMock:
    r = MagicMock(spec=Request)
    r.id = id
    r.status = status
    r.media_type = media_type
    r.tmdb_id = tmdb_id
    r.tvdb_id = tvdb_id
    return r


class TestGetDownloadStatus:
    """Test that get_download_status includes qbit_complete and plex_available."""

    @pytest.mark.asyncio
    async def test_response_includes_qbit_complete_and_plex_available_fields(self):
        """The download-status response should have qbit_complete and plex_available keys."""
        from app.siftarr.routers.staged import get_download_status

        mock_db = AsyncMock()

        # First query: approved torrents
        torrent = _make_torrent()
        mock_scalars_1 = MagicMock()
        mock_scalars_1.all.return_value = [torrent]
        mock_result_1 = MagicMock()
        mock_result_1.scalars.return_value = mock_scalars_1

        # Second query: request statuses
        mock_result_2 = MagicMock()
        mock_result_2.all.return_value = [(1, RequestStatus.DOWNLOADING)]

        # Third query: existing log count (for download_completed dedup)
        mock_scalar_3 = MagicMock()
        mock_scalar_3.scalar.return_value = 1  # already logged

        mock_db.execute = AsyncMock(side_effect=[mock_result_1, mock_result_2, mock_scalar_3])

        with (
            patch(
                "app.siftarr.routers.staged.get_effective_settings", new_callable=AsyncMock
            ) as mock_settings,
            patch("app.siftarr.routers.staged.QbittorrentService") as MockQbit,
        ):
            mock_settings.return_value = MagicMock()
            mock_qbit_instance = AsyncMock()
            mock_qbit_instance.get_torrent_info = AsyncMock(
                return_value={"progress": 1.0, "state": "uploading"}
            )
            MockQbit.return_value = mock_qbit_instance

            response = await get_download_status(db=mock_db)

        import json

        body = json.loads(bytes(response.body))
        assert len(body["torrents"]) == 1
        t = body["torrents"][0]
        assert "qbit_complete" in t
        assert "plex_available" in t
        assert t["qbit_complete"] is True
        assert t["plex_available"] is False

    @pytest.mark.asyncio
    async def test_incomplete_torrent_has_false_fields(self):
        """Incomplete torrents should have qbit_complete=False, plex_available=False."""
        from app.siftarr.routers.staged import get_download_status

        mock_db = AsyncMock()

        torrent = _make_torrent()
        mock_scalars_1 = MagicMock()
        mock_scalars_1.all.return_value = [torrent]
        mock_result_1 = MagicMock()
        mock_result_1.scalars.return_value = mock_scalars_1

        mock_result_2 = MagicMock()
        mock_result_2.all.return_value = [(1, RequestStatus.DOWNLOADING)]

        mock_db.execute = AsyncMock(side_effect=[mock_result_1, mock_result_2])

        with (
            patch(
                "app.siftarr.routers.staged.get_effective_settings", new_callable=AsyncMock
            ) as mock_settings,
            patch("app.siftarr.routers.staged.QbittorrentService") as MockQbit,
        ):
            mock_settings.return_value = MagicMock()
            mock_qbit_instance = AsyncMock()
            mock_qbit_instance.get_torrent_info = AsyncMock(
                return_value={"progress": 0.5, "state": "downloading"}
            )
            MockQbit.return_value = mock_qbit_instance

            response = await get_download_status(db=mock_db)

        import json

        body = json.loads(bytes(response.body))
        t = body["torrents"][0]
        assert t["qbit_complete"] is False
        assert t["plex_available"] is False


class TestCheckNow:
    """Test the POST /staged/{torrent_id}/check-now endpoint."""

    @pytest.mark.asyncio
    async def test_check_now_incomplete_download(self):
        """check-now with incomplete download should not attempt Plex check."""
        from app.siftarr.routers.staged import check_now

        mock_db = AsyncMock()
        torrent = _make_torrent()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = torrent
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch(
                "app.siftarr.routers.staged.get_effective_settings", new_callable=AsyncMock
            ) as mock_settings,
            patch("app.siftarr.routers.staged.QbittorrentService") as MockQbit,
        ):
            mock_settings.return_value = MagicMock()
            mock_qbit_instance = AsyncMock()
            mock_qbit_instance.get_torrent_info = AsyncMock(
                return_value={"progress": 0.3, "state": "downloading"}
            )
            MockQbit.return_value = mock_qbit_instance

            response = await check_now(torrent_id=1, db=mock_db)

        import json

        body = json.loads(bytes(response.body))
        assert body["qbit_complete"] is False
        assert body["plex_available"] is False
        assert body["qbit_progress"] == 0.3

    @pytest.mark.asyncio
    async def test_check_now_complete_triggers_plex(self):
        """check-now with complete download should attempt Plex check."""
        from app.siftarr.routers.staged import check_now

        mock_db = AsyncMock()
        torrent = _make_torrent()
        request_obj = _make_request()

        # First call: load torrent; subsequent calls: various queries
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # Load torrent
                result.scalar_one_or_none.return_value = torrent
            elif call_count == 2:
                # Count existing download_completed logs
                result.scalar.return_value = 0
            elif call_count == 3:
                # Load request for Plex check
                result.scalar_one_or_none.return_value = request_obj
            else:
                result.scalar_one_or_none.return_value = None
                result.scalar.return_value = 0
            return result

        mock_db.execute = mock_execute
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        with (
            patch(
                "app.siftarr.routers.staged.get_effective_settings", new_callable=AsyncMock
            ) as mock_settings,
            patch("app.siftarr.routers.staged.QbittorrentService") as MockQbit,
            patch("app.siftarr.services.plex_service.PlexService") as MockPlex,
        ):
            mock_settings.return_value = MagicMock()
            mock_qbit_instance = AsyncMock()
            mock_qbit_instance.get_torrent_info = AsyncMock(
                return_value={"progress": 1.0, "state": "uploading"}
            )
            MockQbit.return_value = mock_qbit_instance

            mock_plex_instance = AsyncMock()
            mock_plex_instance.check_movie_available = AsyncMock(return_value=True)
            MockPlex.return_value = mock_plex_instance

            response = await check_now(torrent_id=1, db=mock_db)

        import json

        body = json.loads(bytes(response.body))
        assert body["qbit_complete"] is True
        assert body["plex_available"] is True

    @pytest.mark.asyncio
    async def test_check_now_not_found(self):
        """check-now with non-existent torrent should raise 404."""
        from fastapi import HTTPException

        from app.siftarr.routers.staged import check_now

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await check_now(torrent_id=999, db=mock_db)
        assert exc_info.value.status_code == 404
