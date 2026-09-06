"""Safe, bounded extraction of file paths from torrent metainfo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TorrentFileObservation:
    file_paths: tuple[str, ...]

    @property
    def file_count(self) -> int:
        return len(self.file_paths)


class _Decoder:
    def __init__(self, data: bytes, *, max_depth: int, max_items: int) -> None:
        self.data = data
        self.max_depth = max_depth
        self.max_items = max_items
        self.items = 0

    def read(self, pos: int = 0, depth: int = 0):
        if depth > self.max_depth or pos >= len(self.data):
            raise ValueError
        self.items += 1
        if self.items > self.max_items:
            raise ValueError
        ch = self.data[pos : pos + 1]
        if ch == b"i":
            end = self.data.find(b"e", pos)
            if end < 0:
                raise ValueError
            return int(self.data[pos + 1 : end]), end + 1
        if ch.isdigit():
            colon = self.data.find(b":", pos)
            if colon < 0:
                raise ValueError
            length = int(self.data[pos:colon])
            end = colon + 1 + length
            if length < 0 or end > len(self.data):
                raise ValueError
            return self.data[colon + 1 : end], end
        if ch == b"l":
            out, pos = [], pos + 1
            while self.data[pos : pos + 1] != b"e":
                value, pos = self.read(pos, depth + 1)
                out.append(value)
            return out, pos + 1
        if ch == b"d":
            out, pos = {}, pos + 1
            while self.data[pos : pos + 1] != b"e":
                key, pos = self.read(pos, depth + 1)
                if not isinstance(key, bytes):
                    raise ValueError
                if key in out:
                    raise ValueError
                value, pos = self.read(pos, depth + 1)
                out[key] = value
            return out, pos + 1
        raise ValueError


def _safe_component(value: object) -> str | None:
    if not isinstance(value, bytes) or len(value) > 1024:
        return None
    text = value.decode("utf-8", "replace")
    if not text or text in {".", ".."} or "/" in text or "\\" in text or "\x00" in text:
        return None
    return text


def inspect_torrent_metainfo(
    payload: bytes, *, max_bytes: int = 8 * 1024**2, max_files: int = 10_000
) -> TorrentFileObservation | None:
    """Inspect BEP 3 or BEP 52 paths; malformed/oversized payloads are unknown."""
    if not payload or len(payload) > max_bytes:
        return None
    try:
        root, end = _Decoder(payload, max_depth=64, max_items=max_files * 16).read()
        if (
            end != len(payload)
            or not isinstance(root, dict)
            or not isinstance(root.get(b"info"), dict)
        ):
            return None
        info = root[b"info"]
        paths: list[str] = []
        if isinstance(info.get(b"files"), list):
            for entry in info[b"files"]:
                if not isinstance(entry, dict) or not isinstance(entry.get(b"path"), list):
                    return None
                if (
                    not isinstance(entry.get(b"length"), int)
                    or isinstance(entry.get(b"length"), bool)
                    or entry[b"length"] < 0
                ):
                    return None
                parts = [_safe_component(part) for part in entry[b"path"]]
                if not parts or any(part is None for part in parts):
                    return None
                paths.append("/".join(part for part in parts if part is not None))
        elif (
            isinstance(info.get(b"length"), int)
            and not isinstance(info.get(b"length"), bool)
            and info[b"length"] >= 0
        ):
            name = _safe_component(info.get(b"name"))
            if name is None:
                return None
            paths.append(name)
        elif isinstance(info.get(b"file tree"), dict):

            def walk(tree: dict, prefix: tuple[str, ...] = ()) -> None:
                for raw_name, child in tree.items():
                    if raw_name == b"":
                        length = child.get(b"length") if isinstance(child, dict) else None
                        if (
                            prefix
                            and isinstance(length, int)
                            and not isinstance(length, bool)
                            and length >= 0
                        ):
                            paths.append("/".join(prefix))
                            continue
                        raise ValueError
                    name = _safe_component(raw_name)
                    if name is None or not isinstance(child, dict):
                        raise ValueError
                    walk(child, (*prefix, name))

            walk(info[b"file tree"])
        else:
            return None
        if not paths or len(paths) > max_files:
            return None
        return TorrentFileObservation(tuple(paths))
    except IndexError, TypeError, ValueError:
        return None
