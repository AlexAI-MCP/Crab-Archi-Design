from __future__ import annotations

from typing import Any


def classify_node(node: dict[str, Any], drawing_area: float) -> tuple[str, float]:
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

    if tag in {"line", "polyline"} and max_side >= 2.0 and stroke_width >= 0.25:
        return "wall", 0.35
    if tag == "path" and not is_closed and max_side >= 2.0 and stroke_width >= 0.25:
        return "wall", 0.32

    if is_closed and min_side > 0:
        aspect = max_side / min_side
        if tag in {"rect", "polygon"} and aspect <= 1.35 and 6.0 <= min_side <= 45.0 and area <= max(1.0, drawing_area) * 0.001:
            return "column", 0.45
        if tag != "path" and area >= max(1.0, drawing_area) * 0.002 and aspect <= 10.0:
            return "room_envelope", 0.3

    return "unknown", 0.0
