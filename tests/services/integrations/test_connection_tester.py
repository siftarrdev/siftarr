"""Tests for ConnectionTester."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import qbittorrentapi

from app.siftarr.services.integrations.connection_tester import ConnectionTester

HTTP_SERVICES = ["overseerr", "prowlarr"]
ALL_SERVICES = ["overseerr", "prowlarr", "qbittorrent"]


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.overseerr_url = "http://localhost:5055"
    settings.overseerr_api_key = "test_key"
    settings.prowlarr_url = "http://localhost:9696"
    settings.prowlarr_api_key = "test_key"
    settings.qbittorrent_url = "http://localhost:8080"
    settings.qbittorrent_api_key = "qbt_test_key"
    return settings


def _run(service: str, settings):
    return getattr(ConnectionTester, f"test_{service}")(settings)


@contextmanager
def patched_http(*, response=None, error=None):
    client = AsyncMock()
    client.get = AsyncMock(return_value=response, side_effect=error)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    with patch("httpx.AsyncClient", return_value=client):
        yield


def _response(status_code: int, payload: dict | None = None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ALL_SERVICES)
@pytest.mark.parametrize(
    ("field", "expected"),
    [("url", "url is not configured"), ("api_key", "api key is not configured")],
    ids=["no_url", "no_api_key"],
)
async def test_missing_config_fails_without_network(mock_settings, service, field, expected):
    setattr(mock_settings, f"{service}_{field}", "")

    result = await _run(service, mock_settings)

    assert result.success is False
    assert expected in result.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("service,version", [("overseerr", "1.0.0"), ("prowlarr", "2.0.0")])
async def test_http_service_success_reports_version(mock_settings, service, version):
    with patched_http(response=_response(200, {"version": version})):
        result = await _run(service, mock_settings)

    assert result.success is True
    assert result.details is not None
    assert version in result.details


@pytest.mark.asyncio
@pytest.mark.parametrize("service", HTTP_SERVICES)
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"response": _response(401)}, "authentication failed"),
        ({"response": _response(500)}, "http error: 500"),
        ({"error": httpx.TimeoutException("Timeout")}, "timeout"),
        ({"error": httpx.RequestError("Connection refused")}, "connection failed"),
    ],
    ids=["unauthorized", "http_500", "timeout", "request_error"],
)
async def test_http_service_failure_modes(mock_settings, service, kwargs, expected):
    with patched_http(**kwargs):
        result = await _run(service, mock_settings)

    assert result.success is False
    assert expected in result.message.lower()


@pytest.mark.asyncio
async def test_qbittorrent_success_reports_api_version(mock_settings):
    client = MagicMock()
    client.app.web_api_version = "v2.0"

    with (
        patch("qbittorrentapi.Client", return_value=client),
        patch("asyncio.to_thread", AsyncMock()),
    ):
        result = await ConnectionTester.test_qbittorrent(mock_settings)

    assert result.success is True
    assert result.details is not None
    assert "Web API Version" in result.details


@pytest.mark.asyncio
async def test_qbittorrent_succeeds_when_version_lookup_fails(mock_settings):
    with (
        patch("qbittorrentapi.Client", return_value=MagicMock()),
        patch("asyncio.to_thread", AsyncMock(side_effect=Exception("Failed"))),
    ):
        result = await ConnectionTester.test_qbittorrent(mock_settings)

    assert result.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (qbittorrentapi.LoginFailed("Invalid"), "authentication failed"),
        (Exception("Connection refused"), "connection failed"),
    ],
    ids=["login_failed", "connection_error"],
)
async def test_qbittorrent_failure_modes(mock_settings, error, expected):
    with patch("qbittorrentapi.Client", side_effect=error):
        result = await ConnectionTester.test_qbittorrent(mock_settings)

    assert result.success is False
    assert expected in result.message.lower()
