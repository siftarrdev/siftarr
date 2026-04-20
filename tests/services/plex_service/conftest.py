from unittest.mock import MagicMock

import pytest

from app.siftarr.services.plex_service import PlexService


def build_settings(*, concurrency: int | None = None):
    settings = MagicMock()
    settings.plex_url = "http://plex:32400"
    settings.plex_token = "test-token"
    if concurrency is not None:
        settings.plex_sync_concurrency = concurrency
    return settings


@pytest.fixture
def service_factory():
    def factory(*, concurrency: int | None = None):
        return PlexService(settings=build_settings(concurrency=concurrency))

    return factory
