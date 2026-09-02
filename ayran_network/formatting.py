"""Small presentation helpers shared by the terminal UI and integrations."""

from __future__ import annotations

import math

__all__ = ["format_bytes", "format_rate"]


def format_rate(bytes_per_second: float) -> str:
    """Format a byte rate for compact display."""

    return f"{_format_quantity(bytes_per_second)}/s"


def format_bytes(byte_count: int) -> str:
    """Format an accumulated byte count for compact display."""

    return _format_quantity(float(byte_count))


def _format_quantity(value: float) -> str:
    if not math.isfinite(value) or value <= 0.0:
        return "0 B"

    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{value:.0f} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"
