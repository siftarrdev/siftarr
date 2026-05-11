"""Tests for staged torrent approval routes."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.siftarr.models import Base, Request, StagedTorrent
from app.siftarr.models.request import MediaType, RequestStatus
from app.siftarr.routers import staged


class TestStagedRouter:
    """Focused tests for staged approval behavior."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_approve_staged_torrent_logs_rule_accept(self, mock_db, monkeypatch):
        """Approving the rule-selected torrent should log a rule_accept decision."""
        torrent = MagicMock()
        torrent.id = 1
        torrent.request_id = 2
        torrent.magnet_url = "magnet:?xt=urn:btih:abc"
        torrent.status = "staged"
        torrent.selection_source = "rule"
        torrent.torrent_path = "/tmp/test.torrent"
        torrent.json_path = "/tmp/test.json"

        request = MagicMock()
        request.id = 2
        request.media_type = MediaType.MOVIE

        rule_result = MagicMock()
        rule_result.scalars.return_value.first.return_value = torrent

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [torrent_result, request_result, rule_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash123"
        lifecycle_service = AsyncMock()
        log_decision = MagicMock()

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", log_decision)
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.approve_staged_torrent(
            1, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        assert torrent.status == "approved"
        lifecycle_service.transition.assert_awaited_once_with(request.id, RequestStatus.DOWNLOADING)
        log_decision.assert_called_once_with(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=torrent,
        )

    @pytest.mark.asyncio
    async def test_approve_staged_torrent_logs_manual_override(self, mock_db, monkeypatch):
        """Approving a manual torrent should log against the current rule-picked torrent."""
        torrent = MagicMock()
        torrent.id = 3
        torrent.request_id = 4
        torrent.magnet_url = "magnet:?xt=urn:btih:def"
        torrent.status = "staged"
        torrent.selection_source = "manual"
        torrent.torrent_path = "/tmp/test2.torrent"
        torrent.json_path = "/tmp/test2.json"

        request = MagicMock()
        request.id = 4
        request.media_type = MediaType.TV

        rule_torrent = MagicMock()
        rule_torrent.id = 5
        rule_torrent.selection_source = "rule"

        rule_result = MagicMock()
        rule_result.scalars.return_value.first.return_value = rule_torrent

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [torrent_result, request_result, rule_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash456"
        lifecycle_service = AsyncMock()
        log_decision = MagicMock()

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", log_decision)
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.approve_staged_torrent(
            3, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        log_decision.assert_called_once_with(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=rule_torrent,
        )

    @pytest.mark.asyncio
    async def test_approve_uses_stored_download_url_when_file_missing(
        self, mock_db, monkeypatch, tmp_path
    ):
        """Staged approvals can use sidecar download URL without local torrent file."""
        json_path = tmp_path / "stage.json"
        json_path.write_text(
            json.dumps({"release": {"download_url": "https://example.test/release.torrent"}})
        )
        torrent = MagicMock()
        torrent.id = 6
        torrent.request_id = 7
        torrent.magnet_url = None
        torrent.status = "staged"
        torrent.selection_source = "rule"
        torrent.torrent_path = str(tmp_path / "missing.torrent")
        torrent.json_path = str(json_path)

        request = MagicMock()
        request.id = 7
        request.media_type = MediaType.MOVIE
        rule_result = MagicMock()
        rule_result.scalars.return_value.first.return_value = torrent
        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        mock_db.execute.side_effect = [torrent_result, request_result, rule_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash789"
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=AsyncMock()))
        monkeypatch.setattr(staged, "log_staging_decision", MagicMock())

        response = await staged.approve_staged_torrent(
            6, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        qbittorrent.add_torrent.assert_awaited_once_with(
            magnet_uri="https://example.test/release.torrent",
            category=staged.MediaCategory.MOVIES,
        )

    @pytest.mark.asyncio
    async def test_approve_overseerr_sync_failure_does_not_block(self, mock_db, monkeypatch):
        """Overseerr approve sync failures should not prevent local approval."""
        torrent = MagicMock()
        torrent.id = 8
        torrent.request_id = 9
        torrent.magnet_url = "magnet:?xt=urn:btih:abc"
        torrent.status = "staged"
        torrent.selection_source = "rule"
        torrent.torrent_path = "/tmp/test.torrent"
        torrent.json_path = "/tmp/test.json"

        request = MagicMock()
        request.id = 9
        request.media_type = MediaType.MOVIE
        request.status = RequestStatus.STAGED
        request.overseerr_request_id = 99

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        rule_result = MagicMock()
        rule_result.scalars.return_value.first.return_value = torrent
        mock_db.execute.side_effect = [torrent_result, request_result, rule_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash789"
        lifecycle_service = AsyncMock()
        sync = AsyncMock(return_value=False)
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", MagicMock())
        monkeypatch.setattr(staged, "approve_overseerr_request_best_effort", sync)
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.approve_staged_torrent(
            8, http_request=MagicMock(headers={}), db=mock_db
        )

        assert response.status_code == 303
        assert torrent.status == "approved"
        lifecycle_service.transition.assert_awaited_once_with(request.id, RequestStatus.DOWNLOADING)
        sync.assert_awaited_once_with(mock_db, request, reason="staged_approval_qbit_sent")

    @pytest.mark.asyncio
    async def test_bulk_staged_action_approves_selected(self, mock_db, monkeypatch):
        """Bulk approve should process multiple staged torrents."""
        torrent_one = MagicMock()
        torrent_one.id = 1
        torrent_one.request_id = 10
        torrent_one.magnet_url = "magnet:?xt=urn:btih:abc"
        torrent_one.status = "staged"
        torrent_one.selection_source = "rule"
        torrent_one.torrent_path = "/tmp/one.torrent"
        torrent_one.json_path = "/tmp/one.json"

        torrent_two = MagicMock()
        torrent_two.id = 2
        torrent_two.request_id = 11
        torrent_two.magnet_url = "magnet:?xt=urn:btih:def"
        torrent_two.status = "staged"
        torrent_two.selection_source = "rule"
        torrent_two.torrent_path = "/tmp/two.torrent"
        torrent_two.json_path = "/tmp/two.json"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent_one, torrent_two]

        request_one = MagicMock()
        request_one.id = 10
        request_one.media_type = MediaType.MOVIE
        request_one.status = RequestStatus.STAGED
        request_two = MagicMock()
        request_two.id = 11
        request_two.media_type = MediaType.MOVIE
        request_two.status = RequestStatus.STAGED
        request_one_result = MagicMock()
        request_one_result.scalar_one_or_none.return_value = request_one
        request_two_result = MagicMock()
        request_two_result.scalar_one_or_none.return_value = request_two
        rule_one_result = MagicMock()
        rule_one_result.scalars.return_value.first.return_value = torrent_one
        rule_two_result = MagicMock()
        rule_two_result.scalars.return_value.first.return_value = torrent_two
        mock_db.execute.side_effect = [
            torrent_result,
            request_one_result,
            rule_one_result,
            request_two_result,
            rule_two_result,
        ]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.side_effect = ["hash1", "hash2"]
        lifecycle_service = AsyncMock()
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "LifecycleService", MagicMock(return_value=lifecycle_service))
        monkeypatch.setattr(staged, "log_staging_decision", MagicMock())
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.bulk_staged_action(
            action="approve",
            torrent_ids=[1, 2],
            http_request=MagicMock(headers={"accept": "application/json"}),
            db=mock_db,
        )

        assert response.status_code == 200
        assert torrent_one.status == "approved"
        assert torrent_two.status == "approved"
        assert lifecycle_service.transition.await_count == 2
        lifecycle_service.transition.assert_any_await(
            request_one.id,
            RequestStatus.DOWNLOADING,
            commit=False,
        )
        lifecycle_service.transition.assert_any_await(
            request_two.id,
            RequestStatus.DOWNLOADING,
            commit=False,
        )
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bulk_staged_action_persists_downloading_and_download_status(self, monkeypatch):
        """Bulk approval should persist approved/downloading rows visible to polling."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with session_maker() as db:
            request_one = Request(
                external_id="bulk-tv-1",
                media_type=MediaType.TV,
                title="Bulk Show One",
                status=RequestStatus.STAGED,
            )
            request_two = Request(
                external_id="bulk-tv-2",
                media_type=MediaType.TV,
                title="Bulk Show Two",
                status=RequestStatus.STAGED,
            )
            db.add_all([request_one, request_two])
            await db.flush()
            db.add_all(
                [
                    StagedTorrent(
                        request_id=request_one.id,
                        torrent_path="/tmp/bulk-one.torrent",
                        json_path="/tmp/bulk-one.json",
                        original_filename="bulk-one.torrent",
                        title="Bulk Show One S01",
                        size=100,
                        indexer="test",
                        score=90,
                        magnet_url="magnet:?xt=urn:btih:1111111111111111111111111111111111111111",
                        selection_source="rule",
                    ),
                    StagedTorrent(
                        request_id=request_two.id,
                        torrent_path="/tmp/bulk-two.torrent",
                        json_path="/tmp/bulk-two.json",
                        original_filename="bulk-two.torrent",
                        title="Bulk Show Two S01",
                        size=200,
                        indexer="test",
                        score=80,
                        magnet_url="magnet:?xt=urn:btih:2222222222222222222222222222222222222222",
                        selection_source="rule",
                    ),
                ]
            )
            await db.commit()

            qbit = AsyncMock()
            qbit.add_torrent.side_effect = ["hash1", "hash2"]
            qbit.get_torrent_info.return_value = None
            commit_spy = AsyncMock(wraps=db.commit)
            monkeypatch.setattr(db, "commit", commit_spy)
            monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
            monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbit))
            monkeypatch.setattr(staged, "log_staging_decision", MagicMock())
            monkeypatch.setattr(staged, "approve_overseerr_request_best_effort", AsyncMock())
            monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

            response = await staged.bulk_staged_action(
                action="approve",
                torrent_ids=[1, 2],
                http_request=MagicMock(headers={"accept": "application/json"}),
                db=db,
            )

            assert response.status_code == 200
            commit_spy.assert_awaited_once()

            request_rows = (await db.execute(select(Request).order_by(Request.id))).scalars().all()
            torrent_rows = (
                (await db.execute(select(StagedTorrent).order_by(StagedTorrent.id))).scalars().all()
            )
            assert [row.status for row in request_rows] == [
                RequestStatus.DOWNLOADING,
                RequestStatus.DOWNLOADING,
            ]
            assert [row.status for row in torrent_rows] == ["approved", "approved"]

            status_response = await staged.get_download_status(db=db)
            body = json.loads(bytes(status_response.body))  # type: ignore[arg-type]
            assert [item["id"] for item in body["torrents"]] == [1, 2]
            assert [item["request_status"] for item in body["torrents"]] == [
                RequestStatus.DOWNLOADING.value,
                RequestStatus.DOWNLOADING.value,
            ]
            assert [item["qbit_progress"] for item in body["torrents"]] == [None, None]

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_bulk_staged_action_discards_selected(self, mock_db, monkeypatch):
        """Bulk discard should process multiple staged torrents."""
        torrent_one = MagicMock()
        torrent_one.id = 3
        torrent_one.request_id = None
        torrent_one.status = "staged"
        torrent_one.torrent_path = "/tmp/three.torrent"
        torrent_one.json_path = "/tmp/three.json"

        torrent_two = MagicMock()
        torrent_two.id = 4
        torrent_two.request_id = None
        torrent_two.status = "staged"
        torrent_two.torrent_path = "/tmp/four.torrent"
        torrent_two.json_path = "/tmp/four.json"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent_one, torrent_two]
        mock_db.execute.return_value = torrent_result

        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.bulk_staged_action(
            action="discard",
            torrent_ids=[3, 4],
            http_request=MagicMock(headers={"accept": "application/json"}),
            db=mock_db,
        )

        assert response.status_code == 200
        assert torrent_one.status == "discarded"
        assert torrent_two.status == "discarded"

    @pytest.mark.asyncio
    async def test_replace_staged_torrent_uses_redirect_to(self, mock_db, monkeypatch):
        """Replacing with a staged candidate should honor redirect_to."""
        new_torrent = MagicMock()
        new_torrent.id = 9
        new_torrent.request_id = 4
        new_torrent.magnet_url = "magnet:?xt=urn:btih:def"
        new_torrent.torrent_path = "/tmp/new.torrent"
        new_torrent.json_path = "/tmp/new.json"
        new_torrent.status = "staged"

        old_torrent = MagicMock()
        old_torrent.id = 10
        old_torrent.request_id = 4
        old_torrent.status = "approved"

        request = MagicMock()
        request.id = 4
        request.media_type = MediaType.TV

        new_result = MagicMock()
        new_result.scalar_one_or_none.return_value = new_torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_torrent
        mock_db.execute.side_effect = [new_result, request_result, old_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash456"
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "log_replacement_decision", MagicMock())
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.replace_staged_torrent(
            torrent_id=9,
            reason="Better quality",
            redirect_to="/?tab=downloading",
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=downloading"
        assert old_torrent.status == "replaced"
        assert new_torrent.status == "approved"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "malicious_redirect",
        [
            "https://evil.example/phish",
            "//evil.example/phish",
            "http:\\evil.example\\phish",
        ],
    )
    async def test_replace_staged_torrent_ignores_malicious_redirect_to(
        self, mock_db, monkeypatch, malicious_redirect
    ):
        """Replacing with a staged candidate should ignore external redirect_to values."""
        new_torrent = MagicMock()
        new_torrent.id = 9
        new_torrent.request_id = 4
        new_torrent.magnet_url = "magnet:?xt=urn:btih:def"
        new_torrent.torrent_path = "/tmp/new.torrent"
        new_torrent.json_path = "/tmp/new.json"
        new_torrent.status = "staged"

        old_torrent = MagicMock()
        old_torrent.id = 10
        old_torrent.request_id = 4
        old_torrent.status = "approved"

        request = MagicMock()
        request.id = 4
        request.media_type = MediaType.TV

        new_result = MagicMock()
        new_result.scalar_one_or_none.return_value = new_torrent
        request_result = MagicMock()
        request_result.scalar_one_or_none.return_value = request
        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_torrent
        mock_db.execute.side_effect = [new_result, request_result, old_result]

        qbittorrent = AsyncMock()
        qbittorrent.add_torrent.return_value = "hash456"
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbittorrent))
        monkeypatch.setattr(staged, "log_replacement_decision", MagicMock())
        monkeypatch.setattr(staged.os.path, "exists", MagicMock(return_value=False))

        response = await staged.replace_staged_torrent(
            torrent_id=9,
            reason="Better quality",
            redirect_to=malicious_redirect,
            db=mock_db,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?tab=staged"

    @pytest.mark.asyncio
    async def test_replace_staged_torrent_rejects_approved_id(self, mock_db):
        """Replace endpoint must receive the staged replacement candidate ID."""
        approved_torrent = MagicMock()
        approved_torrent.status = "approved"

        result = MagicMock()
        result.scalar_one_or_none.return_value = approved_torrent
        mock_db.execute.return_value = result

        with pytest.raises(HTTPException) as exc_info:
            await staged.replace_staged_torrent(
                torrent_id=10,
                reason=None,
                redirect_to="/?tab=downloading",
                db=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Replacement torrent must be staged"


class TestDownloadStatusEndpoint:
    """Tests for GET /staged/download-status."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_approved_torrents(self, mock_db, monkeypatch):
        """Returns empty list when no approved torrents."""
        from app.siftarr.routers.staged import get_download_status

        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = empty_result

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        import json

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert body == {"torrents": []}

    @pytest.mark.asyncio
    async def test_returns_torrent_status(self, mock_db, monkeypatch):
        """Returns torrent list with qbit progress."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        torrent = MagicMock()
        torrent.id = 5
        torrent.title = "Test Movie"
        torrent.request_id = 99
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent]

        request_status_result = MagicMock()
        request_status_result.all.return_value = [(99, RequestStatus.DOWNLOADING)]

        logs_result = MagicMock()
        logs_result.all.return_value = []
        mock_db.execute.side_effect = [torrent_result, request_status_result, logs_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(
            return_value={
                "progress": 0.6,
                "state": "downloading",
                "eta": 120,
                "dlspeed": 2048,
            }
        )
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert len(body["torrents"]) == 1
        assert body["torrents"][0]["id"] == 5
        assert body["torrents"][0]["qbit_progress"] == 0.6
        assert body["torrents"][0]["qbit_progress_percent"] == 60.0
        assert body["torrents"][0]["qbit_eta_seconds"] == 120
        assert body["torrents"][0]["qbit_download_speed"] == 2048
        assert body["torrents"][0]["refresh_staged_tab"] is False

    @pytest.mark.asyncio
    async def test_download_status_keeps_active_torrent_when_qbit_info_missing(
        self, mock_db, monkeypatch
    ):
        """Active approved torrents remain visible even before qBit reports progress."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        torrent = MagicMock()
        torrent.id = 15
        torrent.title = "Missing Progress Movie"
        torrent.request_id = 199
        torrent.magnet_url = "magnet:?xt=urn:btih:ca39a3ee5e6b4b0d3255bfef95601890afd80709"
        torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent]
        request_status_result = MagicMock()
        request_status_result.all.return_value = [(199, RequestStatus.DOWNLOADING)]
        logs_result = MagicMock()
        logs_result.all.return_value = []
        mock_db.execute.side_effect = [torrent_result, request_status_result, logs_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value=None)
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert [item["id"] for item in body["torrents"]] == [15]
        assert body["torrents"][0]["qbit_progress"] is None
        assert body["torrents"][0]["qbit_state"] is None
        assert body["torrents"][0]["request_status"] == RequestStatus.DOWNLOADING.value

    @pytest.mark.asyncio
    async def test_ignores_resolved_request_torrents(self, mock_db, monkeypatch):
        """Approved torrents for available or partial requests should not poll as active."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        active_torrent = MagicMock()
        active_torrent.id = 5
        active_torrent.title = "Still Downloading"
        active_torrent.request_id = 99
        active_torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        active_torrent.status = "approved"

        available_torrent = MagicMock()
        available_torrent.id = 6
        available_torrent.title = "Already Available"
        available_torrent.request_id = 100
        available_torrent.magnet_url = (
            "magnet:?xt=urn:btih:ea39a3ee5e6b4b0d3255bfef95601890afd80709"
        )
        available_torrent.status = "approved"

        partial_torrent = MagicMock()
        partial_torrent.id = 7
        partial_torrent.title = "Partially Available"
        partial_torrent.request_id = 101
        partial_torrent.magnet_url = "magnet:?xt=urn:btih:fa39a3ee5e6b4b0d3255bfef95601890afd80709"
        partial_torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [
            active_torrent,
            available_torrent,
            partial_torrent,
        ]

        request_status_result = MagicMock()
        request_status_result.all.return_value = [
            (99, RequestStatus.DOWNLOADING),
            (100, RequestStatus.COMPLETED),
            (101, RequestStatus.COMPLETED),
        ]

        logs_result = MagicMock()
        logs_result.all.return_value = []
        mock_db.execute.side_effect = [torrent_result, request_status_result, logs_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 0.6, "state": "downloading"})
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)
        assert response.status_code == 200
        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert [torrent["id"] for torrent in body["torrents"]] == [5]
        qbit.get_torrent_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_status_does_not_reconcile_on_get(self, mock_db, monkeypatch):
        """GET download-status should not perform Plex check side effects."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        torrent = MagicMock()
        torrent.id = 5
        torrent.title = "Test Show S01E01"
        torrent.request_id = 99
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        torrent.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [torrent]

        request_status_result = MagicMock()
        request_status_result.all.return_value = [(99, RequestStatus.DOWNLOADING)]

        logs_result = MagicMock()
        logs_result.all.return_value = []
        mock_db.execute.side_effect = [torrent_result, request_status_result, logs_result]

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 1.0, "state": "uploading"})

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert body["torrents"][0]["qbit_complete"] is True
        assert body["torrents"][0]["waiting_for_plex"] is True
        assert body["torrents"][0]["plex_available"] is False
        assert body["torrents"][0]["request_status"] == RequestStatus.DOWNLOADING.value
        assert body["torrents"][0]["refresh_staged_tab"] is False
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_incomplete_sibling_remains_visible_after_download_completed_log(
        self, mock_db, monkeypatch
    ):
        """A sibling completion log must not make a <100% torrent wait-for-Plex/hidden."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import get_download_status

        season_one = MagicMock()
        season_one.id = 11
        season_one.title = "Test Show S01"
        season_one.request_id = 99
        season_one.magnet_url = "magnet:?xt=urn:btih:1111111111111111111111111111111111111111"
        season_one.status = "approved"

        season_two = MagicMock()
        season_two.id = 12
        season_two.title = "Test Show S02"
        season_two.request_id = 99
        season_two.magnet_url = "magnet:?xt=urn:btih:2222222222222222222222222222222222222222"
        season_two.status = "approved"

        torrent_result = MagicMock()
        torrent_result.scalars.return_value.all.return_value = [season_one, season_two]
        request_status_result = MagicMock()
        request_status_result.all.return_value = [(99, RequestStatus.DOWNLOADING)]
        logs_result = MagicMock()
        logs_result.all.return_value = [(99, json.dumps({"done_torrents": [{"torrent_id": 11}]}))]
        mock_db.execute.side_effect = [torrent_result, request_status_result, logs_result]

        qbit = AsyncMock()

        async def get_torrent_info(torrent_hash):
            if torrent_hash == "1111111111111111111111111111111111111111":
                return {"progress": 1.0, "state": "uploading"}
            return {"progress": 0.42, "state": "downloading"}

        qbit.get_torrent_info = AsyncMock(side_effect=get_torrent_info)
        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))

        response = await get_download_status(db=mock_db)

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        by_id = {torrent["id"]: torrent for torrent in body["torrents"]}
        assert sorted(by_id) == [11, 12]
        assert by_id[11]["waiting_for_plex"] is True
        assert by_id[12]["qbit_progress"] == 0.42
        assert by_id[12]["qbit_complete"] is False
        assert by_id[12]["waiting_for_plex"] is False

    @pytest.mark.asyncio
    async def test_reconcile_request_via_plex_closes_service_on_error(self, mock_db, monkeypatch):
        """Targeted check should always close PlexService."""
        from app.siftarr.routers.staged import _reconcile_request_via_plex

        runtime_settings = MagicMock()
        plex_service = AsyncMock()
        plex_polling = AsyncMock()
        plex_polling.check_request = AsyncMock(side_effect=RuntimeError("plex boom"))

        monkeypatch.setattr(staged, "PlexService", MagicMock(return_value=plex_service))
        monkeypatch.setattr(staged, "PlexPollingService", MagicMock(return_value=plex_polling))

        with pytest.raises(RuntimeError, match="plex boom"):
            await _reconcile_request_via_plex(
                mock_db,
                request_id=99,
                title="Test Show S01E01",
                runtime_settings=runtime_settings,
            )


class TestCheckNowEndpoint:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_check_now_does_not_reconcile_incomplete_torrent(self, mock_db, monkeypatch):
        """Incomplete check-now requests should not trigger Plex checks."""
        import json

        from app.siftarr.routers import staged as staged_module
        from app.siftarr.routers.staged import check_now

        torrent = MagicMock()
        torrent.id = 7
        torrent.title = "Test Show S01E01"
        torrent.request_id = 77
        torrent.magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"

        torrent_result = MagicMock()
        torrent_result.scalar_one_or_none.return_value = torrent
        mock_db.execute = AsyncMock(return_value=torrent_result)
        mock_db.commit = AsyncMock()

        qbit = AsyncMock()
        qbit.get_torrent_info = AsyncMock(return_value={"progress": 0.2, "state": "downloading"})
        plex_polling = AsyncMock()

        monkeypatch.setattr(staged, "get_settings", lambda: MagicMock())
        monkeypatch.setattr(staged_module, "QbittorrentService", MagicMock(return_value=qbit))
        monkeypatch.setattr(staged_module, "PlexService", MagicMock(return_value=AsyncMock()))
        monkeypatch.setattr(
            staged_module, "PlexPollingService", MagicMock(return_value=plex_polling)
        )

        response = await check_now(torrent_id=7, db=mock_db)

        body = json.loads(bytes(response.body))  # type: ignore[arg-type]
        assert body["qbit_complete"] is False
        assert body["plex_available"] is False
        plex_polling.check_request.assert_not_called()
