"""Tests for QbittorrentService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.siftarr.services.integrations.qbittorrent_service import (
    BulkTorrentPayload,
    MediaCategory,
    QbittorrentService,
)


class FakeTorrentsAddedMetadata(dict):
    """Small stand-in for qbittorrentapi TorrentsAddedMetadata."""


class TestMediaCategory:
    """Test cases for MediaCategory enum."""

    def test_movies_value(self):
        """Test MediaCategory.MOVIES value."""
        assert MediaCategory.MOVIES == "radarr"

    def test_tv_value(self):
        """Test MediaCategory.TV value."""
        assert MediaCategory.TV == "sonarr"


class TestQbittorrentServiceUnit:
    """Unit tests for QbittorrentService."""

    def test_client_property_creates_client(self):
        """Test client property creates qbittorrent client when accessed."""
        with patch(
            "app.siftarr.services.integrations.qbittorrent_service.get_settings"
        ) as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            with patch("qbittorrentapi.Client") as mock_client_class:
                service = QbittorrentService()
                _ = service.client

                mock_client_class.assert_called_once_with(
                    host="http://localhost:8080",
                    EXTRA_HEADERS={"Authorization": "Bearer qbt_test_key"},
                )

    def test_client_property_reuses_client(self):
        """Test client property reuses existing client."""
        with patch(
            "app.siftarr.services.integrations.qbittorrent_service.get_settings"
        ) as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            assert service.client is mock_client

    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        """Test successful authentication."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            mock_client.app.web_api_version = "v2.0"
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()):
                result = await service.authenticate()
                assert result is True

    @pytest.mark.asyncio
    async def test_authenticate_failure(self):
        """Test failed authentication."""
        import qbittorrentapi

        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch(
                "asyncio.to_thread", side_effect=qbittorrentapi.LoginFailed("Invalid credentials")
            ):
                result = await service.authenticate()
                assert result is False

    @pytest.mark.asyncio
    async def test_ensure_category_exists_already_exists(self):
        """Test ensure_category_exists when category exists."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            mock_client.torrents_categories = {"radarr", "sonarr"}
            service._client = mock_client

            with patch(
                "asyncio.to_thread", AsyncMock(return_value=mock_client.torrents_categories)
            ):
                result = await service.ensure_category_exists("radarr")
                assert result is True

    @pytest.mark.asyncio
    async def test_ensure_category_exists_error(self):
        """Test ensure_category_exists with error."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=Exception("Connection error")):
                result = await service.ensure_category_exists("radarr")
                assert result is False

    @pytest.mark.asyncio
    async def test_get_torrent_info_found(self):
        """Test getting torrent info for existing torrent."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_torrent = MagicMock()
            mock_torrent.hash = "abc123"
            mock_torrent.name = "Test.Torrent"
            mock_torrent.size = 1024
            mock_torrent.progress = 0.5
            mock_torrent.state = "downloading"
            mock_torrent.category = "radarr"
            mock_torrent.ratio = 0.1
            mock_torrent.added_on = 1234567890
            mock_torrent.completed_on = 1234567900
            mock_torrent.download_location = "/downloads"
            mock_torrent.save_path = "/downloads"
            mock_torrent.seeding_time = 3600

            mock_client = MagicMock()
            mock_client.torrents_info = MagicMock(return_value=[mock_torrent])
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=[mock_torrent])):
                result = await service.get_torrent_info("abc123")

                assert result is not None
                assert result["hash"] == "abc123"
                assert result["name"] == "Test.Torrent"
                assert result["save_path"] == "/downloads"
                assert result["seeding_time"] == 3600

    @pytest.mark.asyncio
    async def test_get_completed_torrents_uses_completed_filter(self):
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client
            mock_torrent = MagicMock()
            mock_torrent.hash = "abc"
            mock_torrent.name = "Done"
            mock_torrent.progress = 1.0
            mock_torrent.save_path = "/downloads"
            mock_torrent.seeding_time = 60

            with patch("asyncio.to_thread", AsyncMock(return_value=[mock_torrent])) as to_thread:
                result = await service.get_completed_torrents()

            assert result[0]["hash"] == "abc"
            assert result[0]["save_path"] == "/downloads"
            to_thread.assert_awaited_once_with(
                mock_client.torrents_info,
                status_filter="completed",
            )

    @pytest.mark.asyncio
    async def test_get_unfinished_torrents_keeps_unknown_progress(self):
        service = QbittorrentService(settings=MagicMock())
        with patch.object(
            service,
            "get_all_active_torrents_or_raise",
            AsyncMock(
                return_value=[
                    {"hash": "active", "progress": 0.5},
                    {"hash": "done", "progress": 1.0},
                    {"hash": "unknown", "progress": None},
                ]
            ),
        ):
            torrents = await service.get_unfinished_torrents_or_raise()

        assert [torrent["hash"] for torrent in torrents] == ["active", "unknown"]

    @pytest.mark.asyncio
    async def test_set_torrent_location_moves_by_default(self):
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()) as to_thread:
                result = await service.set_torrent_location("abc123", "/media/movies")

            assert result is True
            to_thread.assert_awaited_once_with(
                mock_client.torrents_set_location,
                torrent_hashes="abc123",
                location="/media/movies",
                move=True,
            )

    @pytest.mark.asyncio
    async def test_delete_torrents_keeps_files_by_default(self):
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()) as to_thread:
                result = await service.delete_torrents(["abc123", "def456"])

            assert result is True
            to_thread.assert_awaited_once_with(
                mock_client.torrents_delete,
                torrent_hashes=["abc123", "def456"],
                delete_files=False,
            )

    @pytest.mark.asyncio
    async def test_get_torrent_info_not_found(self):
        """Test getting torrent info for non-existent torrent."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            mock_client.torrents_info = MagicMock(return_value=[])
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=[])):
                result = await service.get_torrent_info("nonexistent")
                assert result is None

    @pytest.mark.asyncio
    async def test_get_torrents_by_category(self):
        """Test getting torrents by category."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_torrent1 = MagicMock()
            mock_torrent1.hash = "abc"
            mock_torrent1.name = "Torrent 1"
            mock_torrent1.size = 1024
            mock_torrent1.progress = 1.0
            mock_torrent1.state = "seeding"

            mock_torrent2 = MagicMock()
            mock_torrent2.hash = "def"
            mock_torrent2.name = "Torrent 2"
            mock_torrent2.size = 2048
            mock_torrent2.progress = 0.5
            mock_torrent2.state = "downloading"

            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock(return_value=[mock_torrent1, mock_torrent2])):
                result = await service.get_torrents_by_category("radarr")

                assert len(result) == 2
                assert result[0]["hash"] == "abc"

    @pytest.mark.asyncio
    async def test_get_torrents_by_category_error(self):
        """Test getting torrents by category with error."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=Exception("Error")):
                result = await service.get_torrents_by_category("radarr")
                assert result == []

    @pytest.mark.asyncio
    async def test_delete_torrent_success(self):
        """Test successful torrent deletion."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()):
                result = await service.delete_torrent("abc123")
                assert result is True

    @pytest.mark.asyncio
    async def test_delete_torrent_error(self):
        """Test torrent deletion with error."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", side_effect=Exception("Error")):
                result = await service.delete_torrent("abc123")
                assert result is False

    @pytest.mark.asyncio
    async def test_get_all_active_torrents_returns_list(self):
        """Test get_all_active_torrents returns list of dicts."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_t = MagicMock()
            mock_t.hash = "aaa"
            mock_t.name = "Test Movie"
            mock_t.progress = 0.5
            mock_t.state = "downloading"
            mock_t.category = "radarr"
            service._client = MagicMock()

            with patch("asyncio.to_thread", AsyncMock(return_value=[mock_t])):
                result = await service.get_all_active_torrents()

            assert len(result) == 1
            assert result[0]["hash"] == "aaa"
            assert result[0]["progress"] == 0.5

    @pytest.mark.asyncio
    async def test_get_all_active_torrents_error_returns_empty(self):
        """Test get_all_active_torrents returns [] on exception."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()

            with patch("asyncio.to_thread", side_effect=Exception("err")):
                result = await service.get_all_active_torrents()

            assert result == []

    @pytest.mark.asyncio
    async def test_get_torrent_progress_by_name_found(self):
        """Test get_torrent_progress_by_name finds matching torrent."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()

            active = [
                {
                    "hash": "x",
                    "name": "The Dark Knight 2008",
                    "progress": 0.75,
                    "state": "downloading",
                    "category": "radarr",
                }
            ]

            with patch.object(service, "get_all_active_torrents", AsyncMock(return_value=active)):
                result = await service.get_torrent_progress_by_name("dark knight")
            assert result == 0.75

    @pytest.mark.asyncio
    async def test_get_torrent_progress_by_name_not_found(self):
        """Test get_torrent_progress_by_name returns None when not found."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()

            active = [
                {
                    "hash": "x",
                    "name": "Some Other Movie",
                    "progress": 1.0,
                    "state": "seeding",
                    "category": "radarr",
                }
            ]

            with patch.object(service, "get_all_active_torrents", AsyncMock(return_value=active)):
                result = await service.get_torrent_progress_by_name("dark knight")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete_torrent_with_files(self):
        """Test deleting torrent with files."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.qbittorrent_url = "http://localhost:8080"
            mock_settings.qbittorrent_api_key = "qbt_test_key"
            mock_get_settings.return_value = mock_settings

            service = QbittorrentService()
            mock_client = MagicMock()
            service._client = mock_client

            with patch("asyncio.to_thread", AsyncMock()):
                result = await service.delete_torrent("abc123", delete_files=True)
                assert result is True

    @pytest.mark.asyncio
    async def test_add_torrent_treats_duplicate_response_as_success(self):
        """Unexpected add responses are success when qBit confirms the hash exists."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            torrent_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            magnet = f"magnet:?xt=urn:btih:{torrent_hash}"

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(
                    service,
                    "get_torrent_info",
                    AsyncMock(side_effect=[None, {"hash": torrent_hash}]),
                ) as get_info,
                patch("asyncio.to_thread", AsyncMock(return_value="Fails.")),
            ):
                result = await service.add_torrent(
                    magnet_uri=magnet,
                    category=MediaCategory.MOVIES,
                )

            assert result == torrent_hash
            assert get_info.await_count == 2

    @pytest.mark.asyncio
    async def test_add_torrent_ok_is_success_when_visibility_lookup_times_out(self):
        """Single add should succeed when qBit accepts before metadata is visible."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            magnet = "magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(service, "get_torrent_info", AsyncMock()) as get_info,
                patch(
                    "asyncio.to_thread",
                    AsyncMock(side_effect=["Ok.", TimeoutError("delayed")]),
                ),
            ):
                result = await service.add_torrent(
                    magnet_uri=magnet,
                    category=MediaCategory.MOVIES,
                )

            assert result == "abcdefghijklmnopqrstuvwxyz234567"
            get_info.assert_awaited_once_with("abcdefghijklmnopqrstuvwxyz234567")

    @pytest.mark.asyncio
    async def test_add_torrent_metadata_pending_is_success(self):
        """qbittorrentapi metadata with pending adds means accepted."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            response = FakeTorrentsAddedMetadata(
                added_torrent_ids=[], failure_count=0, pending_count=3, success_count=0
            )

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(service, "get_torrent_info", AsyncMock()) as get_info,
                patch("asyncio.to_thread", AsyncMock(return_value=response)),
            ):
                result = await service.add_torrent(
                    magnet_uri="magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
                    category=MediaCategory.MOVIES,
                )

            assert result == "abcdefghijklmnopqrstuvwxyz234567"
            get_info.assert_awaited_once_with("abcdefghijklmnopqrstuvwxyz234567")

    @pytest.mark.asyncio
    async def test_add_torrents_bulk_sends_more_than_seventeen_magnets_in_chunks(self):
        """Bulk add should submit every compatible magnet in conservative chunks."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            hashes = [f"{i:040x}" for i in range(18)]
            payloads = [
                BulkTorrentPayload(
                    key=i,
                    title=f"Release {i}",
                    magnet_uri=f"magnet:?xt=urn:btih:{torrent_hash}",
                    category=MediaCategory.MOVIES,
                )
                for i, torrent_hash in enumerate(hashes)
            ]

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(
                    service,
                    "get_torrent_info",
                    AsyncMock(side_effect=[None] * 18),
                ),
                patch("asyncio.to_thread", AsyncMock(return_value="Ok.")) as to_thread,
            ):
                results = await service.add_torrents_bulk(payloads)

            assert len(results) == 18
            assert all(result.success for result in results)
            add_call = to_thread.await_args_list[0]
            assert add_call.args[0] == service.client.torrents_add
            assert add_call.kwargs["urls"].count("\n") == 9
            second_add_call = to_thread.await_args_list[1]
            assert second_add_call.kwargs["urls"].count("\n") == 7

    @pytest.mark.asyncio
    async def test_add_torrents_bulk_ok_is_success_before_qbit_visibility(self, tmp_path):
        """qBit may return Ok before torrent metadata is queryable."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            payloads = []
            for i in range(18):
                torrent_path = tmp_path / f"release-{i}.torrent"
                torrent_path.write_bytes(b"not-bencoded-in-test")
                payloads.append(
                    BulkTorrentPayload(
                        key=i,
                        title=f"Release {i}",
                        torrent_path=str(torrent_path),
                        category=MediaCategory.MOVIES,
                    )
                )

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(service, "get_torrent_info", AsyncMock(return_value=None)) as get_info,
                patch.object(
                    service, "get_torrent_info_by_name", AsyncMock(return_value=None)
                ) as get_by_name,
                patch("asyncio.to_thread", AsyncMock(return_value="Ok.")) as to_thread,
            ):
                results = await service.add_torrents_bulk(payloads)

            assert len(results) == 18
            assert all(result.success for result in results)
            assert all(result.torrent_hash is None for result in results)
            assert to_thread.await_count == 2
            assert len(to_thread.await_args_list[0].kwargs["torrent_files"]) == 10
            assert len(to_thread.await_args_list[1].kwargs["torrent_files"]) == 8
            get_info.assert_not_awaited()
            get_by_name.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_torrents_bulk_metadata_pending_is_chunk_success(self):
        """Pending-only metadata is accepted for the whole chunk."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            payloads = [
                BulkTorrentPayload(
                    key=i,
                    title=f"Release {i}",
                    magnet_uri=f"magnet:?xt=urn:btih:{i + 1:040x}",
                    category=MediaCategory.MOVIES,
                )
                for i in range(3)
            ]
            response = FakeTorrentsAddedMetadata(
                added_torrent_ids=[], failure_count=0, pending_count=3, success_count=0
            )

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(service, "get_torrent_info", AsyncMock(side_effect=[None] * 3)),
                patch("asyncio.to_thread", AsyncMock(return_value=response)),
            ):
                results = await service.add_torrents_bulk(payloads)

            assert [result.success for result in results] == [True, True, True]
            assert [result.error for result in results] == [None, None, None]

    @pytest.mark.asyncio
    async def test_add_torrents_bulk_metadata_failure_marks_chunk_failed(self):
        """Failure metadata has no per-item mapping, so the whole chunk fails."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            payloads = [
                BulkTorrentPayload(
                    key=i,
                    title=f"Release {i}",
                    magnet_uri=f"magnet:?xt=urn:btih:{i + 1:040x}",
                    category=MediaCategory.MOVIES,
                )
                for i in range(2)
            ]
            response = FakeTorrentsAddedMetadata(
                added_torrent_ids=[], failure_count=1, pending_count=1, success_count=0
            )

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(service, "get_torrent_info", AsyncMock(side_effect=[None] * 2)),
                patch("asyncio.to_thread", AsyncMock(return_value=response)),
            ):
                results = await service.add_torrents_bulk(payloads)

            assert [result.success for result in results] == [False, False]
            assert all(
                "qBittorrent reported add failures" in (result.error or "") for result in results
            )

    @pytest.mark.asyncio
    async def test_add_torrents_bulk_partial_group_failure_leaves_other_group_success(self):
        """A failing option/category group should not fail successful groups."""
        with patch("app.siftarr.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value = MagicMock()
            service = QbittorrentService()
            service._client = MagicMock()
            movie_hash = "a" * 40
            tv_hash = "b" * 40
            payloads = [
                BulkTorrentPayload(
                    key="movie",
                    title="Movie",
                    magnet_uri=f"magnet:?xt=urn:btih:{movie_hash}",
                    category=MediaCategory.MOVIES,
                ),
                BulkTorrentPayload(
                    key="tv",
                    title="TV",
                    magnet_uri=f"magnet:?xt=urn:btih:{tv_hash}",
                    category=MediaCategory.TV,
                ),
            ]

            with (
                patch.object(service, "ensure_category_exists", AsyncMock(return_value=True)),
                patch.object(
                    service,
                    "get_torrent_info",
                    AsyncMock(side_effect=[None, None, {"hash": movie_hash}]),
                ),
                patch("asyncio.to_thread", AsyncMock(side_effect=["Ok.", RuntimeError("boom")])),
            ):
                results = await service.add_torrents_bulk(payloads)

            by_key = {result.key: result for result in results}
            assert by_key["movie"].success is True
            assert by_key["movie"].torrent_hash == movie_hash
            assert by_key["tv"].success is False
