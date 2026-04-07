"""Tests for staging decision logging."""

import json

from app.arbitratarr.models.request import MediaType
from app.arbitratarr.services import staging_decision_logger


def test_log_staging_decision_records_rule_accept(tmp_path):
    """Approving the rule-selected torrent should log a rule_accept event."""
    log_path = tmp_path / "decision-log.jsonl"
    request = type(
        "RequestRecord",
        (),
        {
            "id": 5,
            "title": "Example Movie",
            "media_type": MediaType.MOVIE,
            "tmdb_id": 123,
            "tvdb_id": None,
            "year": 2024,
        },
    )()
    torrent = type(
        "StagedRecord",
        (),
        {
            "id": 10,
            "title": "Example.Movie.2024.1080p",
            "score": 80,
            "size": 1_000,
            "indexer": "Indexer A",
            "status": "staged",
            "selection_source": "rule",
        },
    )()

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
    request = type(
        "RequestRecord",
        (),
        {
            "id": 6,
            "title": "Example Show",
            "media_type": MediaType.TV,
            "tmdb_id": None,
            "tvdb_id": 456,
            "year": 2023,
        },
    )()
    approved_torrent = type(
        "ApprovedRecord",
        (),
        {
            "id": 11,
            "title": "Example.Show.S01E01.2160p",
            "score": 72,
            "size": 2_000,
            "indexer": "Indexer B",
            "status": "staged",
            "selection_source": "manual",
        },
    )()
    rules_selected_torrent = type(
        "RulesRecord",
        (),
        {
            "id": 12,
            "title": "Example.Show.S01E01.1080p",
            "score": 90,
            "size": 1_500,
            "indexer": "Indexer A",
            "status": "approved",
            "selection_source": "rule",
        },
    )()

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
