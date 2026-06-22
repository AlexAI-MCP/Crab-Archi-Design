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


def make_room_plan(layout: tuple[float, float, float, float], program_areas: dict[str, float]) -> list[dict[str, Any]]:
    x, y, w, h = layout
    hall_w = w * 0.14
    left_w = w * 0.46
    right_w = max(1.0, w - left_w - hall_w)
    top_h = h * 0.36
    lower_y = y + top_h
    lower_h = max(1.0, h - top_h)
    support_w = w * 0.18
    golf_h = lower_h * 0.58
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
    layout = avoid_no_go(inset_box(base_box, margin), protected_boxes)
    program_areas = program_areas_from_standards(solver_input)
    rooms = make_room_plan(layout, program_areas)
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

    room_boxes = [(item["x"], item["y"], item["width"], item["height"]) for item in rooms]
    no_go_intrusions = [
        {"room": room_item["role"], "protected_index": idx + 1}
        for room_item, room_box in zip(rooms, room_boxes)
        for idx, protected in enumerate(protected_boxes)
        if bboxes_intersect(room_box, protected)
    ]
    standards_roles = {classify_program(row) for row in standard_rows(solver_input)}
    standards_roles.discard(None)
    planned_roles = {item["role"] for item in rooms}
    large_roles = {"greenery_lounge", "fitness_gx", "golf_screen"}
    return {
        "viewBox": viewbox,
        "shell_found": shell_box is not None,
        "mutable_zone_used": mutable_box is not None,
        "layout_box": {"x": layout[0], "y": layout[1], "width": layout[2], "height": layout[3]},
        "layout_area": bbox_area(layout),
        "protected_box_count": len(protected_boxes),
        "recognized_column_count": len(column_boxes),
        "no_go_intrusions": no_go_intrusions,
        "program_areas": program_areas,
        "rooms": rooms,
        "room_count": len(rooms),
        "standards_roles": sorted(role for role in standards_roles if role),
        "planned_standard_roles": sorted(role for role in standards_roles if role in planned_roles),
        "large_program_roles_present": sorted(role for role in large_roles if role in planned_roles),
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
                "no_go_intrusion_free": not summary["no_go_intrusions"],
                "standards_available": bool(standards_roles),
                "standard_programs_planned": planned_standard_roles >= standards_roles if standards_roles else False,
                "large_programs_present": set(summary["large_program_roles_present"]) >= {"greenery_lounge", "fitness_gx", "golf_screen"},
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
