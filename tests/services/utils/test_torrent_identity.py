"""Tests for shared torrent identity helpers."""

import hashlib

from app.siftarr.services.utils.torrent_identity import (
    extract_torrent_hash,
    normalize_torrent_name,
    parse_magnet_info_hash,
    torrent_file_info_hash,
)


def test_parse_magnet_info_hash_supports_hex_and_lowercases() -> None:
    info_hash = "ABCDEF1234567890ABCDEF1234567890ABCDEF12"

    assert parse_magnet_info_hash(f"magnet:?xt=urn:btih:{info_hash}&dn=Name") == info_hash.lower()


def test_parse_magnet_info_hash_supports_base32() -> None:
    info_hash = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

    assert parse_magnet_info_hash(f"magnet:?dn=Name&xt=urn:btih:{info_hash}") == info_hash.lower()


def test_parse_magnet_info_hash_returns_none_for_missing_or_invalid() -> None:
    assert parse_magnet_info_hash(None) is None
    assert parse_magnet_info_hash("magnet:?xt=urn:btih:not-a-valid-hash") is None


def test_torrent_file_info_hash_hashes_raw_info_dictionary(tmp_path) -> None:
    info = b"d6:lengthi123e4:name4:test12:piece lengthi16384e6:pieces20:abcdefghijklmnopqrste"
    torrent_path = tmp_path / "sample.torrent"
    torrent_path.write_bytes(b"d8:announce14:http://tracker4:info" + info + b"e")

    assert torrent_file_info_hash(str(torrent_path)) == hashlib.sha1(info).hexdigest()


def test_torrent_file_info_hash_returns_none_for_unreadable_or_invalid(tmp_path) -> None:
    invalid_path = tmp_path / "invalid.torrent"
    invalid_path.write_bytes(b"not bencoded")

    assert torrent_file_info_hash(str(invalid_path)) is None
    assert torrent_file_info_hash(str(tmp_path / "missing.torrent")) is None
    assert torrent_file_info_hash(None) is None


def test_extract_torrent_hash_prefers_magnet_over_torrent_file(tmp_path) -> None:
    info = b"d4:name4:teste"
    torrent_path = tmp_path / "sample.torrent"
    torrent_path.write_bytes(b"d4:info" + info + b"e")

    assert (
        extract_torrent_hash(
            "magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12",
            str(torrent_path),
        )
        == "abcdef1234567890abcdef1234567890abcdef12"
    )


def test_extract_torrent_hash_falls_back_to_torrent_file(tmp_path) -> None:
    info = b"d4:name4:teste"
    torrent_path = tmp_path / "sample.torrent"
    torrent_path.write_bytes(b"d4:info" + info + b"e")

    assert extract_torrent_hash(None, str(torrent_path)) == hashlib.sha1(info).hexdigest()


def test_normalize_torrent_name_collapses_non_alphanumeric_separators() -> None:
    assert normalize_torrent_name("Movie.Title_2024 [Group]") == "movie title 2024 group"
    assert normalize_torrent_name(None) == ""
