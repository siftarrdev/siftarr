"""Shared safe name helpers."""

import re

_UNSAFE_FILENAME_CHARS_RE = re.compile(r"[<>:\"/\\|?*]")
_UNSAFE_PATH_CHARS_RE = re.compile(r"[\\/:*?\"<>|]+")
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORE_RE = re.compile(r"_+")


def safe_staging_filename(title: str) -> str:
    title = _UNSAFE_FILENAME_CHARS_RE.sub("_", title)
    title = _WHITESPACE_RE.sub("_", title)
    title = _UNDERSCORE_RE.sub("_", title)
    return title[:100]


def safe_folder_name(value: str | None, fallback: str) -> str:
    cleaned = _UNSAFE_PATH_CHARS_RE.sub(" ", value or "").strip(" .")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned or fallback
