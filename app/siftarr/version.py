"""Runtime version helpers backed by git-tag-derived package metadata."""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

with suppress(ImportError):
    __version__ = import_module("app.siftarr._version").__version__


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
        except FileNotFoundError, subprocess.CalledProcessError:
            pass

        env_version = os.getenv("SIFTARR_VERSION")
        if env_version:
            return env_version
        return "0.0.0"


if "__version__" not in globals():
    __version__ = get_version()
