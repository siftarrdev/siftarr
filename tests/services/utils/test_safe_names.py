"""Tests for shared safe-name helpers."""

from app.siftarr.services.utils.safe_names import safe_folder_name, safe_staging_filename


def test_safe_staging_filename_replaces_unsafe_chars_and_whitespace() -> None:
    assert safe_staging_filename('Bad / Name: "TV"  Episode?') == "Bad_Name_TV_Episode_"


def test_safe_staging_filename_collapses_underscores_and_truncates() -> None:
    assert safe_staging_filename("A__B   C") == "A_B_C"
    assert len(safe_staging_filename("a" * 101)) == 100


def test_safe_folder_name_replaces_path_chars_and_normalizes_whitespace() -> None:
    assert safe_folder_name(' Bad / Name: "TV"  Episode? ', "Fallback") == "Bad Name TV Episode"


def test_safe_folder_name_strips_dots_spaces_and_uses_fallback() -> None:
    assert safe_folder_name(" ... ", "Fallback") == "Fallback"
    assert safe_folder_name(None, "Fallback") == "Fallback"
