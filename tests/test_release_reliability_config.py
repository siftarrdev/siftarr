"""Bounds for release preferences and user-confirmed download recovery."""

import pytest
from pydantic import ValidationError

from app.siftarr.config import Settings


def test_reliability_defaults():
    settings = Settings(secret_key="test-only")
    assert settings.release_file_count_bonus == 5
    assert settings.release_high_file_count_penalty == 10
    assert settings.release_archive_penalty == 100
    assert settings.release_low_seed_penalty == 40
    assert settings.tv_episode_fallback_max_size_gb == 3.0
    assert settings.download_stall_warning_hours == 6
    assert settings.download_stall_recovery_hours == 24


@pytest.mark.parametrize(
    "field",
    [
        "release_file_count_bonus",
        "release_high_file_count_penalty",
        "release_archive_penalty",
        "release_rar_title_penalty",
        "release_low_seed_penalty",
        "release_marginal_seed_penalty",
        "tv_episode_fallback_max_size_gb",
    ],
)
def test_preference_adjustments_cannot_be_negative(field):
    with pytest.raises(ValidationError):
        Settings.model_validate({"secret_key": "test-only", field: -1})


def test_recovery_cannot_precede_warning():
    with pytest.raises(ValidationError, match="must not precede"):
        Settings(
            secret_key="test-only",
            download_stall_warning_hours=12,
            download_stall_recovery_hours=6,
        )


def test_reliability_settings_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("RELEASE_ARCHIVE_PENALTY", "150")
    monkeypatch.setenv("TV_EPISODE_FALLBACK_MAX_SIZE_GB", "0")
    settings = Settings(secret_key="test-only")
    assert settings.release_archive_penalty == 150
    assert settings.tv_episode_fallback_max_size_gb == 0
