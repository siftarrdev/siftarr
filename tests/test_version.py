"""Tests for application version resolution."""

import subprocess
from unittest.mock import Mock

from app.siftarr import version as version_module


def test_get_version_uses_installed_package_metadata(monkeypatch):
    """Installed package metadata should be the primary version source."""
    monkeypatch.delenv("SIFTARR_VERSION", raising=False)
    monkeypatch.setattr(version_module, "package_version", Mock(return_value="0.2.0"))

    assert version_module.get_version() == "0.2.0"


def test_get_version_falls_back_to_git_describe(monkeypatch):
    """A source checkout should resolve its version directly from git tags."""
    monkeypatch.delenv("SIFTARR_VERSION", raising=False)
    monkeypatch.setattr(
        version_module,
        "package_version",
        Mock(side_effect=version_module.PackageNotFoundError),
    )
    monkeypatch.setattr(version_module.subprocess, "check_output", Mock(return_value="v1.2.3\n"))

    assert version_module.get_version() == "v1.2.3"


def test_get_version_falls_back_to_environment_variable(monkeypatch):
    """Env fallback should support explicit version injection when metadata is unavailable."""
    monkeypatch.setenv("SIFTARR_VERSION", "v1.2.3")
    monkeypatch.setattr(
        version_module, "package_version", Mock(side_effect=version_module.PackageNotFoundError)
    )
    monkeypatch.setattr(
        version_module.subprocess,
        "check_output",
        Mock(side_effect=subprocess.CalledProcessError(1, ["git"])),
    )

    assert version_module.get_version() == "v1.2.3"
