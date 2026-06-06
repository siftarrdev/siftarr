"""Tests for staged decision-log API."""

import json
import os
from base64 import b64encode
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.siftarr.config import reload_settings
from app.siftarr.main import create_app
from app.siftarr.routers import auth_router, staged
from app.siftarr.routers import settings as settings_router


class MemorySettingsStore:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        return None

    async def load_into_environ(self) -> None:
        return None

    async def ensure_runtime_api_key(self) -> None:
        return None


async def _fake_db() -> AsyncGenerator[MagicMock]:
    yield MagicMock()


@pytest.fixture
def client(monkeypatch, tmp_path) -> Iterator[TestClient]:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SIFTARR_API_KEY", "valid-api-key")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    reload_settings()
    monkeypatch.setattr(auth_router, "SettingsStore", MemorySettingsStore)
    monkeypatch.setattr(settings_router, "SettingsStore", MemorySettingsStore)
    monkeypatch.setattr(staged, "STAGING_DECISION_LOG_PATH", tmp_path / "decision-log.jsonl")
    app = create_app()
    app.dependency_overrides[auth_router.get_db] = _fake_db
    app.dependency_overrides[settings_router.get_db] = _fake_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        os.environ.pop("PLEX_CLAIMED_ID", None)
        reload_settings()


def _write_log(*entries: dict) -> None:
    staged.STAGING_DECISION_LOG_PATH.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\nnot-json\n",
        encoding="utf-8",
    )


def _entry(idx: int, **overrides) -> dict:
    payload = {
        "schema_version": 1,
        "logged_at": (datetime.now(UTC) - timedelta(days=idx)).isoformat(),
        "event_type": "rule_accept",
        "outcome": "staged",
        "request": {"id": idx, "title": f"Title {idx}", "media_type": "movie"},
        "selection": {"selection_source": "rule", "source": "rule"},
        "selected_release": {
            "title": f"Release {idx}",
            "download_url": "https://full/link",
            "magnet_url": "magnet:?xt=urn:btih:abc",
        },
        "top_candidates": [],
        "all_candidates": [
            {"title": f"Release {idx}", "matches": [{"rule_name": "Preferred Quality"}]}
        ],
        "failures": [],
        "counts": {},
        "indexer_stats": {},
        "search_context": {},
    }
    payload.update(overrides)
    return payload


def test_session_or_api_key_allowed_and_empty_log(client):
    assert client.get("/staged/decision-log").status_code == 401

    session_data = b64encode(json.dumps({"plex_user_id": "admin-id"}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner("test-secret").sign(session_data).decode("utf-8"))
    session_response = client.get("/staged/decision-log")
    assert session_response.status_code == 200
    assert session_response.json()["items"] == []

    response = client.get("/staged/decision-log", headers={"X-API-Key": "valid-api-key"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_api_key_required_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    reload_settings()

    assert client.get("/staged/decision-log").status_code == 401
    assert client.get("/staged/decision-log", headers={"X-API-Key": "wrong"}).status_code == 401
    assert (
        client.get("/staged/decision-log", headers={"X-API-Key": "valid-api-key"}).status_code
        == 200
    )

    session_data = b64encode(json.dumps({"plex_user_id": "admin-id"}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner("test-secret").sign(session_data).decode("utf-8"))
    assert client.get("/staged/decision-log").status_code == 200


def test_pagination_filters_legacy_links_corrupt_and_retention(client):
    old = _entry(121, request={"id": 99, "title": "Old", "media_type": "movie"})
    tv = _entry(
        1,
        event_type="manual_override",
        outcome="approved",
        request={"id": 2, "title": "Great Show", "media_type": "tv"},
        selection={"selection_source": "manual", "source": "manual"},
    )
    legacy = {
        "logged_at": datetime.now(UTC).isoformat(),
        "event_type": "replacement",
        "request": {"id": 3, "title": "Legacy Movie", "media_type": "movie"},
        "new_torrent": {"title": "Legacy Release", "selection_source": "rule"},
        "reason": "better",
    }
    _write_log(old, tv, legacy, _entry(0))

    headers = {"X-API-Key": "valid-api-key"}
    page = client.get("/staged/decision-log?page_size=1", headers=headers).json()
    assert page["total"] == 3
    assert page["has_next"] is True

    assert client.get("/staged/decision-log?media_type=tv", headers=headers).json()["total"] == 1
    assert (
        client.get("/staged/decision-log?event_type=replacement", headers=headers).json()["total"]
        == 1
    )
    assert (
        client.get("/staged/decision-log?selection_source=manual", headers=headers).json()["total"]
        == 1
    )
    assert client.get("/staged/decision-log?request_id=3", headers=headers).json()["total"] == 1
    assert client.get("/staged/decision-log?title=great", headers=headers).json()["total"] == 1
    assert (
        client.get("/staged/decision-log?rule_name=preferred", headers=headers).json()["total"] == 2
    )
    assert client.get("/staged/decision-log?outcome=approved", headers=headers).json()["total"] == 1

    item = client.get("/staged/decision-log?request_id=0", headers=headers).json()["items"][0]
    assert item["selected_release"]["download_url"] == "https://full/link"
    assert item["selected_release"]["magnet_url"].startswith("magnet:")
