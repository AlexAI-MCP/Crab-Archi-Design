from __future__ import annotations

from typing import Any

from crab_archi_design.solver.sizing import classify_program_role


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


def effective_program_role(candidate: dict[str, Any]) -> str:
    return str(candidate.get("program_role") or candidate.get("solver_projected_role") or "")


def local_search_role_order(solver_objective: dict[str, Any] | None) -> list[str]:
    local_search = (solver_objective or {}).get("local_search") or {}
    order = local_search.get("final_program_order") or []
    return [str(role) for role in order if role]


def local_search_role_rank(solver_objective: dict[str, Any] | None) -> dict[str, int]:
    return {role: index + 1 for index, role in enumerate(local_search_role_order(solver_objective))}


def local_search_placements(solver_objective: dict[str, Any] | None) -> list[dict[str, Any]]:
    local_search = (solver_objective or {}).get("local_search") or {}
    placements = local_search.get("final_placements") or []
    return [item for item in placements if isinstance(item, dict)]


def point_inside_box(point: tuple[float, float], box: dict[str, Any]) -> bool:
    normalized = normalize_bbox_dict(box)
    return (
        normalized["x"] <= point[0] <= normalized["x"] + normalized["width"]
        and normalized["y"] <= point[1] <= normalized["y"] + normalized["height"]
    )


def projected_role_from_solver_plan(candidate: dict[str, Any], solver_objective: dict[str, Any] | None) -> str | None:
    center = bbox_center(candidate.get("bbox") or {})
    for placement in sorted(local_search_placements(solver_objective), key=lambda item: int(item.get("order") or 0)):
        role = str(placement.get("role") or "")
        planned_box = placement.get("planned_box") or {}
        if role and planned_box and point_inside_box(center, planned_box):
            return role
    return None


def local_search_priority_boost(role: str | None, solver_objective: dict[str, Any] | None) -> float:
    rank = local_search_role_rank(solver_objective).get(str(role or ""))
    order_len = len(local_search_role_order(solver_objective))
    if rank is None or order_len <= 0:
        return 0.0
    return round((order_len - rank + 1) * 75.0, 3)


def annotate_candidate_with_solver_plan(candidate: dict[str, Any], solver_objective: dict[str, Any] | None) -> dict[str, Any]:
    projected_role = projected_role_from_solver_plan(candidate, solver_objective) if not candidate.get("program_role") else None
    role = str(candidate.get("program_role") or projected_role or "")
    rank = local_search_role_rank(solver_objective).get(role)
    boost = local_search_priority_boost(role, solver_objective)
    base_priority = float(candidate.get("patch_priority_before_solver_plan", candidate.get("patch_priority") or 0.0))
    candidate["patch_priority_before_solver_plan"] = round(base_priority, 3)
    candidate["solver_plan_source"] = "solver.local_search" if rank is not None else "solver.role_priority"
    candidate["solver_projected_role"] = projected_role
    candidate["local_search_rank"] = rank
    candidate["local_search_priority_boost"] = boost
    candidate["patch_priority"] = round(base_priority + boost, 3)
    return candidate


def annotate_candidates_with_solver_plan(candidates: list[dict[str, Any]], solver_objective: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [annotate_candidate_with_solver_plan(candidate, solver_objective) for candidate in candidates]


def intent_text_fragments(intents: list[dict[str, Any]] | None) -> list[str]:
    fragments: list[str] = []
    for intent in intents or []:
        source = intent.get("source") or {}
        if source.get("text"):
            fragments.append(str(source.get("text")))
        if source.get("type"):
            fragments.append(str(source.get("type")))
        if intent.get("strategy"):
            fragments.append(str(intent.get("strategy")))
        for operation in intent.get("operations", []):
            for key in ["target", "target_hint", "action", "edit_type", "method", "snap_policy"]:
                value = operation.get(key)
                if value:
                    fragments.append(str(value))
    return fragments


def intent_target_roles(intents: list[dict[str, Any]] | None) -> set[str]:
    roles: set[str] = set()
    role_aliases = {
        "greenery_lounge": "greenery_lounge",
        "fitness_gx": "fitness_gx",
        "golf_screen": "golf_screen",
        "sauna_locker_shower": "sauna_locker_shower",
        "hall_lobby": "hall_lobby",
        "management_support": "management_support",
        "main_hall": "hall_lobby",
        "hall": "hall_lobby",
        "lobby": "hall_lobby",
    }
    for fragment in intent_text_fragments(intents):
        role = role_aliases.get(fragment) or classify_program_role(fragment)
        if role:
            roles.add(role)
    return roles


def role_from_intent_fragment(fragment: Any) -> str | None:
    role_aliases = {
        "greenery_lounge": "greenery_lounge",
        "fitness_gx": "fitness_gx",
        "golf_screen": "golf_screen",
        "sauna_locker_shower": "sauna_locker_shower",
        "hall_lobby": "hall_lobby",
        "management_support": "management_support",
        "main_hall": "hall_lobby",
        "hall": "hall_lobby",
        "lobby": "hall_lobby",
    }
    text = str(fragment or "")
    return role_aliases.get(text) or classify_program_role(text)


def intent_primary_target_roles(intents: list[dict[str, Any]] | None) -> set[str]:
    roles: set[str] = set()
    for intent in intents or []:
        for operation in intent.get("operations", []):
            for key in ["target", "target_hint"]:
                role = role_from_intent_fragment(operation.get(key))
                if role:
                    roles.add(role)
    return roles or intent_target_roles(intents)


def intent_requests_opening(intents: list[dict[str, Any]] | None) -> bool:
    text = " ".join(intent_text_fragments(intents)).lower()
    return any(term in text for term in ["open", "opening", "connect", "connection", "개방", "열", "연결", "출입", "동선"])


def intent_requests_partition_rework(intents: list[dict[str, Any]] | None) -> bool:
    text = " ".join(intent_text_fragments(intents)).lower()
    return any(term in text for term in ["merge", "rework", "partition", "revise", "통합", "구획", "벽", "파티션", "재구성", "개선"])


def candidate_intent_roles(candidate: dict[str, Any]) -> set[str]:
    roles = {
        str(candidate.get("program_role") or ""),
        str(candidate.get("solver_projected_role") or ""),
        str(candidate.get("connects_to_role") or ""),
    }
    return {role for role in roles if role}


def candidate_primary_intent_roles(candidate: dict[str, Any]) -> set[str]:
    return {role for role in [effective_program_role(candidate)] if role}


def candidate_connector_intent_roles(candidate: dict[str, Any]) -> set[str]:
    return {role for role in [str(candidate.get("connects_to_role") or "")] if role}


def intent_candidate_boost(candidate: dict[str, Any], intents: list[dict[str, Any]] | None, operation_kind: str) -> float:
    target_roles = intent_target_roles(intents)
    if not target_roles:
        return 0.0
    primary_matches = target_roles & candidate_primary_intent_roles(candidate)
    connector_matches = target_roles & candidate_connector_intent_roles(candidate)
    if not primary_matches and not connector_matches:
        return 0.0
    boost = 0.0
    if primary_matches:
        boost += 420.0 + 180.0 * len(primary_matches)
    if connector_matches:
        boost += 80.0 if primary_matches else 160.0
    if operation_kind == "opening" and intent_requests_opening(intents):
        boost += 320.0 if primary_matches else 80.0
    if operation_kind == "partition_remove" and primary_matches and intent_requests_partition_rework(intents):
        boost += 240.0
    if operation_kind == "endpoint_move" and intent_requests_opening(intents):
        boost += 120.0 if primary_matches else 40.0
    return round(boost, 3)


def annotate_candidate_with_intents(candidate: dict[str, Any], intents: list[dict[str, Any]] | None, priority_field: str, operation_kind: str) -> dict[str, Any]:
    base = float(candidate.get(f"{priority_field}_before_intent", candidate.get(priority_field) or 0.0))
    boost = intent_candidate_boost(candidate, intents, operation_kind)
    candidate[f"{priority_field}_before_intent"] = round(base, 3)
    candidate["intent_target_roles"] = sorted(intent_target_roles(intents))
    candidate["intent_priority_boost"] = boost
    candidate["intent_projection_source"] = "solver_input.intents" if boost > 0 else None
    candidate[priority_field] = round(base + boost, 3)
    return candidate


def project_intents_onto_patch_plan(plan: dict[str, Any], intents: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not intents:
        plan["intent_projection"] = {"status": "no_intents", "target_roles": [], "boosted_candidate_count": 0}
        return plan
    target_roles = sorted(intent_target_roles(intents))
    boosted_count = 0
    for candidate in plan.get("same_layer_mutable_candidates", []):
        annotate_candidate_with_intents(candidate, intents, "patch_priority", "partition_remove")
        boosted_count += 1 if candidate.get("intent_priority_boost") else 0
    for candidate in plan.get("same_layer_opening_candidates", []):
        annotate_candidate_with_intents(candidate, intents, "opening_priority", "opening")
        boosted_count += 1 if candidate.get("intent_priority_boost") else 0
    for candidate in plan.get("same_layer_endpoint_move_candidates", []):
        annotate_candidate_with_intents(candidate, intents, "endpoint_move_priority", "endpoint_move")
        boosted_count += 1 if candidate.get("intent_priority_boost") else 0
    plan["same_layer_mutable_candidates"] = sorted(
        plan.get("same_layer_mutable_candidates", []),
        key=lambda item: (float(item.get("patch_priority") or 0.0), float((item.get("bbox") or {}).get("width") or 0.0) * float((item.get("bbox") or {}).get("height") or 0.0)),
        reverse=True,
    )
    plan["same_layer_opening_candidates"] = sorted(
        plan.get("same_layer_opening_candidates", []),
        key=lambda item: (float(item.get("opening_priority") or 0.0), int(item.get("target_element_index") or 0)),
        reverse=True,
    )
    plan["same_layer_endpoint_move_candidates"] = sorted(
        plan.get("same_layer_endpoint_move_candidates", []),
        key=lambda item: (float(item.get("endpoint_move_priority") or 0.0), int(item.get("target_element_index") or 0)),
        reverse=True,
    )
    plan["intent_projection"] = {
        "status": "active" if target_roles else "no_target_roles",
        "target_roles": target_roles,
        "primary_target_roles": sorted(intent_primary_target_roles(intents)),
        "opening_requested": intent_requests_opening(intents),
        "partition_rework_requested": intent_requests_partition_rework(intents),
        "boosted_candidate_count": boosted_count,
    }
    return plan


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
        + adjacency_role_weight(effective_program_role(source), adjacency.get("target_role"))
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
    return round(base + adjacency_role_weight(effective_program_role(source), adjacency.get("target_role")), 3)


def build_endpoint_move_candidates(mutable_candidates: list[dict[str, Any]], topology: dict[str, Any] | None, max_candidates: int) -> list[dict[str, Any]]:
    endpoint_candidates: list[dict[str, Any]] = []
    adjacency_by_role = topology_adjacency_targets(topology)
    line_candidates = [
        item
        for item in mutable_candidates
        if item.get("tag") in {"line", "polyline", "path"}
        and (item.get("program_cluster_id") or effective_program_role(item))
        and max(normalize_bbox_dict(item.get("bbox"))["width"], normalize_bbox_dict(item.get("bbox"))["height"]) >= 12.0
    ]
    seen_indices: set[Any] = set()
    for source in line_candidates:
        element_index = source.get("element_index")
        if element_index in seen_indices:
            continue
        seen_indices.add(element_index)
        source_role = effective_program_role(source)
        adjacency_options = adjacency_by_role.get(source_role, [])
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
            "program_role": source_role or source.get("program_role"),
            "source_program_role": source.get("program_role"),
            "solver_projected_role": source.get("solver_projected_role"),
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
            "reason": "Create a bounded same-layer endpoint move candidate on a recognized mutable line/polyline/path program boundary.",
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
        if item.get("tag") == "line" and (item.get("program_cluster_id") or effective_program_role(item)) and effective_program_role(item) in preferred_roles
    ]
    polyline_candidates = [
        item
        for item in mutable_candidates
        if item.get("tag") == "polyline" and (item.get("program_cluster_id") or effective_program_role(item)) and effective_program_role(item) in preferred_roles
    ]
    path_candidates = [
        item
        for item in mutable_candidates
        if item.get("tag") == "path" and (item.get("program_cluster_id") or effective_program_role(item)) and effective_program_role(item) in preferred_roles
    ]
    fallback_candidates = [item for item in mutable_candidates if item.get("tag") == "line" and (item.get("program_cluster_id") or effective_program_role(item))]
    fallback_polyline_candidates = [item for item in mutable_candidates if item.get("tag") == "polyline" and (item.get("program_cluster_id") or effective_program_role(item))]
    fallback_path_candidates = [item for item in mutable_candidates if item.get("tag") == "path" and (item.get("program_cluster_id") or effective_program_role(item))]
    seen_indices: set[Any] = set()
    for source in [*line_candidates, *polyline_candidates, *path_candidates, *fallback_candidates, *fallback_polyline_candidates, *fallback_path_candidates]:
        element_index = source.get("element_index")
        if element_index in seen_indices:
            continue
        seen_indices.add(element_index)
        opening_index = len(opening_candidates) + 1
        source_role = effective_program_role(source)
        adjacency_options = adjacency_by_role.get(source_role, [])
        adjacency = sorted(adjacency_options, key=lambda item: opening_candidate_priority(source, item), reverse=True)[0] if adjacency_options else None
        candidate = {
            "operation": "split_line_for_opening",
            "operation_id": f"opening_{opening_index:03d}",
            "target_element_index": element_index,
            "tag": source.get("tag"),
            "bbox": source.get("bbox"),
            "center": source.get("center"),
            "program_cluster_id": source.get("program_cluster_id"),
            "program_role": source_role or source.get("program_role"),
            "source_program_role": source.get("program_role"),
            "solver_projected_role": source.get("solver_projected_role"),
            "space_region_ids": source.get("space_region_ids", []),
            "connects_to_role": adjacency.get("target_role") if adjacency else None,
            "connects_to_cluster_id": adjacency.get("target_cluster_id") if adjacency else None,
            "adjacency_edge_id": adjacency.get("edge_id") if adjacency else None,
            "adjacency_rationale": adjacency.get("rationale") if adjacency else None,
            "topology_evidence": adjacency.get("evidence") if adjacency else None,
            "addressing": "source_svg_element_index",
            "mutation_policy": f"split_existing_{source.get('tag')}_in_same_parent",
            "opening_start_ratio": 0.42,
            "opening_end_ratio": 0.58,
            "opening_priority": opening_candidate_priority(source, adjacency),
            "reason": "Create a same-layer wall opening candidate on a recognized mutable line/polyline/path program boundary.",
        }
        opening_candidates.append(candidate)
        if len(opening_candidates) >= max_candidates:
            break
    return sorted(opening_candidates, key=lambda item: (float(item.get("opening_priority") or 0.0), int(item.get("target_element_index") or 0)), reverse=True)
