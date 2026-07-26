"""Tests for the same-origin qBittorrent Web UI reverse proxy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.siftarr.routers import qbit_proxy

QBIT_URL = "http://192.168.0.201:15080"


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient for an app exposing only the proxy router."""
    monkeypatch.setattr(
        qbit_proxy, "get_settings", lambda: SimpleNamespace(qbittorrent_url=QBIT_URL)
    )
    app = FastAPI()
    app.include_router(qbit_proxy.router)
    return TestClient(app)


def _mock_upstream(monkeypatch, *, status_code=200, headers=None, content=b"", error=None):
    """Install a mocked proxy client and return the request mock for assertions."""
    request_mock = AsyncMock()
    if error is not None:
        request_mock.side_effect = error
    else:
        request_mock.return_value = httpx.Response(
            status_code=status_code,
            headers=headers or {},
            content=content,
        )
    fake_client = MagicMock()
    fake_client.request = request_mock
    monkeypatch.setattr(qbit_proxy, "get_proxy_client", lambda: fake_client)
    return request_mock


def test_get_forwards_path_and_query_and_returns_upstream_body(client, monkeypatch):
    request_mock = _mock_upstream(
        monkeypatch,
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'[{"hash":"abc"}]',
    )

    response = client.get("/qbit/api/v2/torrents/info?filter=downloading")

    assert response.status_code == 200
    assert response.content == b'[{"hash":"abc"}]'
    called_url = request_mock.await_args.args[1]
    assert called_url == f"{QBIT_URL}/api/v2/torrents/info?filter=downloading"
    assert request_mock.await_args.args[0] == "GET"


def test_post_login_forwards_body(client, monkeypatch):
    request_mock = _mock_upstream(monkeypatch, content=b"Ok.")

    response = client.post(
        "/qbit/api/v2/auth/login",
        data={"username": "admin", "password": "secret"},
    )

    assert response.status_code == 200
    assert response.content == b"Ok."
    assert request_mock.await_args.kwargs["content"] == b"username=admin&password=secret"


def test_framing_headers_are_neutralised_but_csp_survives(client, monkeypatch):
    _mock_upstream(
        monkeypatch,
        headers={
            "x-frame-options": "SAMEORIGIN",
            "content-security-policy": "default-src 'self'; frame-ancestors 'self'; img-src *",
        },
    )

    response = client.get("/qbit/")

    assert "x-frame-options" not in response.headers
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors" not in csp
    assert "default-src 'self'" in csp
    assert "img-src *" in csp


def test_set_cookie_is_rescoped_to_proxy_prefix_over_http(client, monkeypatch):
    _mock_upstream(
        monkeypatch,
        headers={"set-cookie": "SID=abc123; path=/; SameSite=Strict; Secure; HttpOnly"},
    )

    response = client.get("/qbit/")

    cookie = response.headers["set-cookie"]
    assert "SID=abc123" in cookie
    assert "Path=/qbit" in cookie
    assert "SameSite=Lax" in cookie
    assert "Strict" not in cookie
    assert "Secure" not in cookie
    assert "HttpOnly" in cookie


def test_base_href_injected_into_html_only(client, monkeypatch):
    _mock_upstream(
        monkeypatch,
        headers={"content-type": "text/html; charset=utf-8"},
        content=b"<html><head><title>qBittorrent</title></head><body></body></html>",
    )

    response = client.get("/qbit/")

    assert '<head><base href="/qbit/">' in response.text


def test_base_href_not_injected_into_json(client, monkeypatch):
    _mock_upstream(
        monkeypatch,
        headers={"content-type": "application/json"},
        content=b'{"head": 1}',
    )

    response = client.get("/qbit/api/v2/app/version")

    assert response.text == '{"head": 1}'


def test_existing_base_tag_is_not_duplicated(client, monkeypatch):
    _mock_upstream(
        monkeypatch,
        headers={"content-type": "text/html"},
        content=b'<html><head><base href="/"></head></html>',
    )

    response = client.get("/qbit/")

    assert response.text.count("<base") == 1


def test_outgoing_origin_and_referer_use_qbittorrent_origin(client, monkeypatch):
    request_mock = _mock_upstream(monkeypatch)

    client.get("/qbit/", headers={"Origin": "http://siftarr.local", "Referer": "http://siftarr/"})

    headers = request_mock.await_args.kwargs["headers"]
    assert headers["origin"] == QBIT_URL
    assert headers["referer"] == f"{QBIT_URL}/"
    assert headers["host"] == "192.168.0.201:15080"


def test_cookies_are_forwarded_upstream(client, monkeypatch):
    request_mock = _mock_upstream(monkeypatch)

    client.get("/qbit/", headers={"Cookie": "SID=abc123"})

    assert request_mock.await_args.kwargs["headers"]["cookie"] == "SID=abc123"


def test_unconfigured_qbittorrent_url_returns_503(monkeypatch):
    monkeypatch.setattr(qbit_proxy, "get_settings", lambda: SimpleNamespace(qbittorrent_url=None))
    app = FastAPI()
    app.include_router(qbit_proxy.router)

    response = TestClient(app).get("/qbit/")

    assert response.status_code == 503
    assert "not configured" in response.text


def test_upstream_connection_error_returns_502(client, monkeypatch):
    _mock_upstream(monkeypatch, error=httpx.ConnectError("refused"))

    response = client.get("/qbit/")

    assert response.status_code == 502
    assert "Unable to reach qBittorrent" in response.text
