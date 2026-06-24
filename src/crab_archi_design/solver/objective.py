from __future__ import annotations

from typing import Any

from crab_archi_design.solver.feasible import build_feasible_report
from crab_archi_design.solver.placement import build_initial_placement_report
from crab_archi_design.solver.scale import resolve_architectural_scale
from crab_archi_design.solver.sizing import extract_program_targets


def topology_nodes(topology: dict[str, Any] | None, node_type: str) -> list[dict[str, Any]]:
    return [node for node in (topology or {}).get("nodes", []) if node.get("type") == node_type]


def topology_edges(topology: dict[str, Any] | None, edge_type: str) -> list[dict[str, Any]]:
    return [edge for edge in (topology or {}).get("edges", []) if edge.get("type") == edge_type]


def cluster_area_world(cluster: dict[str, Any]) -> float:
    if cluster.get("area") is not None:
        try:
            return float(cluster.get("area") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    box = cluster.get("bbox") or {}
    try:
        return float(box.get("width") or 0.0) * float(box.get("height") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def program_cluster_summary(topology: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    clusters = topology_nodes(topology, "program_cluster")
    by_role: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        role = str(cluster.get("role") or "")
        if not role:
            continue
        entry = by_role.setdefault(
            role,
            {
                "role": role,
                "cluster_count": 0,
                "recognized_area_world": 0.0,
                "space_region_count": 0,
                "cluster_ids": [],
            },
        )
        entry["cluster_count"] += 1
        entry["recognized_area_world"] += cluster_area_world(cluster)
        entry["space_region_count"] += int(cluster.get("space_region_count") or 0)
        entry["cluster_ids"].append(cluster.get("id"))
    for entry in by_role.values():
        entry["recognized_area_world"] = round(float(entry["recognized_area_world"]), 3)
    return by_role


def adjacency_summary(topology: dict[str, Any] | None) -> dict[str, Any]:
    edges = topology_edges(topology, "ontology_cluster_adjacency_target")
    return {
        "ontology_cluster_adjacency_target_count": len(edges),
        "pairs": [
            {
                "source": edge.get("source"),
                "target": edge.get("target"),
                "left_role": edge.get("left_role"),
                "right_role": edge.get("right_role"),
                "evidence": edge.get("evidence"),
            }
            for edge in edges
        ],
    }


def calibrated_area_m2(area_world: float | None, scale_report: dict[str, Any]) -> float | None:
    if area_world is None or scale_report.get("status") != "active":
        return None
    mm_per_world = scale_report.get("mm_per_world")
    try:
        scale = float(mm_per_world)
    except (TypeError, ValueError):
        return None
    return round(float(area_world) * scale * scale / 1_000_000.0, 3)


def area_comparison(target_m2: Any, recognized_m2: float | None, has_cluster: bool) -> dict[str, Any]:
    if not has_cluster or target_m2 is None:
        return {"status": "missing_target_or_cluster"}
    if recognized_m2 is None:
        return {"status": "needs_drawing_scale_calibration"}
    target = float(target_m2)
    delta = round(recognized_m2 - target, 3)
    ratio = round(recognized_m2 / target, 3) if target > 0 else None
    return {
        "status": "calibrated_comparison",
        "recognized_area_m2": recognized_m2,
        "area_delta_m2": delta,
        "area_ratio": ratio,
    }


def evaluate_topology_fit(
    topology: dict[str, Any] | None,
    standards: dict[str, Any] | None,
    constraints: dict[str, Any] | None,
    recognition_ir: dict[str, Any] | None = None,
    scale_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = extract_program_targets(standards)
    target_by_role = {str(target["role"]): target for target in targets}
    cluster_by_role = program_cluster_summary(topology)
    scale_report = resolve_architectural_scale(recognition_ir, scale_manifest)
    roles = sorted(set(target_by_role) | set(cluster_by_role))
    role_evaluations: list[dict[str, Any]] = []
    for role in roles:
        target = target_by_role.get(role, {})
        cluster = cluster_by_role.get(role, {})
        recognized_area_m2 = calibrated_area_m2(cluster.get("recognized_area_world"), scale_report)
        comparison = area_comparison(target.get("target_area_m2"), recognized_area_m2, bool(cluster))
        role_evaluations.append(
            {
                "role": role,
                "target_area_pyeong": target.get("target_area_pyeong"),
                "target_area_m2": target.get("target_area_m2"),
                "target_row_count": target.get("row_count", 0),
                "target_aggregation_policy": target.get("aggregation_policy"),
                "cluster_count": cluster.get("cluster_count", 0),
                "space_region_count": cluster.get("space_region_count", 0),
                "recognized_area_world": cluster.get("recognized_area_world"),
                "recognized_area_m2": comparison.get("recognized_area_m2"),
                "area_delta_m2": comparison.get("area_delta_m2"),
                "area_ratio": comparison.get("area_ratio"),
                "area_comparison_status": comparison["status"],
                "cluster_ids": cluster.get("cluster_ids", []),
            }
        )
    feasible = build_feasible_report(constraints)
    placement = build_initial_placement_report(standards, constraints, topology)
    target_roles = {str(target["role"]) for target in targets}
    recognized_roles = set(cluster_by_role)
    covered_roles = sorted(target_roles & recognized_roles)
    missing_target_roles = sorted(target_roles - recognized_roles)
    return {
        "schema": "crab-archi-design-objective-report-v1",
        "status": "active" if topology and (targets or cluster_by_role) else "review_required",
        "target_program_count": len(targets),
        "recognized_program_role_count": len(cluster_by_role),
        "covered_target_role_count": len(covered_roles),
        "covered_target_roles": covered_roles,
        "missing_target_roles": missing_target_roles,
        "scale_calibration": scale_report,
        "role_evaluations": role_evaluations,
        "feasible_report": {
            key: value
            for key, value in feasible.items()
            if key not in {"shell_polygons", "mutable_polygons", "protected_polygons"}
        },
        "initial_placement": placement,
        "adjacency_summary": adjacency_summary(topology),
        "notes": [
            "Area comparison remains advisory until drawing scale is calibrated from architectural dimensions or OCR.",
            "This objective report is a deterministic solver input gate, not an LLM-generated design proposal.",
        ],
    }
