"""Tests for configuration defaults."""

from app.arbitratarr.config import Settings


def test_database_url_defaults_to_data_volume(monkeypatch):
    """Database should default to the shared /data volume."""
    monkeypatch.delenv("ARBITRATARR_DB_PATH", raising=False)

    settings = Settings()

    assert settings.database_url == "sqlite+aiosqlite:////data/db/arbitratarr.db"


def test_database_url_honors_override(monkeypatch):
    """Database path override should be supported."""
    monkeypatch.setenv("ARBITRATARR_DB_PATH", "/tmp/custom.db")

    settings = Settings()

    assert settings.database_url == "sqlite+aiosqlite:////tmp/custom.db"
