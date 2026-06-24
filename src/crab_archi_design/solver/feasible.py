from __future__ import annotations

from typing import Any

from crab_archi_design.svg.geometry import Point, point_in_polygon, polygon_area


def svg_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def constraint_polygons(manifest: dict[str, Any] | None, roles: set[str]) -> list[dict[str, Any]]:
    polygons: list[dict[str, Any]] = []
    for item in (manifest or {}).get("constraint_items", []):
        role = str(item.get("role") or "")
        if role not in roles:
            continue
        points = points_from_item(item)
        if len(points) < 3:
            continue
        polygons.append(
            {
                "id": item.get("id"),
                "role": role,
                "mode": item.get("mode"),
                "target_hint": item.get("target_hint"),
                "points": points,
                "bbox": polygon_bbox(points),
                "area": round(polygon_area(points), 3),
                "source": "constraints.constraint_items",
            }
        )
    return polygons


def points_from_item(item: dict[str, Any]) -> list[Point]:
    points: list[Point] = []
    for point in item.get("points", []):
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            points.append((svg_float(point[0]), svg_float(point[1])))
    return points


def polygon_bbox(points: list[Point]) -> dict[str, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    if not xs or not ys:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}


def bbox_overlap_area(left: dict[str, float], right: dict[str, float]) -> float:
    left_max_x = left["x"] + left["width"]
    left_max_y = left["y"] + left["height"]
    right_max_x = right["x"] + right["width"]
    right_max_y = right["y"] + right["height"]
    overlap_w = max(0.0, min(left_max_x, right_max_x) - max(left["x"], right["x"]))
    overlap_h = max(0.0, min(left_max_y, right_max_y) - max(left["y"], right["y"]))
    return overlap_w * overlap_h


def sample_points(box: dict[str, float]) -> list[Point]:
    x = box["x"]
    y = box["y"]
    w = box["width"]
    h = box["height"]
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + w * 0.5, y + h * 0.5)]


def bbox_inside_any_polygon(box: dict[str, float], polygons: list[dict[str, Any]]) -> bool:
    if not polygons:
        return False
    return any(all(point_in_polygon(point, polygon["points"]) for point in sample_points(box)) for polygon in polygons)


def bbox_touches_any_polygon(box: dict[str, float], polygons: list[dict[str, Any]]) -> bool:
    if not polygons:
        return False
    for polygon in polygons:
        if any(point_in_polygon(point, polygon["points"]) for point in sample_points(box)):
            return True
        if any(box["x"] <= point[0] <= box["x"] + box["width"] and box["y"] <= point[1] <= box["y"] + box["height"] for point in polygon["points"]):
            return True
    return False


def estimate_protected_overlap_area(mutable_polygons: list[dict[str, Any]], protected_polygons: list[dict[str, Any]]) -> float:
    total = 0.0
    for mutable in mutable_polygons:
        mutable_box = mutable["bbox"]
        for protected in protected_polygons:
            total += bbox_overlap_area(mutable_box, protected["bbox"])
    return round(total, 3)


def build_feasible_report(constraints: dict[str, Any] | None) -> dict[str, Any]:
    shell_polygons = constraint_polygons(constraints, {"community_shell"})
    mutable_polygons = constraint_polygons(constraints, {"mutable", "projectable"})
    protected_polygons = constraint_polygons(constraints, {"no_go", "lock", "protect"})
    mutable_area = round(sum(float(item.get("area") or 0.0) for item in mutable_polygons), 3)
    protected_overlap = estimate_protected_overlap_area(mutable_polygons, protected_polygons)
    available_area = round(max(0.0, mutable_area - protected_overlap), 3)
    mutable_inside_shell = True
    if shell_polygons and mutable_polygons:
        mutable_inside_shell = all(bbox_inside_any_polygon(item["bbox"], shell_polygons) or bbox_touches_any_polygon(item["bbox"], shell_polygons) for item in mutable_polygons)
    return {
        "schema": "crab-archi-design-feasible-report-v1",
        "status": "active" if mutable_polygons and (shell_polygons or not constraints) else "review_required",
        "coordinate_space": "source_svg_viewbox",
        "shell_polygon_count": len(shell_polygons),
        "mutable_polygon_count": len(mutable_polygons),
        "protected_polygon_count": len(protected_polygons),
        "shell_area_estimate": round(sum(float(item.get("area") or 0.0) for item in shell_polygons), 3),
        "mutable_area_estimate": mutable_area,
        "protected_overlap_area_estimate": protected_overlap,
        "available_area_estimate": available_area,
        "gates": {
            "shell_found": bool(shell_polygons),
            "mutable_zone_found": bool(mutable_polygons),
            "protected_zone_found": bool(protected_polygons),
            "mutable_inside_shell_or_touches_shell": mutable_inside_shell,
            "available_area_positive": available_area > 0,
        },
        "shell_polygons": shell_polygons,
        "mutable_polygons": mutable_polygons,
        "protected_polygons": protected_polygons,
    }
