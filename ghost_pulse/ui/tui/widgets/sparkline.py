"""Inline sparkline rendering."""

from __future__ import annotations


_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float | int]) -> str:
    """Return a single-line sparkline for the values."""
    if not values:
        return ""
    max_v = max(values) or 1
    return "".join(_CHARS[min(7, int(v / max_v * 7))] for v in values)
