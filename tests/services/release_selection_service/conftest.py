from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.models.request import MediaType, Request, RequestStatus


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def request_record():
    request = MagicMock(spec=Request)
    request.id = 7
    request.media_type = MediaType.MOVIE
    request.status = RequestStatus.PENDING
    return request


@pytest.fixture
def selected_release():
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
