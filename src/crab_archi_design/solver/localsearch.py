from __future__ import annotations

from typing import Any

from crab_archi_design.solver.placement import box_area, role_priority, safe_float, slice_program_boxes


def role_pair(left: Any, right: Any) -> tuple[str, str] | None:
    left_role = str(left or "")
    right_role = str(right or "")
    if not left_role or not right_role or left_role == right_role:
        return None
    return tuple(sorted((left_role, right_role)))


def adjacency_targets(topology: dict[str, Any] | None) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for index, edge in enumerate((topology or {}).get("edges", []), start=1):
        if edge.get("type") != "ontology_cluster_adjacency_target":
            continue
        pair = role_pair(edge.get("left_role"), edge.get("right_role"))
        if pair is None:
            continue
        weight = safe_float(edge.get("weight"), 0.0) or safe_float(edge.get("priority"), 0.0) or 1.0
        targets.append(
            {
                "id": edge.get("id") or f"adjacency_target_{index:03d}",
                "left_role": pair[0],
                "right_role": pair[1],
                "source": edge.get("source"),
                "target": edge.get("target"),
                "weight": round(max(0.0, weight), 3),
                "rationale": edge.get("rationale"),
                "evidence": edge.get("evidence"),
            }
        )
    return targets


def boxes_touch(left: dict[str, Any], right: dict[str, Any], tolerance: float = 0.01) -> bool:
    lx = safe_float(left.get("x"))
    ly = safe_float(left.get("y"))
    lw = safe_float(left.get("width"))
    lh = safe_float(left.get("height"))
    rx = safe_float(right.get("x"))
    ry = safe_float(right.get("y"))
    rw = safe_float(right.get("width"))
    rh = safe_float(right.get("height"))
    if lw <= 0 or lh <= 0 or rw <= 0 or rh <= 0:
        return False

    left_right = lx + lw
    right_right = rx + rw
    left_bottom = ly + lh
    right_bottom = ry + rh
    x_overlap = min(left_right, right_right) - max(lx, rx)
    y_overlap = min(left_bottom, right_bottom) - max(ly, ry)
    vertical_touch = (abs(left_right - rx) <= tolerance or abs(right_right - lx) <= tolerance) and y_overlap > tolerance
    horizontal_touch = (abs(left_bottom - ry) <= tolerance or abs(right_bottom - ly) <= tolerance) and x_overlap > tolerance
    return vertical_touch or horizontal_touch


def adjacent_pairs_for_placements(placements: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for left, right in zip(placements, placements[1:]):
        pair = role_pair(left.get("role"), right.get("role"))
        if pair is None:
            continue
        left_box = left.get("planned_box") or {}
        right_box = right.get("planned_box") or {}
        if not left_box or not right_box or boxes_touch(left_box, right_box):
            pairs.add(pair)
    return pairs


def target_from_placement(placement: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": placement.get("role"),
        "target_area_m2": placement.get("target_area_m2"),
        "target_area_pyeong": placement.get("target_area_pyeong"),
    }


def reslice_order(order: list[str], placement_report: dict[str, Any]) -> list[dict[str, Any]]:
    by_role = {str(item.get("role")): target_from_placement(item) for item in placement_report.get("placements", [])}
    targets = [by_role[role] for role in order if role in by_role]
    box = placement_report.get("placement_bbox") or {}
    return slice_program_boxes(targets, box) if box_area(box) > 0 else []


def hierarchy_inversion_penalty(order: list[str]) -> float:
    penalty = 0.0
    for left_index, left_role in enumerate(order):
        left_priority = role_priority(left_role)
        for right_role in order[left_index + 1 :]:
            right_priority = role_priority(right_role)
            if left_priority < right_priority:
                penalty += right_priority - left_priority
    return round(penalty, 3)


def anchor_stability_penalty(order: list[str], anchor_order: list[str]) -> float:
    anchor_index = {role: index for index, role in enumerate(anchor_order)}
    penalty = 0.0
    for index, role in enumerate(order):
        if role in anchor_index:
            penalty += abs(index - anchor_index[role]) * role_priority(role) / 1000.0
    return round(penalty, 6)


def score_placements(
    placements: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    anchor_order: list[str],
) -> dict[str, Any]:
    order = [str(item.get("role") or "") for item in placements if item.get("role")]
    adjacent_pairs = adjacent_pairs_for_placements(placements)
    target_reports = []
    satisfied_weight = 0.0
    for target in targets:
        pair = role_pair(target.get("left_role"), target.get("right_role"))
        satisfied = pair in adjacent_pairs if pair else False
        weight = safe_float(target.get("weight"), 1.0)
        if satisfied:
            satisfied_weight += weight
        target_reports.append({**target, "status": "satisfied" if satisfied else "missing"})
    total_weight = sum(safe_float(item.get("weight"), 1.0) for item in targets)
    hierarchy_penalty = hierarchy_inversion_penalty(order)
    stability_penalty = anchor_stability_penalty(order, anchor_order)
    score = round(satisfied_weight * 1000.0 - hierarchy_penalty - stability_penalty, 6)
    return {
        "score": score,
        "satisfied_weight": round(satisfied_weight, 3),
        "missing_weight": round(max(0.0, total_weight - satisfied_weight), 3),
        "hierarchy_inversion_penalty": hierarchy_penalty,
        "anchor_stability_penalty": stability_penalty,
        "satisfied_adjacency_count": sum(1 for item in target_reports if item["status"] == "satisfied"),
        "adjacency_target_count": len(target_reports),
        "adjacent_role_pairs": [{"left_role": pair[0], "right_role": pair[1]} for pair in sorted(adjacent_pairs)],
        "adjacency_targets": target_reports,
    }


def build_local_search_report(
    placement_report: dict[str, Any] | None,
    topology: dict[str, Any] | None,
    max_passes: int = 12,
) -> dict[str, Any]:
    placement_report = placement_report or {}
    initial_placements = list(placement_report.get("placements") or [])
    targets = adjacency_targets(topology)
    anchor_order = [str(item.get("role") or "") for item in initial_placements if item.get("role")]
    gates = {
        "initial_placement_active": placement_report.get("status") == "active",
        "initial_placements_found": bool(initial_placements),
        "placement_bbox_positive": box_area(placement_report.get("placement_bbox") or {}) > 0,
    }
    if not all(gates.values()):
        return {
            "schema": "crab-archi-design-local-search-report-v1",
            "status": "review_required",
            "strategy": "deterministic_adjacent_swap_hill_climb",
            "gates": gates,
            "adjacency_target_count": len(targets),
            "satisfied_adjacency_count": 0,
            "initial_score": None,
            "final_score": None,
            "improvement": None,
            "iteration_count": 0,
            "accepted_move_count": 0,
            "adjacency_targets": targets,
            "satisfied_adjacency_targets": [],
            "final_program_order": anchor_order,
            "final_placements": initial_placements,
            "moves": [],
        }

    current_placements = initial_placements
    current_order = anchor_order
    initial_detail = score_placements(current_placements, targets, anchor_order)
    current_detail = dict(initial_detail)
    moves: list[dict[str, Any]] = []
    iteration_count = 0

    for pass_index in range(1, max(1, max_passes) + 1):
        best: dict[str, Any] | None = None
        for swap_index in range(0, max(0, len(current_order) - 1)):
            iteration_count += 1
            candidate_order = list(current_order)
            candidate_order[swap_index], candidate_order[swap_index + 1] = candidate_order[swap_index + 1], candidate_order[swap_index]
            candidate_report = {**placement_report, "placements": current_placements}
            candidate_placements = reslice_order(candidate_order, candidate_report)
            candidate_detail = score_placements(candidate_placements, targets, anchor_order)
            if candidate_detail["score"] <= current_detail["score"]:
                continue
            candidate_key = (
                candidate_detail["score"],
                candidate_detail["satisfied_weight"],
                -candidate_detail["hierarchy_inversion_penalty"],
                -candidate_detail["anchor_stability_penalty"],
                -swap_index,
            )
            if best is None or candidate_key > best["key"]:
                best = {
                    "key": candidate_key,
                    "pass": pass_index,
                    "swap_index": swap_index,
                    "before_order": current_order,
                    "after_order": candidate_order,
                    "before_score": current_detail["score"],
                    "after_score": candidate_detail["score"],
                    "score_delta": round(candidate_detail["score"] - current_detail["score"], 6),
                    "placements": candidate_placements,
                    "detail": candidate_detail,
                }
        if best is None:
            break
        moves.append(
            {
                "pass": best["pass"],
                "swap_index": best["swap_index"],
                "before_order": best["before_order"],
                "after_order": best["after_order"],
                "before_score": best["before_score"],
                "after_score": best["after_score"],
                "score_delta": best["score_delta"],
                "rationale": "adjacent_swap_improves_opencrab_adjacency_score",
            }
        )
        current_order = best["after_order"]
        current_placements = best["placements"]
        current_detail = best["detail"]

    satisfied_targets = [item for item in current_detail["adjacency_targets"] if item["status"] == "satisfied"]
    return {
        "schema": "crab-archi-design-local-search-report-v1",
        "status": "active",
        "strategy": "deterministic_adjacent_swap_hill_climb",
        "gates": gates,
        "max_passes": max_passes,
        "initial_score": initial_detail["score"],
        "final_score": current_detail["score"],
        "improvement": round(current_detail["score"] - initial_detail["score"], 6),
        "iteration_count": iteration_count,
        "accepted_move_count": len(moves),
        "adjacency_target_count": current_detail["adjacency_target_count"],
        "satisfied_adjacency_count": current_detail["satisfied_adjacency_count"],
        "initial_score_detail": initial_detail,
        "final_score_detail": current_detail,
        "adjacency_targets": current_detail["adjacency_targets"],
        "satisfied_adjacency_targets": satisfied_targets,
        "final_program_order": current_order,
        "final_placements": current_placements,
        "moves": moves,
    }
