"""POST /staged/{id}/check-now: qBittorrent completion triggers a Plex reconcile.

The incomplete-download case is covered in tests/routers/test_staged.py.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.siftarr.config import Settings
from app.siftarr.models.request import RequestStatus
from app.siftarr.routers import staged
from app.siftarr.services.admin.plex_polling_service import CheckRequestResult
from tests.factories import make_request, make_staged_torrent


async def test_check_now_complete_download_reports_plex_availability(db, monkeypatch):
    request = await make_request(db, status=RequestStatus.DOWNLOADING)
    torrent = await make_staged_torrent(
        db, request, magnet_url="magnet:?xt=urn:btih:" + "a" * 40, status="approved"
    )
    await db.commit()

    qbit = AsyncMock()
    qbit.get_torrent_info.return_value = {"progress": 1.0, "state": "uploading"}
    settings = Settings(secret_key="test-only")
    monkeypatch.setattr(staged, "get_settings", lambda: settings)
    monkeypatch.setattr(staged, "QbittorrentService", MagicMock(return_value=qbit))
    reconcile = AsyncMock(
        return_value=CheckRequestResult(
            request_id=request.id,
            matched=True,
            available=True,
            status_before=RequestStatus.DOWNLOADING,
            status_after=RequestStatus.COMPLETED,
            reason="Found on Plex",
        )
    )
    monkeypatch.setattr(staged, "_reconcile_request_via_plex", reconcile)

    response = await staged.check_now(torrent_id=torrent.id, db=db)

    body = json.loads(bytes(response.body))  # type: ignore[arg-type]
    assert body["qbit_complete"] is True
    assert body["plex_available"] is True
    reconcile.assert_awaited_once()


async def test_check_now_unknown_torrent_returns_404(db):
    with pytest.raises(HTTPException) as exc_info:
        await staged.check_now(torrent_id=999, db=db)

    assert exc_info.value.status_code == 404
