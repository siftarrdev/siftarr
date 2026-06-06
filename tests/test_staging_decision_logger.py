"""Tests for staging decision logging."""

import json
from datetime import UTC, datetime, timedelta

from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.routers import staged
from app.siftarr.services import staging_decision_log


def test_log_staging_decision_records_rule_accept(tmp_path):
    """Approving the rule-selected torrent should log a rule_accept event."""
    log_path = tmp_path / "decision-log.jsonl"
    sidecar_path = tmp_path / "example.json"
    sidecar_path.write_text(
        json.dumps(
            {
                "download_url": "https://indexer/download/full",
                "info_hash": "abc123",
            }
        ),
        encoding="utf-8",
    )
    request = Request(
        title="Example Movie",
        media_type=MediaType.MOVIE,
        tmdb_id=123,
        tvdb_id=None,
        year=2024,
    )
    request.id = 5
    torrent = StagedTorrent(
        request_id=5,
        torrent_path="/tmp/example.torrent",
        json_path=str(sidecar_path),
        original_filename="example",
        title="Example.Movie.2024.1080p",
        size=1_000,
        indexer="Indexer A",
        score=80,
        selection_source="rule",
        status="staged",
    )
    torrent.id = 10

    original_path = staged.STAGING_DECISION_LOG_PATH
    staged.STAGING_DECISION_LOG_PATH = log_path
    try:
        staged.log_staging_decision(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=torrent,
        )
    finally:
        staged.STAGING_DECISION_LOG_PATH = original_path

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "rule_accept"
    assert payload["selected_release"]["selection_source"] == "rule"
    assert payload["selected_release"]["download_url"] == "https://indexer/download/full"
    assert payload["selected_release"]["info_hash"] == "abc123"
    assert payload["selection"]["selection_source"] == "rule"


def test_log_staging_decision_records_manual_override(tmp_path):
    """Approving a manual choice over the rules-selected torrent should log an override."""
    log_path = tmp_path / "decision-log.jsonl"
    request = Request(
        title="Example Show",
        media_type=MediaType.TV,
        tmdb_id=None,
        tvdb_id=456,
        year=2023,
    )
    request.id = 6
    approved_torrent = StagedTorrent(
        request_id=6,
        torrent_path="/tmp/example2.torrent",
        json_path="/tmp/example2.json",
        original_filename="example2",
        title="Example.Show.S01E01.2160p",
        size=2_000,
        indexer="Indexer B",
        score=72,
        selection_source="manual",
        status="staged",
    )
    approved_torrent.id = 11
    rules_selected_torrent = StagedTorrent(
        request_id=6,
        torrent_path="/tmp/example3.torrent",
        json_path="/tmp/example3.json",
        original_filename="example3",
        title="Example.Show.S01E01.1080p",
        size=1_500,
        indexer="Indexer A",
        score=90,
        selection_source="rule",
        status="approved",
    )
    rules_selected_torrent.id = 12

    original_path = staged.STAGING_DECISION_LOG_PATH
    staged.STAGING_DECISION_LOG_PATH = log_path
    try:
        staged.log_staging_decision(
            request=request,
            approved_torrent=approved_torrent,
            rules_selected_torrent=rules_selected_torrent,
        )
    finally:
        staged.STAGING_DECISION_LOG_PATH = original_path

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["event_type"] == "manual_override"
    assert payload["selected_release"]["id"] == approved_torrent.id
    assert payload["rules_selected_release"]["id"] == rules_selected_torrent.id


def test_log_manual_discard_decision_records_rejection(tmp_path):
    """Manually discarding a staged torrent should log a rejection event."""
    log_path = tmp_path / "decision-log.jsonl"
    request = Request(
        title="Rejected Movie",
        media_type=MediaType.MOVIE,
        tmdb_id=789,
        tvdb_id=None,
        year=2025,
    )
    request.id = 7
    torrent = StagedTorrent(
        request_id=7,
        torrent_path="/tmp/rejected.torrent",
        json_path="/tmp/rejected.json",
        original_filename="rejected",
        title="Rejected.Movie.2025.1080p",
        size=3_000,
        indexer="Indexer C",
        score=65,
        selection_source="manual",
        status="discarded",
    )
    torrent.id = 13

    original_path = staged.STAGING_DECISION_LOG_PATH
    staged.STAGING_DECISION_LOG_PATH = log_path
    try:
        staged.log_manual_discard_decision(request=request, rejected_torrent=torrent)
    finally:
        staged.STAGING_DECISION_LOG_PATH = original_path

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["event_type"] == "manual_reject"
    assert payload["outcome"] == "rejected"
    assert payload["request"]["id"] == request.id
    assert payload["selected_release"]["id"] == torrent.id
    assert payload["selected_release"]["category"] == "rejected"
    assert payload["selection"]["reason"] == "Manually discarded"
    assert payload["failures"][0]["rejected_release"]["id"] == torrent.id


def test_normalizes_legacy_and_preserves_full_links():
    legacy = {
        "logged_at": "2026-01-01T00:00:00+00:00",
        "event_type": "rule_accept",
        "request": {"id": 1, "title": "Movie", "media_type": "movie"},
        "approved_torrent": {
            "id": 2,
            "title": "Movie.1080p",
            "selection_source": "rule",
            "download_url": "https://indexer/download/full",
            "magnet_url": "magnet:?xt=urn:btih:abc",
        },
    }

    normalized = staging_decision_log.normalize_entry(legacy)

    assert normalized["schema_version"] == 1
    assert normalized["selected_release"]["download_url"] == "https://indexer/download/full"
    assert normalized["selected_release"]["magnet_url"] == "magnet:?xt=urn:btih:abc"


def test_read_entries_missing_corrupt_and_retention(tmp_path):
    missing = tmp_path / "missing.jsonl"
    assert staging_decision_log.read_entries(missing) == []

    path = tmp_path / "decision-log.jsonl"
    old = (datetime.now(UTC) - timedelta(days=121)).isoformat()
    current = datetime.now(UTC).isoformat()
    path.write_text(
        "not-json\n"
        + json.dumps({"logged_at": old, "event_type": "rule_accept"})
        + "\n"
        + json.dumps({"logged_at": current, "event_type": "rule_accept"})
        + "\n",
        encoding="utf-8",
    )

    entries = staging_decision_log.read_entries(path)

    assert len(entries) == 1
    assert entries[0]["logged_at"] == current
