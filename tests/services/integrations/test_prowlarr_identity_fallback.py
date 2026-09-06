from unittest.mock import AsyncMock

from app.siftarr.config import Settings
from app.siftarr.services.integrations.prowlarr_service import (
    ProwlarrRelease,
    ProwlarrSearchResult,
    ProwlarrService,
)


def release(title):
    return ProwlarrRelease(
        title=title,
        size=1024,
        seeders=5,
        leechers=0,
        download_url="https://example.test/torrent",
        indexer="test",
    )


async def test_unrelated_metadata_hit_does_not_suppress_title_fallback(monkeypatch):
    service = ProwlarrService(Settings(secret_key="test-only"))
    wrong = ProwlarrSearchResult(
        releases=[release("Burden of Truth S04E03 From Out the Gloomy Rack")], query_time_ms=10
    )
    correct = ProwlarrSearchResult(releases=[release("FROM S04E03 1080p")], query_time_ms=20)
    search = AsyncMock(side_effect=[wrong, correct])
    monkeypatch.setattr(service, "_search", search)
    result = await service.search_by_tvdbid(123, title="FROM", season=4, episode=3)
    assert result.releases[0].title == "FROM S04E03 1080p"
    assert search.await_count == 2
    assert search.await_args_list[1].args[0]["type"] == "search"
    assert wrong.releases, "A cached metadata result must not be mutated by identity filtering"


async def test_correct_metadata_hit_does_not_trigger_an_extra_search(monkeypatch):
    service = ProwlarrService(Settings(secret_key="test-only"))
    correct = ProwlarrSearchResult(releases=[release("FROM S04E03 1080p")], query_time_ms=20)
    search = AsyncMock(return_value=correct)
    monkeypatch.setattr(service, "_search", search)
    result = await service.search_by_tvdbid(123, title="FROM", season=4, episode=3)
    assert result.releases == correct.releases
    search.assert_awaited_once()
