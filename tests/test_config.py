"""Tests for configuration defaults."""

import pytest
from pydantic import ValidationError

from app.siftarr.config import PLACEHOLDER_API_KEY, Settings, generate_api_key


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
