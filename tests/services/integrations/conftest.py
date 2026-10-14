"""Shared fixtures for integration service tests."""

from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx
import pytest

from app.siftarr.config import Settings
from app.siftarr.services.integrations import overseerr_service as overseerr_module
from app.siftarr.services.integrations import prowlarr_service as prowlarr_module
from app.siftarr.services.integrations.overseerr_service import OverseerrService
from app.siftarr.services.integrations.prowlarr_service import ProwlarrService
from app.siftarr.services.integrations.qbittorrent_service import QbittorrentService

Outcome = httpx.Response | Exception


class HttpStub:
    """Scripted responses served through an httpx.MockTransport.

    Queued outcomes are served in order. After the queue is empty, the default
    outcome is served. An Exception outcome is raised from the transport.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._queue: list[Outcome] = []
        self.default: Outcome = httpx.Response(200, json={})

    def reply(self, *outcomes: Outcome) -> None:
        self._queue.extend(outcomes)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self._queue.pop(0) if self._queue else self.default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def http_stub(monkeypatch: pytest.MonkeyPatch) -> HttpStub:
    """Route the shared httpx client used by Overseerr and Prowlarr to a stub transport."""
    stub = HttpStub()
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handle))

    async def fake_get_shared_client() -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(overseerr_module, "get_shared_client", fake_get_shared_client)
    monkeypatch.setattr(prowlarr_module, "get_shared_client", fake_get_shared_client)
    return stub


@pytest.fixture
def overseerr_service() -> OverseerrService:
    return OverseerrService(
        Settings(overseerr_url="http://localhost:5055", overseerr_api_key="test")
    )


@pytest.fixture
def prowlarr_service() -> Iterator[ProwlarrService]:
    prowlarr_module.clear_search_cache()
    service = ProwlarrService(
        Settings(prowlarr_url="http://prowlarr.test", prowlarr_api_key="key", secret_key="test")
    )
    yield service
    prowlarr_module.clear_search_cache()


@pytest.fixture
def qbit_service() -> QbittorrentService:
    """Service whose qbittorrentapi client is a MagicMock.

    The service runs client calls through asyncio.to_thread, so configure the
    client methods directly, for example `qbit_service.client.torrents_add.return_value`.
    """
    service = QbittorrentService(
        Settings(qbittorrent_url="http://localhost:8080", qbittorrent_api_key="key")
    )
    service._client = MagicMock()
    return service
