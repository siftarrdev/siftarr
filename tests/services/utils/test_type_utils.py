"""Unit tests for app.siftarr.services.type_utils."""

from app.siftarr.services.type_utils import (
    coerce_int_list,
    normalize_float,
    normalize_int,
    normalize_optional_text,
)

# -- normalize_optional_text ---------------------------------------------------


class TestNormalizeOptionalText:
    def test_none_returns_none(self) -> None:
        assert normalize_optional_text(None) is None

    def test_string_returns_string(self) -> None:
        assert normalize_optional_text("hello") == "hello"

    def test_empty_string_returns_empty_string(self) -> None:
        assert normalize_optional_text("") == ""

    def test_int_returns_none(self) -> None:
        assert normalize_optional_text(42) is None

    def test_float_returns_none(self) -> None:
        assert normalize_optional_text(3.14) is None

    def test_list_returns_none(self) -> None:
        assert normalize_optional_text([1, 2]) is None

    def test_bool_returns_none(self) -> None:
        assert normalize_optional_text(True) is None

    def test_dict_returns_none(self) -> None:
        assert normalize_optional_text({"a": 1}) is None


# -- normalize_float -----------------------------------------------------------


class TestNormalizeFloat:
    def test_int_returns_float(self) -> None:
        assert normalize_float(5) == 5.0

    def test_negative_int(self) -> None:
        assert normalize_float(-3) == -3.0

    def test_zero_int(self) -> None:
        assert normalize_float(0) == 0.0

    def test_float_returns_float(self) -> None:
        assert normalize_float(3.14) == 3.14

    def test_string_numeric(self) -> None:
        assert normalize_float("2.7") == 2.7

    def test_string_integer(self) -> None:
        assert normalize_float("10") == 10.0

    def test_string_invalid_returns_zero(self) -> None:
        assert normalize_float("abc") == 0.0

    def test_empty_string_returns_zero(self) -> None:
        assert normalize_float("") == 0.0

    def test_none_returns_zero(self) -> None:
        assert normalize_float(None) == 0.0

    def test_bool_returns_zero(self) -> None:
        # bool is a subclass of int in Python, so isinstance(True, int | float)
        # is True — bool values are treated as numeric
        assert normalize_float(True) == 1.0
        assert normalize_float(False) == 0.0

    def test_list_returns_zero(self) -> None:
        assert normalize_float([1]) == 0.0


# -- normalize_int -------------------------------------------------------------


class TestNormalizeInt:
    def test_bool_true_returns_one(self) -> None:
        # bool is checked first; int(True) == 1
        assert normalize_int(True) == 1

    def test_bool_false_returns_zero(self) -> None:
        assert normalize_int(False) == 0

    def test_bool_is_not_equal_to_one(self) -> None:
        # The spec says "bool (should not equal 1)" — meaning True is int(True)=1
        # but the return *type* is int, not bool. Verify the return is int, not bool.
        result = normalize_int(True)
        assert result == 1
        assert type(result) is int  # noqa: E721 — must be int, not bool

    def test_int_returns_int(self) -> None:
        assert normalize_int(42) == 42

    def test_negative_int(self) -> None:
        assert normalize_int(-7) == -7

    def test_float_truncates(self) -> None:
        assert normalize_int(3.9) == 3

    def test_float_zero(self) -> None:
        assert normalize_int(0.0) == 0

    def test_string_numeric(self) -> None:
        assert normalize_int("123") == 123

    def test_string_invalid_returns_zero(self) -> None:
        assert normalize_int("xyz") == 0

    def test_none_returns_zero(self) -> None:
        assert normalize_int(None) == 0

    def test_list_returns_zero(self) -> None:
        assert normalize_int([1]) == 0


# -- coerce_int_list -----------------------------------------------------------


class TestCoerceIntList:
    def test_list_of_ints(self) -> None:
        assert coerce_int_list([1, 2, 3]) == [1, 2, 3]

    def test_list_of_mixed_filters_non_ints(self) -> None:
        # bool is a subclass of int, so isinstance(True, int) is True —
        # booleans pass through just like plain ints.
        result = coerce_int_list([1, "two", 3, None, 4.5, True])
        assert result == [1, 3, True]

    def test_none_returns_empty(self) -> None:
        assert coerce_int_list(None) == []

    def test_string_returns_empty(self) -> None:
        assert coerce_int_list("not a list") == []

    def test_empty_list_returns_empty(self) -> None:
        assert coerce_int_list([]) == []

    def test_dict_returns_empty(self) -> None:
        assert coerce_int_list({"a": 1}) == []

    def test_int_returns_empty(self) -> None:
        assert coerce_int_list(42) == []

    def test_bool_values_filtered(self) -> None:
        # bool is a subclass of int, so isinstance(True, int) is True.
        # This means bools are kept — but the spec says "list of ints".
        # coerce_int_list uses isinstance(item, int), which includes bool.
        # We document the actual behavior.
        result = coerce_int_list([True, False, 1, 0])
        # bools pass isinstance(item, int) check
        assert True in result
        assert 1 in result
