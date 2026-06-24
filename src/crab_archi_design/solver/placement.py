from __future__ import annotations

from typing import Any

from crab_archi_design.solver.feasible import build_feasible_report
from crab_archi_design.solver.sizing import extract_program_targets


PROGRAM_PRIORITY = {
    "greenery_lounge": 100,
    "fitness_gx": 90,
    "golf_screen": 80,
    "sauna_locker_shower": 70,
    "hall_lobby": 60,
    "management_support": 20,
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def box_area(box: dict[str, Any]) -> float:
    return max(0.0, safe_float(box.get("width"))) * max(0.0, safe_float(box.get("height")))


def target_area_weight(target: dict[str, Any]) -> float:
    for key in ["target_area_m2", "target_area_pyeong"]:
        value = safe_float(target.get(key))
        if value > 0:
            return value
    return 1.0


def role_priority(role: str) -> int:
    return PROGRAM_PRIORITY.get(role, 10)


def sorted_program_targets(targets: list[dict[str, Any]], topology: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    adjacency_degree: dict[str, int] = {}
    for edge in (topology or {}).get("edges", []):
        if edge.get("type") != "ontology_cluster_adjacency_target":
            continue
        for key in ["left_role", "right_role"]:
            role = str(edge.get(key) or "")
            if role:
                adjacency_degree[role] = adjacency_degree.get(role, 0) + 1
    return sorted(
        targets,
        key=lambda target: (
            role_priority(str(target.get("role") or "")),
            adjacency_degree.get(str(target.get("role") or ""), 0),
            target_area_weight(target),
            str(target.get("role") or ""),
        ),
        reverse=True,
    )


def largest_mutable_bbox(feasible: dict[str, Any]) -> dict[str, Any] | None:
    polygons = feasible.get("mutable_polygons", [])
    if not polygons:
        return None
    polygon = sorted(polygons, key=lambda item: (box_area(item.get("bbox") or {}), str(item.get("id") or "")), reverse=True)[0]
    bbox = dict(polygon.get("bbox") or {})
    if box_area(bbox) <= 0:
        return None
    bbox["source_polygon_id"] = polygon.get("id")
    bbox["source_polygon_role"] = polygon.get("role")
    return bbox


def slice_program_boxes(targets: list[dict[str, Any]], box: dict[str, Any]) -> list[dict[str, Any]]:
    total_weight = sum(target_area_weight(target) for target in targets)
    if total_weight <= 0:
        return []
    x = safe_float(box.get("x"))
    y = safe_float(box.get("y"))
    width = safe_float(box.get("width"))
    height = safe_float(box.get("height"))
    vertical = width >= height
    cursor = x if vertical else y
    remaining_length = width if vertical else height
    remaining_weight = total_weight
    placements: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        weight = target_area_weight(target)
        if index == len(targets) - 1:
            length = remaining_length
        else:
            length = round(max(0.0, remaining_length * weight / remaining_weight), 3)
        if vertical:
            planned_box = {"x": round(cursor, 3), "y": round(y, 3), "width": length, "height": round(height, 3)}
        else:
            planned_box = {"x": round(x, 3), "y": round(cursor, 3), "width": round(width, 3), "height": length}
        drawing_area = box_area(planned_box)
        placements.append(
            {
                "role": target.get("role"),
                "target_area_m2": target.get("target_area_m2"),
                "target_area_pyeong": target.get("target_area_pyeong"),
                "target_area_weight": round(weight, 3),
                "target_area_ratio": round(weight / total_weight, 6),
                "planned_box": planned_box,
                "planned_drawing_area": round(drawing_area, 3),
                "slice_axis": "x" if vertical else "y",
                "order": index + 1,
                "priority": role_priority(str(target.get("role") or "")),
            }
        )
        cursor += length
        remaining_length = max(0.0, remaining_length - length)
        remaining_weight = max(0.0, remaining_weight - weight)
    return placements


def build_initial_placement_report(
    standards: dict[str, Any] | None,
    constraints: dict[str, Any] | None,
    topology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feasible = build_feasible_report(constraints)
    targets = sorted_program_targets(extract_program_targets(standards), topology)
    placement_box = largest_mutable_bbox(feasible)
    placements = slice_program_boxes(targets, placement_box) if placement_box else []
    total_planned_area = round(sum(safe_float(item.get("planned_drawing_area")) for item in placements), 3)
    placement_area = round(box_area(placement_box or {}), 3)
    protected_overlap = safe_float(feasible.get("protected_overlap_area_estimate"))
    gates = {
        "target_programs_found": bool(targets),
        "mutable_zone_found": feasible.get("mutable_polygon_count", 0) > 0,
        "placement_bbox_positive": placement_area > 0,
        "placed_all_targets": bool(targets) and len(placements) == len(targets),
        "fills_seed_bbox_without_remainder": bool(placements) and abs(total_planned_area - placement_area) <= max(0.01, placement_area * 0.001),
    }
    warnings = []
    if protected_overlap > 0:
        warnings.append(
            {
                "code": "protected_overlap_requires_local_search",
                "protected_overlap_area_estimate": round(protected_overlap, 3),
                "message": "Initial bbox slicing is a deterministic seed; local search or same-layer patch selection must avoid protected overlap.",
            }
        )
    return {
        "schema": "crab-archi-design-initial-placement-report-v1",
        "status": "active" if all(gates.values()) else "review_required",
        "strategy": "deterministic_mutable_bbox_slicing",
        "coordinate_space": feasible.get("coordinate_space"),
        "program_order": [item.get("role") for item in placements],
        "placement_bbox": placement_box,
        "placement_bbox_area": placement_area,
        "planned_drawing_area": total_planned_area,
        "program_count": len(placements),
        "gates": gates,
        "warnings": warnings,
        "placements": placements,
    }
