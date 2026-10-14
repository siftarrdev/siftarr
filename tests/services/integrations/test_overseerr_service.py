"""Tests for OverseerrService."""

import json

import httpx
import pytest

from app.siftarr.config import Settings
from app.siftarr.services.integrations import overseerr_service as overseerr_module
from app.siftarr.services.integrations.overseerr_service import (
    OverseerrFetchError,
    OverseerrService,
    build_overseerr_media_url,
    build_poster_url,
    extract_poster_path,
)


@pytest.fixture(autouse=True)
def clear_cache_between_tests():
    overseerr_module.clear_media_details_cache()
    yield
    overseerr_module.clear_media_details_cache()


def _unconfigured(url, api_key) -> OverseerrService:
    return OverseerrService(Settings(overseerr_url=url, overseerr_api_key=api_key))


class TestUnconfigured:
    @pytest.mark.parametrize(
        ("url", "api_key"),
        [("", "test"), (None, "test"), ("http://localhost:5055", "")],
        ids=["empty_url", "unset_url", "empty_api_key"],
    )
    async def test_every_call_is_skipped_without_http(self, http_stub, url, api_key):
        """Missing config must short-circuit; an unset URL must not become the string "None"."""
        service = _unconfigured(url, api_key)

        assert service.base_url in ("", "http://localhost:5055")
        assert await service.get_requests() == []
        assert await service.get_request(1) is None
        assert await service.get_media_details("movie", 123) is None
        assert await service.decline_request(1) is False
        assert await service.approve_request(1) is False
        assert http_stub.requests == []

    def test_unset_url_gives_empty_base_url(self):
        assert _unconfigured(None, "test").base_url == ""


class TestGetRequests:
    @pytest.mark.parametrize(
        ("kwargs", "expected_params"),
        [
            ({}, {"take": "100", "skip": "0", "filter": "approved"}),
            ({"status": None, "limit": 50, "skip": 100}, {"take": "50", "skip": "100"}),
        ],
        ids=["default_filter", "no_filter"],
    )
    async def test_success_builds_request(
        self, overseerr_service, http_stub, kwargs, expected_params
    ):
        http_stub.reply(httpx.Response(200, json={"results": [{"id": 1}, {"id": 2}]}))

        result = await overseerr_service.get_requests(**kwargs)

        assert result == [{"id": 1}, {"id": 2}]
        (request,) = http_stub.requests
        assert request.url.path == "/api/v1/request"
        assert dict(request.url.params) == expected_params
        assert request.headers["X-Api-Key"] == "test"

    @pytest.mark.parametrize(
        "outcome",
        [httpx.Response(401), httpx.ConnectError("Network error")],
        ids=["unauthorized", "network_error"],
    )
    async def test_failures_return_empty_list(self, overseerr_service, http_stub, outcome):
        http_stub.reply(outcome)

        assert await overseerr_service.get_requests() == []

    async def test_get_all_requests_paginates(self, overseerr_service, http_stub):
        """get_all_requests aggregates pages until a short page."""
        http_stub.reply(
            httpx.Response(200, json={"results": [{"id": 1}]}),
            httpx.Response(200, json={"results": [{"id": 2}]}),
            httpx.Response(200, json={"results": []}),
        )

        result = await overseerr_service.get_all_requests(status=None, page_size=1)

        assert result == [{"id": 1}, {"id": 2}]

    @pytest.mark.parametrize(
        "failure",
        [httpx.ConnectTimeout("timeout"), httpx.Response(500)],
        ids=["timeout", "http_500"],
    )
    async def test_get_all_requests_raises_when_a_later_page_fails(
        self, overseerr_service, http_stub, failure
    ):
        """A failed page must not be returned as a shorter, complete-looking list."""
        http_stub.reply(httpx.Response(200, json={"results": [{"id": 1}]}), failure)

        with pytest.raises(OverseerrFetchError):
            await overseerr_service.get_all_requests(status=None, page_size=1)

    async def test_get_all_requests_raises_when_page_limit_is_reached(
        self, overseerr_service, http_stub
    ):
        http_stub.reply(
            httpx.Response(200, json={"results": [{"id": 1}]}),
            httpx.Response(200, json={"results": [{"id": 2}]}),
        )

        with pytest.raises(OverseerrFetchError):
            await overseerr_service.get_all_requests(status=None, page_size=1, max_pages=2)

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (httpx.Response(200), True),
            (httpx.Response(404), False),
            (httpx.Response(500), None),
            (httpx.ConnectTimeout("timeout"), None),
        ],
        ids=["found", "missing", "server_error", "timeout"],
    )
    async def test_request_exists_only_reports_missing_on_404(
        self, overseerr_service, http_stub, outcome, expected
    ):
        http_stub.reply(outcome)

        assert await overseerr_service.request_exists(42) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [(2, "pending"), (4, "partially_available"), (5, "available")],
)
def test_normalize_media_status_numeric(status, expected):
    assert OverseerrService.normalize_media_status(status) == expected


@pytest.mark.parametrize(
    ("status", "expected"), [(1, "pending"), (2, "approved"), (5, "completed")]
)
def test_normalize_request_status_numeric(status, expected):
    assert OverseerrService.normalize_request_status(status) == expected


class TestMediaDetails:
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (
                httpx.Response(200, json={"id": 123, "title": "Test Movie"}),
                {"id": 123, "title": "Test Movie"},
            ),
            (httpx.Response(404), None),
        ],
        ids=["found", "not_found"],
    )
    async def test_get_media_details(self, overseerr_service, http_stub, response, expected):
        http_stub.reply(response)

        assert await overseerr_service.get_media_details("movie", 123) == expected
        assert http_stub.requests[0].url.path == "/api/v1/movie/123"

    async def test_uses_ttl_cache(self, overseerr_service, http_stub):
        """Media details should reuse a cached response within the TTL window."""
        http_stub.reply(httpx.Response(200, json={"id": 123, "title": "Test Show"}))

        first = await overseerr_service.get_media_details("tv", 123)
        second = await overseerr_service.get_media_details("tv", 123)

        assert first == second == {"id": 123, "title": "Test Show"}
        assert len(http_stub.requests) == 1

    def test_clear_cache_empties_cache(self):
        overseerr_module._MEDIA_DETAILS_CACHE.update(
            {("tv", 1): (1.0, {"id": 1}), ("movie", 2): (2.0, {"id": 2})}
        )

        assert overseerr_module.clear_media_details_cache() == 2
        assert overseerr_module._MEDIA_DETAILS_CACHE == {}


class TestMediaDetailsCacheBounds:
    @pytest.fixture
    def cached(self, overseerr_service, http_stub, monkeypatch):
        http_stub.default = httpx.Response(200, json={"id": 123})
        monkeypatch.setattr(overseerr_module, "_MEDIA_DETAILS_CACHE_MAX_SIZE", 2)
        return overseerr_service, http_stub

    async def test_evicts_least_recently_used_entry(self, cached):
        service, stub = cached
        await service.get_media_details("tv", 1)
        await service.get_media_details("tv", 2)
        await service.get_media_details("tv", 1)
        assert len(stub.requests) == 2

        await service.get_media_details("tv", 3)
        assert list(overseerr_module._MEDIA_DETAILS_CACHE) == [("tv", 1), ("tv", 3)]
        await service.get_media_details("tv", 2)
        assert len(stub.requests) == 4
        assert len(overseerr_module._MEDIA_DETAILS_CACHE) == 2

    async def test_prunes_expired_entries_even_when_not_requested(self, cached, monkeypatch):
        service, stub = cached
        monkeypatch.setattr(overseerr_module.time, "monotonic", lambda: 100.0)
        await service.get_media_details("tv", 1)
        await service.get_media_details("tv", 2)

        monkeypatch.setattr(overseerr_module.time, "monotonic", lambda: 160.0)
        await service.get_media_details("tv", 3)
        assert list(overseerr_module._MEDIA_DETAILS_CACHE) == [("tv", 3)]
        await service.get_media_details("tv", 1)
        assert len(stub.requests) == 4

    async def test_failed_refresh_does_not_retain_expired_data(self, cached, monkeypatch):
        service, stub = cached
        monkeypatch.setattr(overseerr_module.time, "monotonic", lambda: 100.0)
        await service.get_media_details("tv", 1)
        monkeypatch.setattr(overseerr_module.time, "monotonic", lambda: 161.0)
        stub.default = httpx.Response(503)

        assert await service.get_media_details("tv", 1) is None
        assert not overseerr_module._MEDIA_DETAILS_CACHE


class TestDeclineAndApprove:
    @pytest.mark.parametrize(
        ("kwargs", "status", "expected_ok", "expected_body"),
        [
            ({}, 200, True, {}),
            ({"reason": "Test reason"}, 200, True, {"reason": "Test reason"}),
            ({}, 400, False, {}),
        ],
        ids=["no_reason", "with_reason", "rejected"],
    )
    async def test_decline_request(
        self, overseerr_service, http_stub, kwargs, status, expected_ok, expected_body
    ):
        http_stub.reply(httpx.Response(status))

        assert await overseerr_service.decline_request(123, **kwargs) is expected_ok

        (request,) = http_stub.requests
        assert request.method == "POST"
        assert request.url.path == "/api/v1/request/123/decline"
        assert json.loads(request.content) == expected_body

    @pytest.mark.parametrize(
        ("outcome", "expected_ok"),
        [
            (httpx.Response(200), True),
            (httpx.Response(400), False),
            (httpx.ConnectError("Network error"), False),
        ],
        ids=["ok", "rejected", "network_error"],
    )
    async def test_approve_request(self, overseerr_service, http_stub, outcome, expected_ok):
        http_stub.reply(outcome)

        assert await overseerr_service.approve_request(123) is expected_ok

        (request,) = http_stub.requests
        assert str(request.url) == "http://localhost:5055/api/v1/request/123/approve"
        assert request.headers["X-Api-Key"] == "test"


@pytest.mark.parametrize(
    ("poster", "expected"),
    [
        ("/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg", "/kSf9svfD2WiLhrs9AP2Uih2Wq3T.jpg"),
        ("/images/original/abc.jpg", "/abc.jpg"),
        ("/images/w500/abc.jpg", "/abc.jpg"),
        ("/images/original/", "/"),
        ("/images/", None),
        ("https://image.tmdb.org/t/p/original/abc.jpg", "/abc.jpg"),
        ("https://image.tmdb.org/t/p/w500/abc.jpg", "/abc.jpg"),
        ("https://image.tmdb.org/something/abc.jpg", None),
        ("http://overseerr:5055/images/original/abc.jpg", "/abc.jpg"),
        ("https://overseerr.example.com/images/w500/abc.jpg", "/abc.jpg"),
        (None, None),
        ("", None),
        ("   ", None),
        ("/imagesfoo/bar.jpg", None),
    ],
    ids=[
        "bare_tmdb",
        "proxied_original",
        "proxied_w500",
        "proxied_no_filename",
        "proxied_too_short",
        "tmdb_url",
        "tmdb_url_w500",
        "tmdb_url_no_tp_prefix",
        "overseerr_http_url",
        "overseerr_https_url",
        "none",
        "empty",
        "whitespace",
        "images_prefix_not_proxied",
    ],
)
def test_extract_poster_path(poster, expected):
    assert extract_poster_path(poster) == expected


@pytest.mark.parametrize(
    ("poster", "expected"),
    [
        (None, None),
        ("", None),
        ("/abc123.jpg", "/api/poster?path=%2Fabc123.jpg"),
        ("/images/original/abc.jpg", "/api/poster?path=%2Fabc.jpg"),
        ("https://image.tmdb.org/t/p/original/abc.jpg", "/api/poster?path=%2Fabc.jpg"),
        ("/path with spaces.jpg", "/api/poster?path=%2Fpath%20with%20spaces.jpg"),
    ],
    ids=["none", "empty", "bare", "proxied", "tmdb_url", "url_encoded"],
)
def test_build_poster_url(poster, expected):
    assert build_poster_url(poster) == expected


@pytest.mark.parametrize(
    ("base", "media_type", "tmdb_id", "expected"),
    [
        (None, "movie", 123, None),
        ("", "movie", 123, None),
        ("http://overseerr:5055", "movie", None, None),
        ("http://overseerr:5055", "movie", 123, "http://overseerr:5055/movie/123"),
        ("http://overseerr:5055", "tv", 456, "http://overseerr:5055/tv/456"),
        ("http://overseerr:5055/", "movie", 123, "http://overseerr:5055/movie/123"),
    ],
    ids=["none_url", "empty_url", "none_id", "movie", "tv", "trailing_slash"],
)
def test_build_overseerr_media_url(base, media_type, tmdb_id, expected):
    assert build_overseerr_media_url(base, media_type, tmdb_id) == expected
