from app.siftarr.services.utils.torrent_metainfo import inspect_torrent_metainfo


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b"e"
    raise TypeError


def test_inspects_bep3_multi_and_single_file_torrents():
    multi = bencode({b"info": {b"files": [{b"length": 3, b"path": [b"x", b"a.rar"]}]}})
    observed = inspect_torrent_metainfo(multi)
    assert observed is not None and observed.file_paths == ("x/a.rar",)
    single = bencode({b"info": {b"length": 3, b"name": b"movie.mkv"}})
    observed = inspect_torrent_metainfo(single)
    assert observed is not None and observed.file_paths == ("movie.mkv",)


def test_inspects_bep52_file_tree():
    payload = bencode({b"info": {b"file tree": {b"dir": {b"movie.mkv": {b"": {b"length": 3}}}}}})
    observed = inspect_torrent_metainfo(payload)
    assert observed is not None and observed.file_paths == ("dir/movie.mkv",)


def test_malformed_unsafe_and_oversized_are_unknown():
    assert inspect_torrent_metainfo(b"not bencode") is None
    unsafe = bencode({b"info": {b"length": 1, b"name": b"../movie.mkv"}})
    assert inspect_torrent_metainfo(unsafe) is None
    assert inspect_torrent_metainfo(b"x" * 100, max_bytes=10) is None


def test_rejects_missing_or_negative_lengths_and_duplicate_keys():
    missing = bencode({b"info": {b"files": [{b"path": [b"movie.mkv"]}]}})
    negative = bencode({b"info": {b"files": [{b"length": -1, b"path": [b"movie.mkv"]}]}})
    single_negative = bencode({b"info": {b"length": -1, b"name": b"movie.mkv"}})
    duplicate = b"d4:infod4:name1:a4:name1:bee"
    assert inspect_torrent_metainfo(missing) is None
    assert inspect_torrent_metainfo(negative) is None
    assert inspect_torrent_metainfo(single_negative) is None
    assert inspect_torrent_metainfo(duplicate) is None


def test_rejects_invalid_v2_empty_root_and_lengths():
    empty_root = bencode({b"info": {b"file tree": {b"": {b"length": 1}}}})
    missing = bencode({b"info": {b"file tree": {b"movie.mkv": {b"": {}}}}})
    negative = bencode({b"info": {b"file tree": {b"movie.mkv": {b"": {b"length": -1}}}}})
    assert inspect_torrent_metainfo(empty_root) is None
    assert inspect_torrent_metainfo(missing) is None
    assert inspect_torrent_metainfo(negative) is None


def test_file_count_bound_is_enforced():
    payload = bencode(
        {b"info": {b"files": [{b"length": 1, b"path": [str(i).encode()]} for i in range(3)]}}
    )
    assert inspect_torrent_metainfo(payload, max_files=2) is None
    observed = inspect_torrent_metainfo(payload, max_files=3)
    assert observed is not None and observed.file_count == 3


def test_decoder_depth_and_item_limits_are_enforced():
    nested: dict[bytes, object] = {b"length": 1}
    for _ in range(70):
        nested = {b"folder": nested}
    assert inspect_torrent_metainfo(bencode({b"info": {b"file tree": nested}})) is None
    many_items = bencode(
        {b"info": {b"length": 1, b"name": b"movie.mkv"}, b"extra": list(range(100))}
    )
    assert inspect_torrent_metainfo(many_items, max_files=1) is None
