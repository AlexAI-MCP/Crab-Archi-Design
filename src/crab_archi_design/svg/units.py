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


def document_unit_scale(width_raw: str | None, height_raw: str | None, viewbox: ViewBox) -> dict[str, float | str | bool | None]:
    width_value, width_unit = parse_length(width_raw)
    height_value, height_unit = parse_length(height_raw)
    physical_width_mm = length_to_mm(width_value, width_unit)
    physical_height_mm = length_to_mm(height_value, height_unit)
    scale_x = physical_width_mm / viewbox.width if physical_width_mm is not None and viewbox.width > 0 else None
    scale_y = physical_height_mm / viewbox.height if physical_height_mm is not None and viewbox.height > 0 else None
    scale_relative_error = None
    if scale_x is not None and scale_y is not None:
        denominator = max(abs(scale_x), abs(scale_y), 1e-12)
        scale_relative_error = abs(scale_x - scale_y) / denominator
        scale_consistent = scale_relative_error <= 1e-3
        source = "width_height"
    elif scale_x is not None:
        scale_consistent = None
        source = "width"
    elif scale_y is not None:
        scale_consistent = None
        source = "height"
    else:
        scale_consistent = None
        source = "none"
    return {
        "unit_scale_x_mm": scale_x,
        "unit_scale_y_mm": scale_y,
        "unit_scale_consistent": scale_consistent,
        "unit_scale_relative_error": scale_relative_error,
        "unit_scale_source": source,
        "width_unit": width_unit,
        "height_unit": height_unit,
        "physical_width_mm": physical_width_mm,
        "physical_height_mm": physical_height_mm,
    }


def length_to_mm(value: float, unit: str | None) -> float | None:
    if not unit or unit not in UNIT_TO_MM or value <= 0:
        return None
    return value * UNIT_TO_MM[unit]
