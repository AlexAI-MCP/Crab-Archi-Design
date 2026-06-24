from __future__ import annotations

from dataclasses import dataclass

from crab_archi_design.svg.transform import parse_numbers


@dataclass(frozen=True)
class ViewBox:
    min_x: float
    min_y: float
    width: float
    height: float


UNIT_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "px": 25.4 / 96.0,
}


def parse_viewbox(raw: str | None, width: str | None = None, height: str | None = None) -> ViewBox:
    values = parse_numbers(raw or "")
    if len(values) >= 4:
        return ViewBox(values[0], values[1], values[2], values[3])
    width_value = parse_length(width)[0] if width else 0.0
    height_value = parse_length(height)[0] if height else 0.0
    return ViewBox(0.0, 0.0, width_value, height_value)


def parse_length(raw: str | None) -> tuple[float, str | None]:
    if not raw:
        return 0.0, None
    values = parse_numbers(raw)
    unit = "".join(ch for ch in raw.strip() if ch.isalpha() or ch == "%") or None
    return (values[0] if values else 0.0), unit


def unit_scale_mm(width_raw: str | None, viewbox: ViewBox) -> float | None:
    width_value, unit = parse_length(width_raw)
    if not unit or unit not in UNIT_TO_MM or viewbox.width <= 0 or width_value <= 0:
        return None
    return width_value * UNIT_TO_MM[unit] / viewbox.width
