"""Type normalization utilities for dashboard payload serialization."""


def normalize_optional_text(value: object) -> str | None:
    """Return a JSON-safe optional string value."""
    if value is None or isinstance(value, str):
        return value
    return None


def normalize_float(value: object) -> float:
    """Return a safe float for sorting."""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def normalize_int(value: object) -> int:
    """Return a safe int for sorting."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def coerce_int_list(value: object) -> list[int]:
    """Coerce a payload field to a list of ints."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int)]
