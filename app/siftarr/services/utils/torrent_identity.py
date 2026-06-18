"""Shared torrent identity helpers."""

import hashlib
import re

_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[2-7A-Za-z]{32})", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")


def parse_magnet_info_hash(magnet_uri: str | None) -> str | None:
    """Extract a lower-case BTIH from a magnet URI."""
    if not magnet_uri:
        return None
    match = _BTIH_RE.search(magnet_uri)
    return match.group(1).lower() if match else None


def torrent_file_info_hash(torrent_path: str | None) -> str | None:
    """Compute the SHA1 info hash of a .torrent file."""
    if not torrent_path:
        return None
    try:
        with open(torrent_path, "rb") as f:
            data = f.read()
    except Exception:
        return None
    info_raw = _bencode_extract_info_value(data)
    if info_raw is None:
        return None
    return hashlib.sha1(info_raw).hexdigest()


def extract_torrent_hash(
    magnet_url: str | None = None, torrent_path: str | None = None
) -> str | None:
    """Extract hash from a magnet URI, falling back to a torrent file."""
    return parse_magnet_info_hash(magnet_url) or torrent_file_info_hash(torrent_path)


def normalize_torrent_name(name: str | None) -> str:
    """Normalize separators for loose torrent-name matching."""
    return _NON_ALNUM_RE.sub(" ", name or "").strip().lower()


def _bencode_extract_info_value(data: bytes) -> bytes | None:
    if not data or data[0:1] != b"d":
        return None
    cur = 1
    while cur < len(data):
        if data[cur : cur + 1] == b"e":
            return None
        key, nxt = _bencode_read_string(data, cur)
        if key is None or nxt is None:
            return None
        cur = nxt
        val_start = cur
        nxt = _bencode_skip_value(data, cur)
        if nxt is None:
            return None
        cur = nxt
        if key == b"info":
            return data[val_start:cur]
    return None


def _bencode_read_string(data: bytes, pos: int) -> tuple[bytes | None, int | None]:
    colon = data.find(b":", pos)
    if colon == -1:
        return None, None
    try:
        length = int(data[pos:colon])
    except ValueError:
        return None, None
    start = colon + 1
    end = start + length
    if end > len(data):
        return None, None
    return data[start:end], end


def _bencode_skip_value(data: bytes, pos: int) -> int | None:
    if pos >= len(data):
        return None
    ch = data[pos : pos + 1]
    if ch == b"d":
        cur = pos + 1
        while cur < len(data) and data[cur : cur + 1] != b"e":
            _key, nxt = _bencode_read_string(data, cur)
            if nxt is None:
                return None
            cur = nxt
            nxt = _bencode_skip_value(data, cur)
            if nxt is None:
                return None
            cur = nxt
        return cur + 1
    if ch == b"l":
        cur = pos + 1
        while cur < len(data) and data[cur : cur + 1] != b"e":
            nxt = _bencode_skip_value(data, cur)
            if nxt is None:
                return None
            cur = nxt
        return cur + 1
    if ch == b"i":
        end = data.find(b"e", pos)
        return end + 1 if end != -1 else None
    if ch in b"0123456789":
        _, nxt = _bencode_read_string(data, pos)
        return nxt
    return None
