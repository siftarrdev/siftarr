"""Tests for configuration defaults."""

import os
import stat

import pytest
from pydantic import ValidationError

from app.siftarr.config import (
    PLACEHOLDER_API_KEY,
    Settings,
    default_session_secret_file_path,
    generate_api_key,
)


def test_database_url_defaults_to_data_volume(monkeypatch):
    """Database should default to the shared /data volume."""
    monkeypatch.delenv("SIFTARR_DB_PATH", raising=False)

    settings = Settings()

    assert settings.database_url == "sqlite+aiosqlite:////data/db/siftarr.db"


def test_database_url_honors_override(monkeypatch):
    """Database path override should be supported."""
    monkeypatch.setenv("SIFTARR_DB_PATH", "/tmp/custom.db")

    settings = Settings()

    assert settings.database_url == "sqlite+aiosqlite:////tmp/custom.db"


def test_default_session_secret_file_path_uses_db_directory(monkeypatch, tmp_path):
    db_path = tmp_path / "db" / "siftarr.db"
    monkeypatch.setenv("SIFTARR_DB_PATH", str(db_path))
    monkeypatch.delenv("SIFTARR_SECRET_KEY_FILE", raising=False)

    assert default_session_secret_file_path() == db_path.parent / "session_secret"


def test_secret_key_fallback_is_persisted_and_reused(monkeypatch, tmp_path):
    secret_file = tmp_path / "db" / "session_secret"
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SIFTARR_SECRET_KEY_FILE", str(secret_file))

    first = Settings().secret_key
    second = Settings().secret_key

    assert first == second
    assert secret_file.read_text(encoding="utf-8").strip() == first
    if os.name == "posix":
        assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600


def test_secret_key_fallback_reuses_existing_file(monkeypatch, tmp_path):
    secret_file = tmp_path / "session_secret"
    secret_file.write_text("existing-secret\n", encoding="utf-8")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SIFTARR_SECRET_KEY_FILE", str(secret_file))

    assert Settings().secret_key == "existing-secret"


def test_explicit_secret_key_takes_precedence(monkeypatch, tmp_path):
    secret_file = tmp_path / "session_secret"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("SECRET_KEY", "explicit-secret")
    monkeypatch.setenv("SIFTARR_SECRET_KEY_FILE", str(secret_file))

    assert Settings().secret_key == "explicit-secret"


def test_default_api_key_is_placeholder_constant(monkeypatch):
    monkeypatch.delenv("SIFTARR_API_KEY", raising=False)

    assert Settings().api_key == PLACEHOLDER_API_KEY


def test_auth_enabled_defaults_to_api_auth_disabled_only():
    """Browser Plex SSO gating is enforced separately from API auth_enabled."""
    assert Settings().auth_enabled is False


def test_generate_api_key_is_random_and_not_placeholder():
    first = generate_api_key()
    second = generate_api_key()

    assert first != PLACEHOLDER_API_KEY
    assert second != PLACEHOLDER_API_KEY
    assert first != second
    assert len(first) >= 32


def test_prowlarr_tv_sweep_defaults():
    settings = Settings()

    assert settings.prowlarr_tv_page_size == 100
    assert settings.prowlarr_tv_max_pages_per_query == 6
    assert settings.prowlarr_tv_max_results_per_season == 600
    assert settings.prowlarr_tv_strategy_title_sxx_enabled is True
    assert settings.prowlarr_tv_strategy_imdb_enabled is True
    assert settings.prowlarr_tv_strategy_title_season_token_enabled is True
    assert settings.prowlarr_tv_strategy_tvdb_enabled is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prowlarr_tv_page_size", 0),
        ("prowlarr_tv_page_size", 501),
        ("prowlarr_tv_max_pages_per_query", 0),
        ("prowlarr_tv_max_pages_per_query", 51),
        ("prowlarr_tv_max_results_per_season", 0),
        ("prowlarr_tv_max_results_per_season", 5001),
    ],
)
def test_prowlarr_tv_sweep_rejects_bad_bounds(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_prowlarr_tv_sweep_requires_one_enabled_strategy():
    with pytest.raises(ValidationError):
        Settings(
            prowlarr_tv_strategy_title_sxx_enabled=False,
            prowlarr_tv_strategy_imdb_enabled=False,
            prowlarr_tv_strategy_title_season_token_enabled=False,
            prowlarr_tv_strategy_tvdb_enabled=False,
        )
