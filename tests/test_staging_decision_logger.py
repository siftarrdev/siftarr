"""Tests for staging decision logging."""

import json

from app.siftarr.models.request import MediaType, Request
from app.siftarr.models.staged_torrent import StagedTorrent
from app.siftarr.services import staging_decision_logger


def test_log_staging_decision_records_rule_accept(tmp_path):
    """Approving the rule-selected torrent should log a rule_accept event."""
    log_path = tmp_path / "decision-log.jsonl"
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
        json_path="/tmp/example.json",
        original_filename="example",
        title="Example.Movie.2024.1080p",
        size=1_000,
        indexer="Indexer A",
        score=80,
        selection_source="rule",
        status="staged",
    )
    torrent.id = 10

    original_path = staging_decision_logger.STAGING_DECISION_LOG_PATH
    staging_decision_logger.STAGING_DECISION_LOG_PATH = log_path
    try:
        staging_decision_logger.log_staging_decision(
            request=request,
            approved_torrent=torrent,
            rules_selected_torrent=torrent,
        )
    finally:
        staging_decision_logger.STAGING_DECISION_LOG_PATH = original_path

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["event_type"] == "rule_accept"
    assert payload["approved_torrent"]["selection_source"] == "rule"


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

    original_path = staging_decision_logger.STAGING_DECISION_LOG_PATH
    staging_decision_logger.STAGING_DECISION_LOG_PATH = log_path
    try:
        staging_decision_logger.log_staging_decision(
            request=request,
            approved_torrent=approved_torrent,
            rules_selected_torrent=rules_selected_torrent,
        )
    finally:
        staging_decision_logger.STAGING_DECISION_LOG_PATH = original_path

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["event_type"] == "manual_override"
    assert payload["approved_torrent"]["id"] == approved_torrent.id
    assert payload["rules_selected_torrent"]["id"] == rules_selected_torrent.id
