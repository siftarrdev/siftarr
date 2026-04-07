"""Runtime version helpers backed by git-tag-derived package metadata."""

from __future__ import annotations

import os
import subprocess
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version


def get_version() -> str:
    """Return the application version derived from git tags when available."""
    try:
        return package_version("siftarr")
    except PackageNotFoundError:
        try:
            return subprocess.check_output(
                ["git", "describe", "--dirty", "--tags", "--long", "--match", "v*"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        env_version = os.getenv("SIFTARR_VERSION")
        if env_version:
            return env_version
        return "0.0.0"


__version__ = get_version()
