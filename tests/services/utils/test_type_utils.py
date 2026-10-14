"""Unit tests for app.siftarr.services.utils.type_utils."""

import pytest

from app.siftarr.services.utils.type_utils import (
    coerce_int_list,
    normalize_float,
    normalize_int,
    normalize_optional_text,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hello", "hello"),
        ("", ""),
        (None, None),
        (42, None),
        (3.14, None),
        ([1, 2], None),
        (True, None),
        ({"a": 1}, None),
    ],
)
def test_normalize_optional_text(value: object, expected: str | None) -> None:
    assert normalize_optional_text(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5.0),
        (-3, -3.0),
        (3.14, 3.14),
        ("2.7", 2.7),
        ("10", 10.0),
        ("abc", 0.0),
        ("", 0.0),
        (None, 0.0),
        ([1], 0.0),
        (True, 1.0),
        (False, 0.0),
    ],
)
def test_normalize_float(value: object, expected: float) -> None:
    assert normalize_float(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, 42),
        (-7, -7),
        (3.9, 3),
        ("123", 123),
        ("xyz", 0),
        (None, 0),
        ([1], 0),
        (True, 1),
        (False, 0),
    ],
)
def test_normalize_int(value: object, expected: int) -> None:
    result = normalize_int(value)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([1, 2, 3], [1, 2, 3]),
        ([1, "two", 3, None, 4.5], [1, 3]),
        ([], []),
        (None, []),
        ("not a list", []),
        ({"a": 1}, []),
        (42, []),
    ],
)
def test_coerce_int_list(value: object, expected: list[int]) -> None:
    assert coerce_int_list(value) == expected
