from __future__ import annotations

import math
from typing import Any
from xml.etree.ElementTree import Element

from crab_archi_design.svg.geometry import BBox, Point, bbox_center, polygon_area
from crab_archi_design.svg.namespace import local_name
from crab_archi_design.svg.path import flatten_path_points, parse_path, path_is_closed
from crab_archi_design.svg.style import style_number
from crab_archi_design.svg.transform import Matrix, apply_matrix, parse_numbers


def polygon_bbox(points: list[Point]) -> BBox | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return BBox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def bbox_dict(box: BBox) -> dict[str, float]:
    return {"x": box.x, "y": box.y, "w": box.width, "h": box.height}


def point_list(points: list[Point], digits: int = 3) -> list[list[float]]:
    return [[round(x, digits), round(y, digits)] for x, y in points]


def shape_points(element: Element, tag: str, matrix: Matrix) -> tuple[list[Point], bool, dict[str, Any] | None]:
    numbers = parse_numbers
    if tag == "rect":
        x = numbers(element.attrib.get("x", "0"))[0] if numbers(element.attrib.get("x", "0")) else 0.0
        y = numbers(element.attrib.get("y", "0"))[0] if numbers(element.attrib.get("y", "0")) else 0.0
        width = numbers(element.attrib.get("width", "0"))[0] if numbers(element.attrib.get("width", "0")) else 0.0
        height = numbers(element.attrib.get("height", "0"))[0] if numbers(element.attrib.get("height", "0")) else 0.0
        points = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        return [apply_matrix(matrix, point) for point in points], True, {"x": x, "y": y, "w": width, "h": height}
    if tag == "line":
        x1 = numbers(element.attrib.get("x1", "0"))[0] if numbers(element.attrib.get("x1", "0")) else 0.0
        y1 = numbers(element.attrib.get("y1", "0"))[0] if numbers(element.attrib.get("y1", "0")) else 0.0
        x2 = numbers(element.attrib.get("x2", "0"))[0] if numbers(element.attrib.get("x2", "0")) else 0.0
        y2 = numbers(element.attrib.get("y2", "0"))[0] if numbers(element.attrib.get("y2", "0")) else 0.0
        return [apply_matrix(matrix, (x1, y1)), apply_matrix(matrix, (x2, y2))], False, {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if tag in {"polyline", "polygon"}:
        raw_points = numbers(element.attrib.get("points", ""))
        points = list(zip(raw_points[0::2], raw_points[1::2]))
        return [apply_matrix(matrix, point) for point in points], tag == "polygon", {"point_count": len(points)}
    if tag in {"circle", "ellipse"}:
        cx = numbers(element.attrib.get("cx", "0"))[0] if numbers(element.attrib.get("cx", "0")) else 0.0
        cy = numbers(element.attrib.get("cy", "0"))[0] if numbers(element.attrib.get("cy", "0")) else 0.0
        rx_values = numbers(element.attrib.get("rx", element.attrib.get("r", "0")))
        ry_values = numbers(element.attrib.get("ry", element.attrib.get("r", "0")))
        rx = rx_values[0] if rx_values else 0.0
        ry = ry_values[0] if ry_values else rx
        points = [(cx + math.cos(i * math.tau / 32.0) * rx, cy + math.sin(i * math.tau / 32.0) * ry) for i in range(32)]
        return [apply_matrix(matrix, point) for point in points], True, {"cx": cx, "cy": cy, "rx": rx, "ry": ry}
    if tag == "path":
        parsed = parse_path(element.attrib.get("d"))
        points = flatten_path_points(parsed)
        return [apply_matrix(matrix, point) for point in points], path_is_closed(parsed), {"subpath_count": len(parsed.subpaths), "warnings": parsed.warnings}
    return [], False, None


def text_anchor(element: Element, matrix: Matrix) -> Point | None:
    x_values = parse_numbers(element.attrib.get("x", ""))
    y_values = parse_numbers(element.attrib.get("y", ""))
    if x_values and y_values:
        return apply_matrix(matrix, (x_values[0], y_values[0]))
    return apply_matrix(matrix, (0.0, 0.0))


def element_text(element: Element) -> str:
    return " ".join(part.strip() for part in element.itertext() if part and part.strip())


def build_shape_node(index: int, element: Element, matrix: Matrix, group_path: list[str], style: dict[str, str]) -> dict[str, Any] | None:
    from crab_archi_design.recognition.ir import stable_node_id

    tag = local_name(element.tag)
    if tag == "text":
        anchor = text_anchor(element, matrix)
        content = element_text(element)
        if not content:
            return None
        return {
            "id": stable_node_id(index),
            "source_id": element.attrib.get("id"),
            "tag": tag,
            "group_path": group_path,
            "analytic": None,
            "polygon": [],
            "is_closed": False,
            "bbox": None,
            "centroid": list(anchor) if anchor else None,
            "area": 0.0,
            "perimeter": 0.0,
            "style": normalized_style(style),
            "text": {
                "content": content,
                "anchor": [round(anchor[0], 3), round(anchor[1], 3)] if anchor else None,
                "font_size": style_number(style, "font-size", 0.0),
                "align": style.get("text-anchor"),
            },
            "role_hint": "label",
            "role_confidence": 0.8,
        }
    points, is_closed, analytic = shape_points(element, tag, matrix)
    if not points:
        return None
    box = polygon_bbox(points)
    if not box:
        return None
    return {
        "id": stable_node_id(index),
        "source_id": element.attrib.get("id"),
        "tag": tag,
        "group_path": group_path,
        "analytic": analytic,
        "polygon": point_list(points),
        "is_closed": is_closed,
        "bbox": bbox_dict(box),
        "centroid": [round(item, 3) for item in bbox_center(box)],
        "area": round(polygon_area(points), 3) if is_closed else 0.0,
        "perimeter": 0.0,
        "style": normalized_style(style),
        "text": None,
        "role_hint": "unknown",
        "role_confidence": 0.0,
    }


def normalized_style(style: dict[str, str]) -> dict[str, Any]:
    return {
        "fill": style.get("fill"),
        "stroke": style.get("stroke"),
        "stroke_width": style_number(style, "stroke-width", 0.0),
        "opacity": style_number(style, "opacity", 1.0),
        "class": style.get("class"),
    }
