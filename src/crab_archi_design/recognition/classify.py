from __future__ import annotations

import math
from typing import Any

COLUMN_HINT_TOKENS = ("column", "기둥")
UNFILLED_VALUES = {"none", "transparent"}


def column_hint_text(node: dict[str, Any]) -> str:
    style = node.get("style") or {}
    parts = [node.get("source_id"), style.get("class")]
    group_path = node.get("group_path") or []
    parts.extend(item for item in group_path if isinstance(item, str))
    return " ".join(str(item) for item in parts if item).lower()


def has_column_hint(node: dict[str, Any]) -> bool:
    text = column_hint_text(node)
    return any(token in text for token in COLUMN_HINT_TOKENS)


def classify_node(node: dict[str, Any], drawing_area: float, max_dim: float | None = None) -> tuple[str, float]:
    tag = node.get("tag")
    if node.get("source_id") == "background":
        return "unknown", 0.0
    if tag == "text":
        return "label", 0.8
    box = node.get("bbox") or {}
    width = float(box.get("w") or 0.0)
    height = float(box.get("h") or 0.0)
    area = max(0.0, width) * max(0.0, height)
    max_side = max(width, height)
    min_side = min(width, height)
    style = node.get("style") or {}
    stroke_width = float(style.get("stroke_width") or 0.0)
    is_closed = bool(node.get("is_closed"))
    if max_dim is None or max_dim <= 0:
        max_dim = math.sqrt(max(1.0, drawing_area))

    if tag in {"line", "polyline"} and max_side >= 2.0 and stroke_width >= 0.25:
        return "wall", 0.35
    if tag == "path" and not is_closed and max_side >= 2.0 and stroke_width >= 0.25:
        return "wall", 0.32

    if is_closed and min_side > 0:
        aspect = max_side / min_side
        if tag in {"rect", "polygon"} and aspect <= 1.35 and area <= max(1.0, drawing_area) * 0.001:
            if has_column_hint(node):
                return "column", 0.6
            # SVG default fill is black, so an unset fill still reads as a solid column mark.
            fill = str(style.get("fill") or "black").strip().lower()
            in_absolute_band = 6.0 <= min_side <= 45.0
            in_relative_band = max_dim * 0.004 <= min_side <= max_dim * 0.04 and fill not in UNFILLED_VALUES
            if in_absolute_band or in_relative_band:
                return "column", 0.45
        if tag != "path" and area >= max(1.0, drawing_area) * 0.002 and aspect <= 10.0:
            return "room_envelope", 0.3

    return "unknown", 0.0
