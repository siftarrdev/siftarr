"""Tests for QbittorrentService.

The qbittorrentapi client is a MagicMock (see the `qbit_service` fixture). The
service calls it through asyncio.to_thread, which runs for real in these tests.
"""

from types import SimpleNamespace

import pytest
import qbittorrentapi

from app.siftarr.services.integrations.qbittorrent_service import (
    BulkTorrentPayload,
    MediaCategory,
)


class FakeTorrentsAddedMetadata(dict):
    """Small stand-in for qbittorrentapi TorrentsAddedMetadata."""


def _torrent(torrent_hash="abc123", name="Test.Torrent", progress=0.5, **extra):
    return SimpleNamespace(
        hash=torrent_hash,
        name=name,
        progress=progress,
        state="downloading",
        category="radarr",
        save_path="/downloads",
        seeding_time=3600,
        **extra,
    )


def _magnet_payload(key, torrent_hash, category=MediaCategory.MOVIES):
    return BulkTorrentPayload(
        key=key,
        title=f"Release {key}",
        magnet_uri=f"magnet:?xt=urn:btih:{torrent_hash}",
        category=category,
    )


class TestAuthenticate:
    async def test_success(self, qbit_service):
        qbit_service.client.app.web_api_version = "v2.0"

        assert await qbit_service.authenticate() is True

    async def test_login_failure(self, qbit_service):
        class App:
            @property
            def web_api_version(self):
                raise qbittorrentapi.LoginFailed("Invalid credentials")

        qbit_service.client.app = App()

        assert await qbit_service.authenticate() is False


class TestEnsureCategory:
    @pytest.mark.parametrize(
        ("existing", "expect_create"),
        [({"radarr", "sonarr"}, False), (set(), True)],
        ids=["exists", "missing"],
    )
    async def test_creates_only_when_missing(self, qbit_service, existing, expect_create):
        qbit_service.client.torrents_categories.return_value = existing

        assert await qbit_service.ensure_category_exists("radarr") is True
        assert qbit_service.client.torrents_create_category.called is expect_create

    async def test_error_returns_false(self, qbit_service):
        qbit_service.client.torrents_categories.side_effect = Exception("Connection error")

        assert await qbit_service.ensure_category_exists("radarr") is False


class TestTorrentQueries:
    async def test_get_torrent_info_found(self, qbit_service):
        qbit_service.client.torrents_info.return_value = [_torrent()]

        result = await qbit_service.get_torrent_info("abc123")

        assert result is not None
        assert result["hash"] == "abc123"
        assert result["name"] == "Test.Torrent"
        assert result["save_path"] == "/downloads"
        assert result["seeding_time"] == 3600
        qbit_service.client.torrents_info.assert_called_once_with(torrent_hashes="abc123")

    async def test_get_torrent_info_not_found(self, qbit_service):
        qbit_service.client.torrents_info.return_value = []

        assert await qbit_service.get_torrent_info("nonexistent") is None

    async def test_get_all_active_torrents_returns_list(self, qbit_service):
        qbit_service.client.torrents_info.return_value = [_torrent("aaa", "Test Movie")]

        result = await qbit_service.get_all_active_torrents()

        assert [(t["hash"], t["progress"]) for t in result] == [("aaa", 0.5)]

    async def test_get_all_active_torrents_error_returns_empty(self, qbit_service):
        qbit_service.client.torrents_info.side_effect = Exception("err")

        assert await qbit_service.get_all_active_torrents() == []

    async def test_get_completed_torrents_uses_completed_filter(self, qbit_service):
        qbit_service.client.torrents_info.return_value = [_torrent("abc", "Done", progress=1.0)]

        result = await qbit_service.get_completed_torrents()

        assert result[0]["hash"] == "abc"
        assert result[0]["save_path"] == "/downloads"
        qbit_service.client.torrents_info.assert_called_once_with(status_filter="completed")

    async def test_get_unfinished_torrents_keeps_unknown_progress(self, qbit_service):
        qbit_service.client.torrents_info.return_value = [
            _torrent("active", progress=0.5),
            _torrent("done", progress=1.0),
            _torrent("unknown", progress=None),
        ]

        torrents = await qbit_service.get_unfinished_torrents_or_raise()

        assert [torrent["hash"] for torrent in torrents] == ["active", "unknown"]

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("dark knight", 0.75), ("Missing Movie", None)],
        ids=["found", "not_found"],
    )
    async def test_get_torrent_progress_by_name(self, qbit_service, name, expected):
        qbit_service.client.torrents_info.return_value = [
            _torrent("x", "The Dark Knight 2008", progress=0.75)
        ]

        assert await qbit_service.get_torrent_progress_by_name(name) == expected


class TestMutations:
    async def test_set_torrent_location_moves_by_default(self, qbit_service):
        assert await qbit_service.set_torrent_location("abc123", "/media/movies") is True

        qbit_service.client.torrents_set_location.assert_called_once_with(
            torrent_hashes="abc123", location="/media/movies", move=True
        )

    @pytest.mark.parametrize(
        ("kwargs", "expected_delete_files"),
        [({}, False), ({"delete_files": True}, True)],
        ids=["keeps_files_by_default", "delete_files"],
    )
    async def test_delete_torrent(self, qbit_service, kwargs, expected_delete_files):
        assert await qbit_service.delete_torrent("abc123", **kwargs) is True

        qbit_service.client.torrents_delete.assert_called_once_with(
            torrent_hashes="abc123", delete_files=expected_delete_files
        )

    async def test_delete_torrents_accepts_hash_list(self, qbit_service):
        assert await qbit_service.delete_torrents(["abc123", "def456"]) is True

        qbit_service.client.torrents_delete.assert_called_once_with(
            torrent_hashes=["abc123", "def456"], delete_files=False
        )

    async def test_delete_torrent_error_returns_false(self, qbit_service):
        qbit_service.client.torrents_delete.side_effect = Exception("Error")

        assert await qbit_service.delete_torrent("abc123") is False


class TestAddTorrent:
    async def test_duplicate_response_is_success_when_qbit_has_the_hash(self, qbit_service):
        """Unexpected add responses are success when qBit confirms the hash exists."""
        torrent_hash = "a" * 40
        client = qbit_service.client
        client.torrents_info.side_effect = [[], [_torrent(torrent_hash)]]
        client.torrents_add.return_value = "Fails."

        result = await qbit_service.add_torrent(
            magnet_uri=f"magnet:?xt=urn:btih:{torrent_hash}", category=MediaCategory.MOVIES
        )

        assert result == torrent_hash
        assert client.torrents_info.call_count == 2

    async def test_ok_is_success_when_visibility_lookup_times_out(self, qbit_service):
        """Single add should succeed when qBit accepts before metadata is visible."""
        client = qbit_service.client
        client.torrents_info.side_effect = [[], TimeoutError("delayed")]
        client.torrents_add.return_value = "Ok."

        result = await qbit_service.add_torrent(
            magnet_uri="magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
            category=MediaCategory.MOVIES,
        )

        assert result == "abcdefghijklmnopqrstuvwxyz234567"

    async def test_metadata_pending_is_success(self, qbit_service):
        """qbittorrentapi metadata with pending adds means accepted."""
        client = qbit_service.client
        client.torrents_info.return_value = []
        client.torrents_add.return_value = FakeTorrentsAddedMetadata(
            added_torrent_ids=[], failure_count=0, pending_count=3, success_count=0
        )

        result = await qbit_service.add_torrent(
            magnet_uri="magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
            category=MediaCategory.MOVIES,
        )

        assert result == "abcdefghijklmnopqrstuvwxyz234567"


class TestAddTorrentsBulk:
    @pytest.mark.parametrize("source", ["magnet", "torrent_file"])
    async def test_more_than_seventeen_items_are_sent_in_chunks(
        self, qbit_service, tmp_path, source
    ):
        """Every item is submitted in conservative chunks (10 then 8), even before qBit lists it."""
        client = qbit_service.client
        client.torrents_info.return_value = []
        client.torrents_add.return_value = "Ok."
        payloads = []
        for i in range(18):
            if source == "magnet":
                payloads.append(_magnet_payload(i, f"{i:040x}"))
                continue
            path = tmp_path / f"release-{i}.torrent"
            path.write_bytes(b"not-bencoded-in-test")
            payloads.append(
                BulkTorrentPayload(
                    key=i,
                    title=f"Release {i}",
                    torrent_path=str(path),
                    category=MediaCategory.MOVIES,
                )
            )

        results = await qbit_service.add_torrents_bulk(payloads)

        assert len(results) == 18
        assert all(result.success for result in results)
        assert client.torrents_add.call_count == 2
        first, second = (call.kwargs for call in client.torrents_add.call_args_list)
        if source == "magnet":
            assert (first["urls"].count("\n"), second["urls"].count("\n")) == (9, 7)
        else:
            assert (len(first["torrent_files"]), len(second["torrent_files"])) == (10, 8)
            # Hash is unknown for these files, so Ok. is accepted without a visibility lookup.
            assert all(result.torrent_hash is None for result in results)
            client.torrents_info.assert_not_called()

    @pytest.mark.parametrize(
        ("metadata", "expected_success", "expected_error"),
        [
            ({"failure_count": 0, "pending_count": 3}, True, None),
            ({"failure_count": 1, "pending_count": 1}, False, "qBittorrent reported add failures"),
        ],
        ids=["pending_only_succeeds", "any_failure_fails_chunk"],
    )
    async def test_metadata_response_applies_to_whole_chunk(
        self, qbit_service, metadata, expected_success, expected_error
    ):
        """Metadata has no per-item mapping, so pending accepts and failure rejects the chunk."""
        client = qbit_service.client
        client.torrents_info.return_value = []
        client.torrents_add.return_value = FakeTorrentsAddedMetadata(
            added_torrent_ids=[], success_count=0, **metadata
        )
        payloads = [_magnet_payload(i, f"{i + 1:040x}") for i in range(3)]

        results = await qbit_service.add_torrents_bulk(payloads)

        assert [result.success for result in results] == [expected_success] * 3
        for result in results:
            if expected_error:
                assert expected_error in (result.error or "")
            else:
                assert result.error is None

    async def test_partial_group_failure_leaves_other_group_success(self, qbit_service):
        """A failing option/category group should not fail successful groups."""
        movie_hash, tv_hash = "a" * 40, "b" * 40
        client = qbit_service.client
        client.torrents_info.side_effect = [[], [], [_torrent(movie_hash)]]
        client.torrents_add.side_effect = ["Ok.", RuntimeError("boom")]
        payloads = [
            _magnet_payload("movie", movie_hash),
            _magnet_payload("tv", tv_hash, MediaCategory.TV),
        ]

        results = await qbit_service.add_torrents_bulk(payloads)

        by_key = {result.key: result for result in results}
        assert by_key["movie"].success is True
        assert by_key["movie"].torrent_hash == movie_hash
        assert by_key["tv"].success is False
