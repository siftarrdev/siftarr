"""Tests for staging decision logging."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import staging_decision_log


@pytest.fixture
def log_path(monkeypatch, tmp_path):
    path = tmp_path / "decision-log.jsonl"
    monkeypatch.setattr(staging_decision_log, "STAGING_DECISION_LOG_PATH", path)
    return path


def _request(request_id: int = 5) -> Request:
    request = Request(
        title="Example Movie", media_type=MediaType.MOVIE, tmdb_id=123, tvdb_id=None, year=2024
    )
    request.id = request_id
    return request


def _torrent(torrent_id: int, **overrides) -> StagedTorrent:
    fields = {
        "request_id": 5,
        "torrent_path": "/tmp/example.torrent",
        "json_path": "/tmp/example.json",
        "original_filename": "example",
        "title": "Example.Movie.2024.1080p",
        "size": 1_000,
        "indexer": "Indexer A",
        "score": 80,
        "selection_source": "rule",
        "status": "staged",
    }
    torrent = StagedTorrent(**(fields | overrides))
    torrent.id = torrent_id
    return torrent


def _logged(log_path) -> dict:
    return json.loads(log_path.read_text(encoding="utf-8").strip())


def test_log_staging_decision_records_rule_accept(log_path, tmp_path):
    """Approving the rule-selected torrent should log a rule_accept event."""
    sidecar_path = tmp_path / "example.json"
    sidecar_path.write_text(
        json.dumps({"download_url": "https://indexer/download/full", "info_hash": "abc123"}),
        encoding="utf-8",
    )
    torrent = _torrent(10, json_path=str(sidecar_path))

    staging_decision_log.log_staging_decision(
        request=_request(), approved_torrent=torrent, rules_selected_torrent=torrent
    )

    payload = _logged(log_path)
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "rule_accept"
    assert payload["selected_release"]["selection_source"] == "rule"
    assert payload["selected_release"]["download_url"] == "https://indexer/download/full"
    assert payload["selected_release"]["info_hash"] == "abc123"
    assert payload["selection"]["selection_source"] == "rule"


def test_log_staging_decision_records_manual_override(log_path):
    """Approving a manual choice over the rules-selected torrent should log an override."""
    approved = _torrent(11, selection_source="manual", score=72)
    rules_selected = _torrent(12, status="approved", score=90)

    staging_decision_log.log_staging_decision(
        request=_request(), approved_torrent=approved, rules_selected_torrent=rules_selected
    )

    payload = _logged(log_path)
    assert payload["event_type"] == "manual_override"
    assert payload["selected_release"]["id"] == approved.id
    assert payload["rules_selected_release"]["id"] == rules_selected.id


def test_log_manual_discard_decision_records_rejection(log_path):
    """Manually discarding a staged torrent should log a rejection event."""
    request = _request(7)
    torrent = _torrent(13, request_id=7, selection_source="manual", status="discarded")

    staging_decision_log.log_manual_discard_decision(request=request, rejected_torrent=torrent)

    payload = _logged(log_path)
    assert payload["event_type"] == "manual_reject"
    assert payload["outcome"] == "rejected"
    assert payload["request"]["id"] == request.id
    assert payload["selected_release"]["id"] == torrent.id
    assert payload["selected_release"]["category"] == "rejected"
    assert payload["selection"]["reason"] == "Manually discarded"
    assert payload["failures"][0]["rejected_release"]["id"] == torrent.id


def test_read_entries_missing_corrupt_and_retention(tmp_path):
    missing = tmp_path / "missing.jsonl"
    assert staging_decision_log.read_entries(missing) == []

    path = tmp_path / "decision-log.jsonl"
    old = (datetime.now(UTC) - timedelta(days=121)).isoformat()
    current = datetime.now(UTC).isoformat()
    path.write_text(
        "not-json\n"
        + json.dumps({"logged_at": datetime.now(UTC).isoformat(), "event_type": "legacy"})
        + "\n"
        + json.dumps({"schema_version": 1, "logged_at": old, "event_type": "rule_accept"})
        + "\n"
        + json.dumps({"schema_version": 1, "logged_at": current, "event_type": "rule_accept"})
        + "\n",
        encoding="utf-8",
    )

    entries = staging_decision_log.read_entries(path)

    assert len(entries) == 1
    assert entries[0]["logged_at"] == current
