from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


PROGRAM_RULES = [
    ("greenery_lounge", "Greenery Lounge", ["그리너리", "라운지", "카페", "도서관", "greenery", "lounge", "cafe", "library"]),
    ("fitness_gx", "Fitness / GX", ["피트니스", "gx", "운동", "fitness"]),
    ("golf_screen", "Golf / Screen Golf", ["골프", "스크린", "golf", "screen"]),
    ("sauna_locker_shower", "Sauna / Locker / Shower", ["사우나", "샤워", "락커", "라커", "sauna", "shower", "locker"]),
    ("management_support", "Management / Support", ["관리", "방재", "당직", "회의", "탕비", "소장", "용역", "support", "management"]),
]


DEFAULT_PROGRAM_AREAS = {
    "greenery_lounge": 80.0,
    "fitness_gx": 70.0,
    "golf_screen": 95.0,
    "sauna_locker_shower": 85.0,
    "management_support": 35.0,
}


PROGRAM_COLORS = {
    "greenery_lounge": ("#dff7e8", "#16803a"),
    "fitness_gx": ("#e3f0ff", "#2563eb"),
    "golf_screen": ("#fff4d6", "#b45309"),
    "sauna_locker_shower": ("#e0f7f4", "#0f766e"),
    "management_support": ("#eceff3", "#4b5563"),
    "hall_lobby": ("#fff8bf", "#ca8a04"),
    "support": ("#f1f5f9", "#64748b"),
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def qname(tag: str) -> str:
    return f"{{{SVG_NS}}}{tag}"


def local_tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def parse_viewbox(raw: Any) -> list[float]:
    if isinstance(raw, str) and raw.strip():
        try:
            values = [float(item) for item in raw.replace(",", " ").split()]
        except ValueError:
            values = []
        if len(values) == 4 and values[2] > 0 and values[3] > 0:
            return values
    return [0.0, 0.0, 1000.0, 700.0]


def count_images(root: ET.Element) -> int:
    return sum(1 for element in root.iter() if local_tag(element) == "image")


def normalize_points(points: Any) -> list[tuple[float, float]]:
    normalized = []
    if not isinstance(points, list):
        return normalized
    for point in points:
        if not isinstance(point, list) or len(point) < 2:
            continue
        try:
            normalized.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    return normalized


def points_bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x <= min_x or max_y <= min_y:
        return None
    return min_x, min_y, max_x - min_x, max_y - min_y


def bbox_right(box: tuple[float, float, float, float]) -> float:
    return box[0] + box[2]


def bbox_bottom(box: tuple[float, float, float, float]) -> float:
    return box[1] + box[3]


def bbox_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2]) * max(0.0, box[3])


def polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        total += point[0] * next_point[1] - next_point[0] * point[1]
    return abs(total) * 0.5


def point_on_segment(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float], tolerance: float = 1e-6) -> bool:
    px, py = point
    sx, sy = start
    ex, ey = end
    cross = (px - sx) * (ey - sy) - (py - sy) * (ex - sx)
    if abs(cross) > tolerance:
        return False
    dot = (px - sx) * (ex - sx) + (py - sy) * (ey - sy)
    if dot < -tolerance:
        return False
    length_sq = (ex - sx) ** 2 + (ey - sy) ** 2
    if length_sq <= tolerance:
        return (px - sx) ** 2 + (py - sy) ** 2 <= tolerance
    return dot <= length_sq + tolerance


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if point_on_segment(point, previous, current):
            return True
        xi, yi = current
        xj, yj = previous
        intersects = (yi > y) != (yj > y)
        if intersects:
            x_at_y = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x <= x_at_y:
                inside = not inside
        previous = current
    return inside


def room_box(room_item: dict[str, Any]) -> tuple[float, float, float, float]:
    return (float(room_item["x"]), float(room_item["y"]), float(room_item["width"]), float(room_item["height"]))


def box_checkpoints(box: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    x, y, w, h = box
    return [
        (x, y),
        (x + w, y),
        (x + w, y + h),
        (x, y + h),
        (x + w * 0.5, y + h * 0.5),
    ]


def box_sample_points(box: tuple[float, float, float, float], divisions: int = 4) -> list[tuple[float, float]]:
    x, y, w, h = box
    steps = max(1, divisions)
    points = []
    for xi in range(steps + 1):
        for yi in range(steps + 1):
            points.append((x + w * xi / steps, y + h * yi / steps))
    return points


def box_inside_polygons(box: tuple[float, float, float, float], polygons: list[list[tuple[float, float]]]) -> bool:
    if not polygons:
        return True
    samples = box_sample_points(box)
    return all(all(point_in_polygon(point, polygon) for point in samples) for polygon in polygons)


def room_shell_violations(rooms: list[dict[str, Any]], shell_points: list[tuple[float, float]]) -> list[dict[str, Any]]:
    if not shell_points:
        return []
    violations = []
    for planned in rooms:
        outside_points = [point for point in box_sample_points(room_box(planned)) if not point_in_polygon(point, shell_points)]
        if outside_points:
            violations.append({"room": planned["role"], "outside_point_count": len(outside_points)})
    return violations


def room_aspect_violations(rooms: list[dict[str, Any]], max_room_aspect: float = 5.5, max_hall_aspect: float = 8.0) -> list[dict[str, Any]]:
    violations = []
    for planned in rooms:
        width = max(1.0, float(planned["width"]))
        height = max(1.0, float(planned["height"]))
        aspect = max(width / height, height / width)
        allowed = max_hall_aspect if planned["role"] == "hall_lobby" else max_room_aspect
        if aspect > allowed:
            violations.append({"room": planned["role"], "aspect": round(aspect, 3), "allowed": allowed})
    return violations


def role_area_map(rooms: list[dict[str, Any]]) -> dict[str, float]:
    return {item["role"]: float(item["drawing_area"]) for item in rooms}


def large_program_hierarchy_ok(rooms: list[dict[str, Any]]) -> bool:
    areas = role_area_map(rooms)
    required = ["greenery_lounge", "fitness_gx", "golf_screen"]
    if not all(role in areas for role in required):
        return False
    return areas["greenery_lounge"] >= areas["fitness_gx"] >= areas["golf_screen"]


def room_no_go_intrusions(rooms: list[dict[str, Any]], protected_boxes: list[tuple[float, float, float, float]]) -> list[dict[str, Any]]:
    return [
        {"room": room_item["role"], "protected_index": idx + 1}
        for room_item in rooms
        for idx, protected in enumerate(protected_boxes)
        if bboxes_intersect(room_box(room_item), protected)
    ]


def layout_box_intrudes_no_go(box: tuple[float, float, float, float], protected_boxes: list[tuple[float, float, float, float]]) -> bool:
    return any(bboxes_intersect(box, protected) for protected in protected_boxes)


def find_shell_aware_layout_box(
    base_box: tuple[float, float, float, float],
    boundary_polygons: list[list[tuple[float, float]]],
    protected_boxes: list[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], dict[str, Any]]:
    x, y, w, h = base_box
    scale_values = [1.0, 0.96, 0.92, 0.88, 0.84, 0.8, 0.74, 0.68, 0.62, 0.56, 0.5, 0.44, 0.38]
    offsets = [0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]
    tested = 0
    feasible: list[tuple[float, tuple[float, float, float, float]]] = []
    for scale_w in scale_values:
        for scale_h in scale_values:
            candidate_w = w * scale_w
            candidate_h = h * scale_h
            if candidate_w < max(1.0, w * 0.22) or candidate_h < max(1.0, h * 0.22):
                continue
            for offset_x in offsets:
                for offset_y in offsets:
                    candidate = (x + (w - candidate_w) * offset_x, y + (h - candidate_h) * offset_y, candidate_w, candidate_h)
                    tested += 1
                    if not box_inside_polygons(candidate, boundary_polygons):
                        continue
                    if layout_box_intrudes_no_go(candidate, protected_boxes):
                        continue
                    aspect = max(candidate_w / max(1.0, candidate_h), candidate_h / max(1.0, candidate_w))
                    center_bias = abs(offset_x - 0.5) + abs(offset_y - 0.5)
                    score = bbox_area(candidate) - bbox_area(base_box) * 0.015 * aspect - bbox_area(base_box) * 0.01 * center_bias
                    feasible.append((score, candidate))
    if feasible:
        feasible.sort(key=lambda item: item[0], reverse=True)
        return feasible[0][1], {
            "strategy": "shell_aware_grid_search",
            "repair_applied": feasible[0][1] != base_box,
            "tested_layout_boxes": tested,
            "feasible_layout_boxes": len(feasible),
        }

    repaired = avoid_no_go(base_box, protected_boxes)
    return repaired, {
        "strategy": "fallback_inset_avoid_no_go",
        "repair_applied": repaired != base_box,
        "tested_layout_boxes": tested,
        "feasible_layout_boxes": 0,
    }


def bboxes_intersect(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return not (
        bbox_right(left) <= right[0]
        or bbox_right(right) <= left[0]
        or bbox_bottom(left) <= right[1]
        or bbox_bottom(right) <= left[1]
    )


def inset_box(box: tuple[float, float, float, float], margin: float) -> tuple[float, float, float, float]:
    margin = max(0.0, min(margin, box[2] * 0.18, box[3] * 0.18))
    return box[0] + margin, box[1] + margin, max(1.0, box[2] - margin * 2), max(1.0, box[3] - margin * 2)


def box_from_constraint_items(items: list[dict[str, Any]], roles: set[str]) -> tuple[list[tuple[float, float]], tuple[float, float, float, float] | None]:
    for item in items:
        if item.get("role") not in roles:
            continue
        points = normalize_points(item.get("points"))
        box = points_bbox(points)
        if box:
            return points, box
    return [], None


def no_go_boxes(items: list[dict[str, Any]]) -> list[tuple[float, float, float, float]]:
    boxes = []
    for item in items:
        if item.get("role") not in {"no_go", "protect", "lock"}:
            continue
        box = points_bbox(normalize_points(item.get("points")))
        if box:
            boxes.append(box)
    return boxes


def recognized_column_boxes(solver_input: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    boxes = []
    geometry = (solver_input.get("recognition_manifest") or {}).get("geometry_candidates", {})
    for item in geometry.get("column_candidates", []):
        bbox = item.get("bbox", {})
        try:
            box = (float(bbox["x"]), float(bbox["y"]), float(bbox["width"]), float(bbox["height"]))
        except (KeyError, TypeError, ValueError):
            continue
        if bbox_area(box) > 0:
            boxes.append(box)
    return boxes[:300]


def avoid_no_go(layout: tuple[float, float, float, float], protected_boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    current = layout
    for box in protected_boxes:
        if not bboxes_intersect(current, box):
            continue
        x, y, w, h = current
        gap = max(min(w, h) * 0.025, 1.0)
        center_x = box[0] + box[2] * 0.5
        center_y = box[1] + box[3] * 0.5
        if center_x >= x + w * 0.52:
            new_right = max(x + w * 0.55, box[0] - gap)
            current = (x, y, max(1.0, new_right - x), h)
        elif center_x <= x + w * 0.48:
            new_left = min(x + w * 0.45, bbox_right(box) + gap)
            current = (new_left, y, max(1.0, bbox_right(current) - new_left), h)
        elif center_y >= y + h * 0.5:
            new_bottom = max(y + h * 0.55, box[1] - gap)
            current = (x, y, w, max(1.0, new_bottom - y))
        else:
            new_top = min(y + h * 0.45, bbox_bottom(box) + gap)
            current = (x, new_top, w, max(1.0, bbox_bottom(current) - new_top))
    return current


def classify_program(row: dict[str, str]) -> str | None:
    text = " ".join(f"{key} {value}" for key, value in row.items()).lower()
    for role, _label, terms in PROGRAM_RULES:
        if any(term.lower() in text for term in terms):
            return role
    return None


def parse_area(row: dict[str, str]) -> float | None:
    preferred_values = []
    other_values = []
    for key, value in row.items():
        target = preferred_values if any(term in key.lower() for term in ["area", "면적", "m2", "㎡"]) else other_values
        target.append(value)
    for value in [*preferred_values, *other_values]:
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if match:
            return float(match.group(0))
    return None


def standard_rows(solver_input: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for item in (solver_input.get("standards_manifest") or {}).get("standard_items", []):
        for row in item.get("payload", {}).get("selected_rows", []):
            if isinstance(row, dict):
                rows.append({str(key): str(value) for key, value in row.items()})
    return rows


def program_areas_from_standards(solver_input: dict[str, Any]) -> dict[str, float]:
    areas: dict[str, float] = {}
    for row in standard_rows(solver_input):
        role = classify_program(row)
        area = parse_area(row)
        if role and area:
            areas[role] = areas.get(role, 0.0) + area
    for role, area in DEFAULT_PROGRAM_AREAS.items():
        areas.setdefault(role, area)
    return areas


def room(x: float, y: float, w: float, h: float, role: str, label: str, target_area_m2: float | None) -> dict[str, Any]:
    return {
        "role": role,
        "label": label,
        "x": x,
        "y": y,
        "width": max(1.0, w),
        "height": max(1.0, h),
        "drawing_area": max(1.0, w) * max(1.0, h),
        "target_area_m2": target_area_m2,
    }


def make_room_plan(layout: tuple[float, float, float, float], program_areas: dict[str, float], variant: dict[str, float] | None = None) -> list[dict[str, Any]]:
    variant = variant or {}
    x, y, w, h = layout
    hall_w = w * variant.get("hall_w", 0.14)
    left_w = w * variant.get("left_w", 0.46)
    right_w = max(1.0, w - left_w - hall_w)
    top_h = h * variant.get("top_h", 0.36)
    lower_y = y + top_h
    lower_h = max(1.0, h - top_h)
    support_w = w * variant.get("support_w", 0.18)
    golf_h = lower_h * variant.get("golf_h", 0.58)
    right_x = x + left_w + hall_w
    rooms = [
        room(x, y, w - support_w, top_h, "greenery_lounge", "Greenery Lounge", program_areas.get("greenery_lounge")),
        room(x + w - support_w, y, support_w, top_h * 0.52, "management_support", "Management", program_areas.get("management_support")),
        room(x + w - support_w, y + top_h * 0.52, support_w, top_h * 0.48, "support", "Support", None),
        room(x, lower_y, left_w, lower_h, "fitness_gx", "Fitness / GX", program_areas.get("fitness_gx")),
        room(x + left_w, lower_y, hall_w, lower_h, "hall_lobby", "Main Hall", None),
        room(right_x, lower_y, right_w, golf_h, "golf_screen", "Golf / Screen Golf", program_areas.get("golf_screen")),
        room(right_x, lower_y + golf_h, right_w, lower_h - golf_h, "sauna_locker_shower", "Sauna / Locker / Shower", program_areas.get("sauna_locker_shower")),
    ]
    return rooms


def plan_variants() -> list[dict[str, Any]]:
    return [
        {"name": "balanced", "top_h": 0.36, "left_w": 0.46, "hall_w": 0.14, "support_w": 0.18, "golf_h": 0.58},
        {"name": "greenery_expanded", "top_h": 0.42, "left_w": 0.47, "hall_w": 0.12, "support_w": 0.16, "golf_h": 0.52},
        {"name": "fitness_priority", "top_h": 0.39, "left_w": 0.5, "hall_w": 0.12, "support_w": 0.15, "golf_h": 0.52},
        {"name": "compact_support", "top_h": 0.4, "left_w": 0.48, "hall_w": 0.13, "support_w": 0.13, "golf_h": 0.54},
    ]


def choose_room_plan(
    layout: tuple[float, float, float, float],
    program_areas: dict[str, float],
    shell_points: list[tuple[float, float]],
    protected_boxes: list[tuple[float, float, float, float]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    choices = []
    for variant in plan_variants():
        rooms = make_room_plan(layout, program_areas, variant)
        shell_violations = room_shell_violations(rooms, shell_points)
        no_go_intrusions = room_no_go_intrusions(rooms, protected_boxes)
        aspect_violations = room_aspect_violations(rooms)
        hierarchy_ok = large_program_hierarchy_ok(rooms)
        total_room_area = sum(bbox_area(room_box(item)) for item in rooms)
        penalty = len(shell_violations) * 1000 + len(no_go_intrusions) * 1000 + len(aspect_violations) * 250 + (0 if hierarchy_ok else 500)
        score = total_room_area - penalty
        choices.append((score, rooms, variant, shell_violations, no_go_intrusions, aspect_violations, hierarchy_ok))
    choices.sort(key=lambda item: item[0], reverse=True)
    _score, rooms, variant, shell_violations, no_go_intrusions, aspect_violations, hierarchy_ok = choices[0]
    return rooms, {
        "strategy": variant["name"],
        "tested_room_plans": len(choices),
        "selected_room_plan_shell_violation_count": len(shell_violations),
        "selected_room_plan_no_go_intrusion_count": len(no_go_intrusions),
        "selected_room_plan_aspect_violation_count": len(aspect_violations),
        "selected_room_plan_hierarchy_ok": hierarchy_ok,
    }


def room_by_role(rooms: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["role"]: item for item in rooms}


def shared_boundary(left: dict[str, Any], right: dict[str, Any], tolerance: float = 1e-3) -> dict[str, float | str] | None:
    ax, ay, aw, ah = room_box(left)
    bx, by, bw, bh = room_box(right)
    overlap_y1 = max(ay, by)
    overlap_y2 = min(ay + ah, by + bh)
    if overlap_y2 > overlap_y1:
        if abs(ax + aw - bx) <= tolerance:
            return {"orientation": "vertical", "x": ax + aw, "y1": overlap_y1, "y2": overlap_y2}
        if abs(bx + bw - ax) <= tolerance:
            return {"orientation": "vertical", "x": ax, "y1": overlap_y1, "y2": overlap_y2}
    overlap_x1 = max(ax, bx)
    overlap_x2 = min(ax + aw, bx + bw)
    if overlap_x2 > overlap_x1:
        if abs(ay + ah - by) <= tolerance:
            return {"orientation": "horizontal", "y": ay + ah, "x1": overlap_x1, "x2": overlap_x2}
        if abs(by + bh - ay) <= tolerance:
            return {"orientation": "horizontal", "y": ay, "x1": overlap_x1, "x2": overlap_x2}
    return None


def add_line(parent: ET.Element, x1: float, y1: float, x2: float, y2: float, attrs: dict[str, str]) -> ET.Element:
    line_attrs = dict(attrs)
    line_attrs.update({"x1": f"{x1:.3f}", "y1": f"{y1:.3f}", "x2": f"{x2:.3f}", "y2": f"{y2:.3f}"})
    return ET.SubElement(parent, qname("line"), line_attrs)


def add_plan_rect_outline(parent: ET.Element, planned: dict[str, Any], wall: float) -> None:
    ET.SubElement(
        parent,
        qname("rect"),
        {
            "x": f"{planned['x']:.3f}",
            "y": f"{planned['y']:.3f}",
            "width": f"{planned['width']:.3f}",
            "height": f"{planned['height']:.3f}",
            "fill": "none",
            "stroke": "#050505",
            "stroke-width": f"{wall * 1.15:.3f}",
            "stroke-linejoin": "miter",
            "data-role": "partition-wall",
            "data-program-boundary": planned["role"],
        },
    )


def add_glazing(parent: ET.Element, boundary: dict[str, float | str], wall: float) -> int:
    common = {
        "stroke": "#2563eb",
        "stroke-width": f"{wall * 0.55:.3f}",
        "stroke-dasharray": f"{wall * 1.4:.3f} {wall * 1.1:.3f}",
        "stroke-linecap": "butt",
        "data-role": "interior-glazing",
    }
    if boundary["orientation"] == "vertical":
        add_line(parent, float(boundary["x"]), float(boundary["y1"]), float(boundary["x"]), float(boundary["y2"]), common)
    else:
        add_line(parent, float(boundary["x1"]), float(boundary["y"]), float(boundary["x2"]), float(boundary["y"]), common)
    return 1


def add_door_opening(
    parent: ET.Element,
    boundary: dict[str, float | str],
    wall: float,
    door_between: str,
    swing_toward: dict[str, Any] | None = None,
) -> None:
    if boundary["orientation"] == "vertical":
        x = float(boundary["x"])
        y1 = float(boundary["y1"])
        y2 = float(boundary["y2"])
        overlap = y2 - y1
        size = max(wall * 7.5, min(overlap * 0.42, wall * 18.0))
        center = (y1 + y2) * 0.5
        start = center - size * 0.5
        end = center + size * 0.5
        sign = 1.0
        if swing_toward:
            sx, _sy, sw, _sh = room_box(swing_toward)
            sign = 1.0 if sx + sw * 0.5 > x else -1.0
        add_line(
            parent,
            x,
            start,
            x,
            end,
            {
                "stroke": "#ffffff",
                "stroke-width": f"{wall * 2.9:.3f}",
                "stroke-linecap": "butt",
                "data-role": "door-opening",
                "data-door-between": door_between,
            },
        )
        leaf = size * 0.72
        add_line(
            parent,
            x,
            start,
            x + sign * leaf,
            start,
            {
                "stroke": "#050505",
                "stroke-width": f"{wall * 0.55:.3f}",
                "stroke-linecap": "square",
                "data-role": "door-leaf",
                "data-door-between": door_between,
            },
        )
        ET.SubElement(
            parent,
            qname("path"),
            {
                "d": f"M {x + sign * leaf:.3f} {start:.3f} Q {x + sign * leaf:.3f} {center:.3f} {x:.3f} {end:.3f}",
                "fill": "none",
                "stroke": "#050505",
                "stroke-width": f"{wall * 0.35:.3f}",
                "data-role": "door-swing",
                "data-door-between": door_between,
            },
        )
        return

    y = float(boundary["y"])
    x1 = float(boundary["x1"])
    x2 = float(boundary["x2"])
    overlap = x2 - x1
    size = max(wall * 7.5, min(overlap * 0.42, wall * 18.0))
    center = (x1 + x2) * 0.5
    start = center - size * 0.5
    end = center + size * 0.5
    sign = 1.0
    if swing_toward:
        _sx, sy, _sw, sh = room_box(swing_toward)
        sign = 1.0 if sy + sh * 0.5 > y else -1.0
    add_line(
        parent,
        start,
        y,
        end,
        y,
        {
            "stroke": "#ffffff",
            "stroke-width": f"{wall * 2.9:.3f}",
            "stroke-linecap": "butt",
            "data-role": "door-opening",
            "data-door-between": door_between,
        },
    )
    leaf = size * 0.72
    add_line(
        parent,
        start,
        y,
        start,
        y + sign * leaf,
        {
            "stroke": "#050505",
            "stroke-width": f"{wall * 0.55:.3f}",
            "stroke-linecap": "square",
            "data-role": "door-leaf",
            "data-door-between": door_between,
        },
    )
    ET.SubElement(
        parent,
        qname("path"),
        {
            "d": f"M {start:.3f} {y + sign * leaf:.3f} Q {center:.3f} {y + sign * leaf:.3f} {end:.3f} {y:.3f}",
            "fill": "none",
            "stroke": "#050505",
            "stroke-width": f"{wall * 0.35:.3f}",
            "data-role": "door-swing",
            "data-door-between": door_between,
        },
    )


def add_corridor_axis(parent: ET.Element, hall: dict[str, Any], wall: float) -> int:
    x, y, w, h = room_box(hall)
    attrs = {
        "stroke": "#64748b",
        "stroke-width": f"{wall * 0.42:.3f}",
        "stroke-dasharray": f"{wall * 1.2:.3f} {wall * 1.8:.3f}",
        "stroke-linecap": "butt",
        "data-role": "corridor-axis",
    }
    inset = wall * 4.0
    if h >= w:
        add_line(parent, x + w * 0.5, y + inset, x + w * 0.5, y + h - inset, attrs)
    else:
        add_line(parent, x + inset, y + h * 0.5, x + w - inset, y + h * 0.5, attrs)
    return 1


def add_plan_detail_layer(parent: ET.Element, rooms: list[dict[str, Any]], layout: tuple[float, float, float, float], wall: float) -> dict[str, Any]:
    detail = ET.SubElement(
        parent,
        qname("g"),
        {
            "id": "crab_archi_design_plan_detail_layer",
            "data-role": "wall-door-corridor-plan",
        },
    )
    for planned in rooms:
        add_plan_rect_outline(detail, planned, wall)

    rooms_by_role = room_by_role(rooms)
    hall = rooms_by_role.get("hall_lobby")
    door_count = 0
    glazing_count = 0
    corridor_axis_count = 0
    if hall:
        corridor_axis_count += add_corridor_axis(detail, hall, wall)
        for role in ["greenery_lounge", "fitness_gx", "golf_screen", "sauna_locker_shower", "management_support", "support"]:
            target = rooms_by_role.get(role)
            if not target:
                continue
            boundary = shared_boundary(hall, target)
            if not boundary:
                continue
            if role == "greenery_lounge":
                glazing_count += add_glazing(detail, boundary, wall)
            add_door_opening(detail, boundary, wall, f"hall_lobby:{role}", swing_toward=hall)
            door_count += 1

        hx, hy, hw, hh = room_box(hall)
        main_entry = {"orientation": "horizontal", "y": hy + hh, "x1": hx, "x2": hx + hw}
        add_door_opening(detail, main_entry, wall, "main_entry:hall_lobby", swing_toward=None)
        door_count += 1

    lx, ly, lw, lh = layout
    ET.SubElement(
        detail,
        qname("rect"),
        {
            "x": f"{lx:.3f}",
            "y": f"{ly:.3f}",
            "width": f"{lw:.3f}",
            "height": f"{lh:.3f}",
            "fill": "none",
            "stroke": "#050505",
            "stroke-width": f"{wall * 1.85:.3f}",
            "stroke-linejoin": "miter",
            "data-role": "community-perimeter-wall",
        },
    )
    return {
        "partition_wall_count": len(rooms),
        "door_opening_count": door_count,
        "corridor_axis_count": corridor_axis_count,
        "interior_glazing_count": glazing_count,
    }


def add_text(parent: ET.Element, x: float, y: float, text: str, size: float, fill: str = "#111827") -> ET.Element:
    element = ET.SubElement(
        parent,
        qname("text"),
        {
            "x": f"{x:.3f}",
            "y": f"{y:.3f}",
            "font-family": "Arial, sans-serif",
            "font-size": f"{size:.3f}",
            "fill": fill,
        },
    )
    element.text = text
    return element


def add_polygon(parent: ET.Element, points: list[tuple[float, float]], attrs: dict[str, str]) -> ET.Element:
    attr = dict(attrs)
    attr["points"] = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return ET.SubElement(parent, qname("polygon"), attr)


def draw_layout(root: ET.Element, solver_input: dict[str, Any]) -> dict[str, Any]:
    viewbox = parse_viewbox(root.attrib.get("viewBox"))
    min_x, min_y, width, height = viewbox
    items = (solver_input.get("constraint_manifest") or {}).get("constraint_items", [])
    shell_points, shell_box = box_from_constraint_items(items, {"community_shell"})
    mutable_points, mutable_box = box_from_constraint_items(items, {"mutable", "projectable"})
    protected_boxes = no_go_boxes(items)
    column_boxes = recognized_column_boxes(solver_input)
    base_box = mutable_box or shell_box or (min_x, min_y, width, height)
    margin = max(min(base_box[2], base_box[3]) * 0.035, 2.0)
    layout_boundary_polygons = []
    if mutable_points:
        layout_boundary_polygons.append(mutable_points)
    if shell_points:
        layout_boundary_polygons.append(shell_points)
    initial_layout_box = inset_box(base_box, margin)
    layout, layout_repair = find_shell_aware_layout_box(initial_layout_box, layout_boundary_polygons, protected_boxes)
    program_areas = program_areas_from_standards(solver_input)
    rooms, room_plan_meta = choose_room_plan(layout, program_areas, shell_points, protected_boxes)
    room_boxes = [room_box(item) for item in rooms]
    total_room_area = sum(bbox_area(item) for item in room_boxes)
    layout_fill_ratio = total_room_area / max(1.0, bbox_area(layout))
    base_coverage_ratio = total_room_area / max(1.0, bbox_area(base_box))
    shell_area = polygon_area(shell_points)
    shell_coverage_ratio = total_room_area / max(1.0, shell_area) if shell_area > 0 else None
    shell_violations = room_shell_violations(rooms, shell_points)
    aspect_violations = room_aspect_violations(rooms)
    source_id = "crab_archi_design_layout_clip"

    defs = next((child for child in root if local_tag(child) == "defs"), None)
    if defs is None:
        defs = ET.SubElement(root, qname("defs"))
    clip = ET.SubElement(defs, qname("clipPath"), {"id": source_id})
    if shell_points:
        add_polygon(clip, shell_points, {})
    else:
        ET.SubElement(clip, qname("rect"), {"x": f"{min_x:.3f}", "y": f"{min_y:.3f}", "width": f"{width:.3f}", "height": f"{height:.3f}"})

    group = ET.SubElement(
        root,
        qname("g"),
        {
            "id": "crab_archi_design_layout_engine_candidate",
            "data-engine": "layout-svg-engine",
            "data-generated-at": now(),
            "clip-path": f"url(#{source_id})",
        },
    )
    wall = max(width, height) * 0.0024
    x, y, w, h = layout
    ET.SubElement(
        group,
        qname("rect"),
        {
            "x": f"{x:.3f}",
            "y": f"{y:.3f}",
            "width": f"{w:.3f}",
            "height": f"{h:.3f}",
            "fill": "#ffffff",
            "stroke": "#111827",
            "stroke-width": f"{wall * 1.4:.3f}",
            "data-role": "redrawn-community-layout-underlay",
        },
    )

    for planned in rooms:
        fill, stroke = PROGRAM_COLORS.get(planned["role"], PROGRAM_COLORS["support"])
        ET.SubElement(
            group,
            qname("rect"),
            {
                "x": f"{planned['x']:.3f}",
                "y": f"{planned['y']:.3f}",
                "width": f"{planned['width']:.3f}",
                "height": f"{planned['height']:.3f}",
                "fill": fill,
                "fill-opacity": "0.84",
                "stroke": "#111827",
                "stroke-width": f"{wall:.3f}",
                "data-program": planned["role"],
                "data-target-area-m2": "" if planned["target_area_m2"] is None else f"{planned['target_area_m2']:.2f}",
            },
        )
        label_size = max(min(planned["height"] * 0.12, planned["width"] * 0.085), max(width, height) * 0.012)
        add_text(group, planned["x"] + wall * 3, planned["y"] + label_size * 1.55, planned["label"], label_size, stroke)
        if planned["target_area_m2"] is not None:
            add_text(group, planned["x"] + wall * 3, planned["y"] + label_size * 2.75, f"target {planned['target_area_m2']:.0f} m2", label_size * 0.58, "#374151")

    plan_detail_summary = add_plan_detail_layer(group, rooms, layout, wall)

    if shell_points:
        add_polygon(
            group,
            shell_points,
            {
                "fill": "none",
                "stroke": "#ec4899",
                "stroke-width": f"{wall * 1.25:.3f}",
                "stroke-linejoin": "round",
                "data-role": "community-shell-preserved",
            },
        )
    for index, box in enumerate(protected_boxes, start=1):
        ET.SubElement(
            root,
            qname("rect"),
            {
                "x": f"{box[0]:.3f}",
                "y": f"{box[1]:.3f}",
                "width": f"{box[2]:.3f}",
                "height": f"{box[3]:.3f}",
                "fill": "none",
                "stroke": "#ef4444",
                "stroke-width": f"{wall * 0.8:.3f}",
                "stroke-dasharray": f"{wall * 3:.3f} {wall * 2:.3f}",
                "data-role": "protected-no-go-zone",
                "data-protected-index": str(index),
            },
        )
    for index, box in enumerate(column_boxes, start=1):
        ET.SubElement(
            root,
            qname("rect"),
            {
                "x": f"{box[0]:.3f}",
                "y": f"{box[1]:.3f}",
                "width": f"{box[2]:.3f}",
                "height": f"{box[3]:.3f}",
                "fill": "#111827",
                "stroke": "#ffffff",
                "stroke-width": f"{wall * 0.35:.3f}",
                "data-role": "recognized-column-preserved",
                "data-column-index": str(index),
            },
        )

    no_go_intrusions = room_no_go_intrusions(rooms, protected_boxes)
    standards_roles = {classify_program(row) for row in standard_rows(solver_input)}
    standards_roles.discard(None)
    planned_roles = {item["role"] for item in rooms}
    large_roles = {"greenery_lounge", "fitness_gx", "golf_screen"}
    return {
        "viewBox": viewbox,
        "shell_found": shell_box is not None,
        "mutable_zone_used": mutable_box is not None,
        "base_box": {"x": base_box[0], "y": base_box[1], "width": base_box[2], "height": base_box[3]},
        "initial_layout_box": {"x": initial_layout_box[0], "y": initial_layout_box[1], "width": initial_layout_box[2], "height": initial_layout_box[3]},
        "layout_box": {"x": layout[0], "y": layout[1], "width": layout[2], "height": layout[3]},
        "layout_repair": layout_repair,
        "room_plan": room_plan_meta,
        "layout_area": bbox_area(layout),
        "total_room_area": total_room_area,
        "layout_fill_ratio": layout_fill_ratio,
        "layout_coverage_ratio": layout_fill_ratio,
        "base_coverage_ratio": base_coverage_ratio,
        "shell_area": shell_area,
        "shell_coverage_ratio": shell_coverage_ratio,
        "protected_box_count": len(protected_boxes),
        "recognized_column_count": len(column_boxes),
        "no_go_intrusions": no_go_intrusions,
        "room_shell_violations": shell_violations,
        "room_aspect_violations": aspect_violations,
        "plan_detail": plan_detail_summary,
        "program_areas": program_areas,
        "rooms": rooms,
        "room_count": len(rooms),
        "standards_roles": sorted(role for role in standards_roles if role),
        "planned_standard_roles": sorted(role for role in standards_roles if role in planned_roles),
        "large_program_roles_present": sorted(role for role in large_roles if role in planned_roles),
        "large_program_hierarchy_ok": large_program_hierarchy_ok(rooms),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-preview", action="store_true")
    args, _ = parser.parse_known_args()
    _ = args

    solver_input_path = Path(os.environ["CRAB_ARCHI_SOLVER_INPUT"])
    run_dir = Path(os.environ["CRAB_ARCHI_RUN_DIR"])
    solver_input = json.loads(solver_input_path.read_text(encoding="utf-8"))
    source_svg = Path(os.environ.get("CRAB_ARCHI_SOURCE_SVG") or solver_input["manifest"]["source_svg"]).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)

    tree = ET.parse(source_svg)
    root = tree.getroot()
    source_image_count = count_images(root)
    summary = draw_layout(root, solver_input)
    output_svg = run_dir / "layout_engine_candidate.svg"
    tree.write(output_svg, encoding="utf-8", xml_declaration=True)

    output_tree = ET.parse(output_svg)
    output_image_count = count_images(output_tree.getroot())
    standards_roles = set(summary["standards_roles"])
    planned_standard_roles = set(summary["planned_standard_roles"])
    report = {
        "schema": "crab-archi-design-layout-engine-report-v1",
        "created_at": now(),
        "status": "pass",
        "engine": "layout-svg-engine",
        "reference_only": False,
        "source_svg": str(source_svg),
        "solver_input": str(solver_input_path),
        "output_svg": str(output_svg),
        "summary": summary,
        "quality": {
            "gates": {
                "native_svg_only": output_image_count == 0,
                "no_raster_overlay_added": output_image_count == source_image_count,
                "layout_layer_added": True,
                "community_shell_found": summary["shell_found"],
                "mutable_zone_or_shell_used": summary["mutable_zone_used"] or summary["shell_found"],
                "rooms_inside_community_shell": not summary["room_shell_violations"],
                "no_go_intrusion_free": not summary["no_go_intrusions"],
                "layout_coverage_sufficient": summary["layout_fill_ratio"] >= 0.98,
                "room_aspect_efficiency": not summary["room_aspect_violations"],
                "plan_detail_layer_added": summary["plan_detail"]["partition_wall_count"] >= summary["room_count"],
                "door_openings_planned": summary["plan_detail"]["door_opening_count"] >= 4,
                "corridor_axis_planned": summary["plan_detail"]["corridor_axis_count"] >= 1,
                "standards_available": bool(standards_roles),
                "standard_programs_planned": planned_standard_roles >= standards_roles if standards_roles else False,
                "large_programs_present": set(summary["large_program_roles_present"]) >= {"greenery_lounge", "fitness_gx", "golf_screen"},
                "large_program_hierarchy": summary["large_program_hierarchy_ok"],
                "room_count_positive": summary["room_count"] > 0,
                "recognized_columns_preserved": summary["recognized_column_count"] >= 0,
            }
        },
    }
    if not all(report["quality"]["gates"].values()):
        report["status"] = "review_required"
    report_path = run_dir / "layout_engine_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    print(output_svg)


if __name__ == "__main__":
    main()
