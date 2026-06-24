from __future__ import annotations

from typing import Any


def svg_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_bbox_dict(box: dict[str, Any] | None) -> dict[str, float]:
    box = box or {}
    return {
        "x": svg_float(box.get("x")),
        "y": svg_float(box.get("y")),
        "width": svg_float(box.get("width", box.get("w"))),
        "height": svg_float(box.get("height", box.get("h"))),
    }


def bbox_gap_distance(left: dict[str, float], right: dict[str, float]) -> float:
    left_max_x = left["x"] + left["width"]
    left_max_y = left["y"] + left["height"]
    right_max_x = right["x"] + right["width"]
    right_max_y = right["y"] + right["height"]
    gap_x = max(0.0, max(left["x"], right["x"]) - min(left_max_x, right_max_x))
    gap_y = max(0.0, max(left["y"], right["y"]) - min(left_max_y, right_max_y))
    return (gap_x**2 + gap_y**2) ** 0.5


def patch_role_priority(role: str | None) -> float:
    return {
        "greenery_lounge": 700.0,
        "fitness_gx": 600.0,
        "golf_screen": 520.0,
        "sauna_locker_shower": 460.0,
        "hall_lobby": 380.0,
        "management_support": 120.0,
    }.get(str(role or ""), 0.0)


def topology_adjacency_targets(topology: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    targets: dict[str, list[dict[str, Any]]] = {}
    if not topology:
        return targets
    nodes = {node["id"]: node for node in topology.get("nodes", []) if node.get("id")}
    for edge in topology.get("edges", []):
        if edge.get("type") != "ontology_cluster_adjacency_target":
            continue
        left_role = str(edge.get("left_role") or "")
        right_role = str(edge.get("right_role") or "")
        if not left_role or not right_role:
            continue
        source_cluster = nodes.get(edge.get("source"), {})
        target_cluster = nodes.get(edge.get("target"), {})
        payload = {
            "edge_id": edge.get("id"),
            "source_cluster_id": edge.get("source"),
            "target_cluster_id": edge.get("target"),
            "target_role": right_role,
            "target_cluster_bbox": normalize_bbox_dict(target_cluster.get("bbox")),
            "rationale": edge.get("rationale"),
            "evidence": edge.get("evidence"),
        }
        targets.setdefault(left_role, []).append(payload)
        reverse_payload = {
            "edge_id": edge.get("id"),
            "source_cluster_id": edge.get("target"),
            "target_cluster_id": edge.get("source"),
            "target_role": left_role,
            "target_cluster_bbox": normalize_bbox_dict(source_cluster.get("bbox")),
            "rationale": edge.get("rationale"),
            "evidence": edge.get("evidence"),
        }
        targets.setdefault(right_role, []).append(reverse_payload)
    return targets


def adjacency_role_weight(role: str | None, target_role: str | None) -> float:
    pair = {str(role or ""), str(target_role or "")}
    if pair == {"greenery_lounge", "hall_lobby"}:
        return 900.0
    if pair == {"fitness_gx", "hall_lobby"}:
        return 760.0
    if pair == {"golf_screen", "hall_lobby"}:
        return 700.0
    if pair == {"sauna_locker_shower", "golf_screen"}:
        return 640.0
    if pair == {"management_support", "hall_lobby"}:
        return 360.0
    return 180.0


def opening_candidate_priority(source: dict[str, Any], adjacency: dict[str, Any] | None) -> float:
    source_box = normalize_bbox_dict(source.get("bbox"))
    if not adjacency:
        return float(source.get("patch_priority") or 0.0)
    target_box = normalize_bbox_dict(adjacency.get("target_cluster_bbox"))
    has_target_bbox = bool(target_box.get("width") or target_box.get("height"))
    gap = bbox_gap_distance(source_box, target_box) if has_target_bbox else 5000.0
    proximity_score = max(0.0, 600.0 - min(gap, 600.0))
    return round(
        float(source.get("patch_priority") or 0.0)
        + adjacency_role_weight(source.get("program_role"), adjacency.get("target_role"))
        + proximity_score,
        3,
    )


def bbox_center(box: dict[str, Any]) -> tuple[float, float]:
    normalized = normalize_bbox_dict(box)
    return (normalized["x"] + normalized["width"] * 0.5, normalized["y"] + normalized["height"] * 0.5)


def endpoint_move_vector(source: dict[str, Any], adjacency: dict[str, Any] | None) -> tuple[str, float, float]:
    source_box = normalize_bbox_dict(source.get("bbox"))
    target_box = normalize_bbox_dict(adjacency.get("target_cluster_bbox") if adjacency else None)
    source_x, source_y = bbox_center(source_box)
    target_x, target_y = bbox_center(target_box) if target_box.get("width") or target_box.get("height") else (source_x + source_box["width"], source_y)
    delta_x = target_x - source_x
    delta_y = target_y - source_y
    horizontal = source_box["width"] >= source_box["height"]
    span = max(source_box["width"] if horizontal else source_box["height"], 1.0)
    step = round(min(24.0, max(6.0, span * 0.08)), 3)
    if horizontal:
        direction = 1.0 if delta_x >= 0 else -1.0
        return ("end" if direction >= 0 else "start", round(step * direction, 3), 0.0)
    direction = 1.0 if delta_y >= 0 else -1.0
    return ("end" if direction >= 0 else "start", 0.0, round(step * direction, 3))


def endpoint_move_candidate_priority(source: dict[str, Any], adjacency: dict[str, Any] | None) -> float:
    source_box = normalize_bbox_dict(source.get("bbox"))
    line_span = max(source_box["width"], source_box["height"])
    base = float(source.get("patch_priority") or 0.0) + min(300.0, line_span)
    if not adjacency:
        return round(base, 3)
    return round(base + adjacency_role_weight(source.get("program_role"), adjacency.get("target_role")), 3)


def build_endpoint_move_candidates(mutable_candidates: list[dict[str, Any]], topology: dict[str, Any] | None, max_candidates: int) -> list[dict[str, Any]]:
    endpoint_candidates: list[dict[str, Any]] = []
    adjacency_by_role = topology_adjacency_targets(topology)
    line_candidates = [
        item
        for item in mutable_candidates
        if item.get("tag") in {"line", "polyline"}
        and item.get("program_cluster_id")
        and max(normalize_bbox_dict(item.get("bbox"))["width"], normalize_bbox_dict(item.get("bbox"))["height"]) >= 12.0
    ]
    seen_indices: set[Any] = set()
    for source in line_candidates:
        element_index = source.get("element_index")
        if element_index in seen_indices:
            continue
        seen_indices.add(element_index)
        adjacency_options = adjacency_by_role.get(str(source.get("program_role") or ""), [])
        adjacency = sorted(adjacency_options, key=lambda item: endpoint_move_candidate_priority(source, item), reverse=True)[0] if adjacency_options else None
        endpoint, dx, dy = endpoint_move_vector(source, adjacency)
        move_index = len(endpoint_candidates) + 1
        candidate = {
            "operation": "move_line_endpoint",
            "operation_id": f"endpoint_move_{move_index:03d}",
            "target_element_index": element_index,
            "tag": source.get("tag"),
            "bbox": source.get("bbox"),
            "center": source.get("center"),
            "program_cluster_id": source.get("program_cluster_id"),
            "program_role": source.get("program_role"),
            "space_region_ids": source.get("space_region_ids", []),
            "connects_to_role": adjacency.get("target_role") if adjacency else None,
            "connects_to_cluster_id": adjacency.get("target_cluster_id") if adjacency else None,
            "adjacency_edge_id": adjacency.get("edge_id") if adjacency else None,
            "adjacency_rationale": adjacency.get("rationale") if adjacency else None,
            "topology_evidence": adjacency.get("evidence") if adjacency else None,
            "addressing": "source_svg_element_index",
            "mutation_policy": "move_existing_line_endpoint_in_same_parent",
            "endpoint": endpoint,
            "dx": dx,
            "dy": dy,
            "endpoint_move_priority": endpoint_move_candidate_priority(source, adjacency),
            "reason": "Create a bounded same-layer endpoint move candidate on a recognized mutable line/polyline program boundary.",
        }
        endpoint_candidates.append(candidate)
        if len(endpoint_candidates) >= max_candidates:
            break
    return sorted(endpoint_candidates, key=lambda item: (float(item.get("endpoint_move_priority") or 0.0), int(item.get("target_element_index") or 0)), reverse=True)


def build_opening_candidates(mutable_candidates: list[dict[str, Any]], topology: dict[str, Any] | None, max_candidates: int) -> list[dict[str, Any]]:
    opening_candidates: list[dict[str, Any]] = []
    adjacency_by_role = topology_adjacency_targets(topology)
    preferred_roles = {"greenery_lounge", "fitness_gx", "golf_screen", "hall_lobby", "sauna_locker_shower"}
    line_candidates = [
        item
        for item in mutable_candidates
        if item.get("tag") == "line" and item.get("program_cluster_id") and item.get("program_role") in preferred_roles
    ]
    fallback_candidates = [item for item in mutable_candidates if item.get("tag") == "line" and item.get("program_cluster_id")]
    seen_indices: set[Any] = set()
    for source in [*line_candidates, *fallback_candidates]:
        element_index = source.get("element_index")
        if element_index in seen_indices:
            continue
        seen_indices.add(element_index)
        opening_index = len(opening_candidates) + 1
        adjacency_options = adjacency_by_role.get(str(source.get("program_role") or ""), [])
        adjacency = sorted(adjacency_options, key=lambda item: opening_candidate_priority(source, item), reverse=True)[0] if adjacency_options else None
        candidate = {
            "operation": "split_line_for_opening",
            "operation_id": f"opening_{opening_index:03d}",
            "target_element_index": element_index,
            "tag": source.get("tag"),
            "bbox": source.get("bbox"),
            "center": source.get("center"),
            "program_cluster_id": source.get("program_cluster_id"),
            "program_role": source.get("program_role"),
            "space_region_ids": source.get("space_region_ids", []),
            "connects_to_role": adjacency.get("target_role") if adjacency else None,
            "connects_to_cluster_id": adjacency.get("target_cluster_id") if adjacency else None,
            "adjacency_edge_id": adjacency.get("edge_id") if adjacency else None,
            "adjacency_rationale": adjacency.get("rationale") if adjacency else None,
            "topology_evidence": adjacency.get("evidence") if adjacency else None,
            "addressing": "source_svg_element_index",
            "mutation_policy": "split_existing_line_in_same_parent",
            "opening_start_ratio": 0.42,
            "opening_end_ratio": 0.58,
            "opening_priority": opening_candidate_priority(source, adjacency),
            "reason": "Create a same-layer wall opening candidate on a recognized mutable program boundary.",
        }
        opening_candidates.append(candidate)
        if len(opening_candidates) >= max_candidates:
            break
    return sorted(opening_candidates, key=lambda item: (float(item.get("opening_priority") or 0.0), int(item.get("target_element_index") or 0)), reverse=True)
