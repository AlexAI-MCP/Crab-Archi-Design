from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

from crab_archi_design.solver.svg_edit_ops import (
    collapse_linear_element_to_zero_length,
    count_images,
    edit_capability_report,
    local_tag,
    move_line_endpoint,
    preserve_original_attrs,
    split_line_for_opening,
    split_polyline_for_opening,
    split_path_for_opening,
    svg_float,
)
from crab_archi_design.svg.transform import Matrix, identity_matrix, multiply_matrix, parse_transform


def existing_elements_by_document_index(root: Element) -> dict[int, Element]:
    return {index: element for index, element in enumerate(root.iter(), start=1)}


def element_matrices_by_document_index(root: Element) -> dict[int, Matrix]:
    matrices: dict[int, Matrix] = {}
    index = 1

    def walk(element: Element, parent_matrix: Matrix) -> None:
        nonlocal index
        current_index = index
        index += 1
        matrix = multiply_matrix(parent_matrix, parse_transform(element.attrib.get("transform")))
        matrices[current_index] = matrix
        for child in list(element):
            walk(child, matrix)

    walk(root, identity_matrix())
    return matrices


def parents_by_document_index(root: Element) -> dict[int, Element | None]:
    parents: dict[int, Element | None] = {}
    index = 1

    def walk(element: Element, parent: Element | None) -> None:
        nonlocal index
        current_index = index
        index += 1
        parents[current_index] = parent
        for child in list(element):
            walk(child, element)

    walk(root, None)
    return parents


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, int]:
    bbox = candidate.get("bbox") or {}
    area = float(bbox.get("width") or 0.0) * float(bbox.get("height") or 0.0)
    return (float(candidate.get("patch_priority") or 0.0), area, int(candidate.get("element_index") or 0))


def locked_candidate_indices(plan: dict[str, Any]) -> set[int]:
    indices: set[int] = set()
    for candidate in plan.get("locked_candidates", []):
        element_index = selected_candidate_index(candidate)
        if element_index is not None:
            indices.add(element_index)
    return indices


def locked_target_skip(candidate: dict[str, Any], element_index: int | None, operation: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "element_index": element_index,
        "target_element_index": element_index,
        "tag": candidate.get("tag"),
        "role_hint": candidate.get("role_hint"),
        "program_cluster_id": candidate.get("program_cluster_id"),
        "program_role": candidate.get("program_role"),
        "reason": "target element is locked/protected by patch plan",
    }


def select_candidates(plan: dict[str, Any], max_mutations: int, locked_indices: set[int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    locked_indices = locked_indices or set()
    candidates = [
        candidate
        for candidate in plan.get("same_layer_mutable_candidates", [])
        if candidate.get("mutation_policy") == "modify_or_remove_existing_element_only"
    ]
    candidates = sorted(candidates, key=candidate_sort_key, reverse=True)

    wall_candidates = [candidate for candidate in candidates if candidate.get("role_hint") == "wall_candidate"]
    clustered = [candidate for candidate in wall_candidates if candidate.get("program_cluster_id")]
    selected_pool = clustered or wall_candidates or candidates
    selected: list[dict[str, Any]] = []
    locked_skips: list[dict[str, Any]] = []
    for candidate in selected_pool:
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            locked_skips.append(locked_target_skip(candidate, element_index, "remove_or_patch_existing_element"))
            continue
        selected.append(candidate)
        if len(selected) >= max_mutations:
            break
    return selected, locked_skips


def selected_candidate_index(candidate: dict[str, Any]) -> int | None:
    raw_index = candidate.get("element_index", candidate.get("target_element_index"))
    try:
        index = int(raw_index or 0)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def element_state(element: Element) -> dict[str, Any]:
    return {
        "tag": element.tag,
        "attrib": dict(sorted(element.attrib.items())),
        "text": element.text,
        "tail": element.tail,
        "children": [element_state(child) for child in list(element)],
    }


def capture_locked_element_states(root: Element, plan: dict[str, Any]) -> list[dict[str, Any]]:
    element_map = existing_elements_by_document_index(root)
    captured: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in plan.get("locked_candidates", []):
        element_index = selected_candidate_index(candidate)
        if element_index is None or element_index in seen:
            continue
        seen.add(element_index)
        element = element_map.get(element_index)
        if element is None:
            captured.append(
                {
                    "element_index": element_index,
                    "tag": candidate.get("tag"),
                    "role_hint": candidate.get("role_hint"),
                    "reason": "locked candidate element not found",
                    "element": None,
                    "state": None,
                }
            )
            continue
        captured.append(
            {
                "element_index": element_index,
                "tag": local_tag(element),
                "role_hint": candidate.get("role_hint"),
                "reason": candidate.get("reason"),
                "element": element,
                "state": element_state(element),
            }
        )
    return captured


def locked_preservation_report(captured: list[dict[str, Any]]) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    mutated: list[dict[str, Any]] = []
    preserved_count = 0
    for item in captured:
        element = item.get("element")
        if element is None:
            missing.append(
                {
                    "element_index": item.get("element_index"),
                    "tag": item.get("tag"),
                    "role_hint": item.get("role_hint"),
                    "reason": item.get("reason"),
                }
            )
            continue
        if element_state(element) == item.get("state"):
            preserved_count += 1
            continue
        mutated.append(
            {
                "element_index": item.get("element_index"),
                "tag": item.get("tag"),
                "role_hint": item.get("role_hint"),
                "reason": item.get("reason"),
            }
        )
    return {
        "locked_candidate_count": len(captured),
        "locked_preserved_count": preserved_count,
        "locked_missing_count": len(missing),
        "locked_mutated_count": len(mutated),
        "locked_missing": missing,
        "locked_mutations": mutated,
        "locked_geometry_unchanged": len(captured) == preserved_count and not missing and not mutated,
    }


def opening_sort_key(candidate: dict[str, Any]) -> tuple[float, int]:
    return (float(candidate.get("opening_priority", candidate.get("patch_priority") or 0.0)), int(candidate.get("target_element_index") or 0))


def endpoint_move_sort_key(candidate: dict[str, Any]) -> tuple[float, int]:
    return (float(candidate.get("endpoint_move_priority", candidate.get("patch_priority") or 0.0)), int(candidate.get("target_element_index") or 0))


def candidate_capability_summary(
    candidates: list[dict[str, Any]],
    operation: str,
    element_map: dict[int, Element],
    parent_map: dict[int, Element | None] | None = None,
    transform_map: dict[int, Matrix] | None = None,
    locked_indices: set[int] | None = None,
    max_review_examples: int = 12,
) -> dict[str, Any]:
    parent_map = parent_map or {}
    transform_map = transform_map or {}
    locked_indices = locked_indices or set()
    by_tag: dict[str, dict[str, int]] = {}
    supported: list[dict[str, Any]] = []
    review_required: list[dict[str, Any]] = []
    locked: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for candidate in candidates:
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            locked.append(
                {
                    "target_element_index": element_index,
                    "tag": candidate.get("tag"),
                    "reason": "target element is locked/protected by patch plan",
                }
            )
            continue
        element = element_map.get(element_index or -1)
        if element_index is None or element is None:
            missing.append(
                {
                    "target_element_index": element_index,
                    "tag": candidate.get("tag"),
                    "reason": "target element not found",
                }
            )
            continue
        report = edit_capability_report(element, parent_map.get(element_index), transform_map.get(element_index))
        tag = report["tag"]
        tag_counts = by_tag.setdefault(tag, {"supported": 0, "review_required": 0})
        operation_report = report["operations"][operation]
        item = {
            "target_element_index": element_index,
            "tag": tag,
            "id": report.get("id"),
            "status": operation_report["status"],
            "reason": operation_report["reason"],
            "supported_operations": report["supported_operations"],
        }
        if operation_report["status"] == "supported":
            tag_counts["supported"] += 1
            supported.append(item)
        else:
            tag_counts["review_required"] += 1
            if len(review_required) < max_review_examples:
                review_required.append(item)
    return {
        "operation": operation,
        "candidate_count": len(candidates),
        "supported_count": len(supported),
        "review_required_count": sum(item["review_required"] for item in by_tag.values()),
        "locked_count": len(locked),
        "missing_count": len(missing),
        "by_tag": by_tag,
        "review_required_examples": review_required,
        "locked_examples": locked[:max_review_examples],
        "missing_examples": missing[:max_review_examples],
    }


def edit_capability_summary(
    plan: dict[str, Any],
    element_map: dict[int, Element],
    parent_map: dict[int, Element | None],
    transform_map: dict[int, Matrix],
    locked_indices: set[int],
) -> dict[str, Any]:
    mutable_candidates = [
        candidate
        for candidate in plan.get("same_layer_mutable_candidates", [])
        if candidate.get("mutation_policy") == "modify_or_remove_existing_element_only"
    ]
    return {
        "opening_split": candidate_capability_summary(
            plan.get("same_layer_opening_candidates", []),
            "opening_split",
            element_map,
            parent_map=parent_map,
            transform_map=transform_map,
            locked_indices=locked_indices,
        ),
        "endpoint_move": candidate_capability_summary(
            plan.get("same_layer_endpoint_move_candidates", []),
            "endpoint_move",
            element_map,
            parent_map=parent_map,
            transform_map=transform_map,
            locked_indices=locked_indices,
        ),
        "partition_remove": candidate_capability_summary(
            mutable_candidates,
            "partition_remove",
            element_map,
            parent_map=parent_map,
            transform_map=transform_map,
            locked_indices=locked_indices,
        ),
    }


def edit_capability_totals(capability_summary: dict[str, Any]) -> dict[str, int]:
    totals = {
        "operation_count": 0,
        "candidate_count": 0,
        "supported_count": 0,
        "review_required_count": 0,
        "locked_count": 0,
        "missing_count": 0,
        "manual_review_count": 0,
    }
    for item in capability_summary.values():
        if not isinstance(item, dict):
            continue
        totals["operation_count"] += 1
        for key in ["candidate_count", "supported_count", "review_required_count", "locked_count", "missing_count"]:
            totals[key] += int(item.get(key) or 0)
    totals["manual_review_count"] = totals["review_required_count"] + totals["locked_count"] + totals["missing_count"]
    return totals


def review_required_skip(
    candidate: dict[str, Any],
    element_index: int | None,
    action: str,
    capability_report: dict[str, Any],
    operation_name: str,
) -> dict[str, Any]:
    operation_report = capability_report["operations"][operation_name]
    return {
        "action": action,
        "operation": candidate.get("operation", action),
        "status": "skipped",
        "review_required": True,
        "reason": operation_report["reason"],
        "capability_status": operation_report["status"],
        "target_element_index": element_index,
        "tag": capability_report.get("tag"),
        "id": capability_report.get("id"),
        "supported_operations": capability_report.get("supported_operations", []),
        "review_required_operations": capability_report.get("review_required_operations", []),
        "geometry_mutated": False,
    }


def apply_endpoint_move_candidates(
    root: Element,
    plan: dict[str, Any],
    max_endpoint_moves: int,
    locked_indices: set[int] | None = None,
    excluded_indices: set[int] | None = None,
    element_map: dict[int, Element] | None = None,
    parent_map: dict[int, Element | None] | None = None,
    transform_map: dict[int, Matrix] | None = None,
) -> dict[str, Any]:
    locked_indices = locked_indices or set()
    excluded_indices = excluded_indices or set()
    element_map = element_map or existing_elements_by_document_index(root)
    parent_map = parent_map or parents_by_document_index(root)
    transform_map = transform_map or element_matrices_by_document_index(root)
    candidates = sorted(plan.get("same_layer_endpoint_move_candidates", []), key=endpoint_move_sort_key, reverse=True)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    locked_skips: list[dict[str, Any]] = []
    moved_indices: set[int] = set()
    for candidate in candidates:
        if len(applied) >= max_endpoint_moves:
            break
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            skip = locked_target_skip(candidate, element_index, "move_line_endpoint")
            skipped.append(skip)
            locked_skips.append(skip)
            continue
        if element_index in excluded_indices:
            skipped.append({"target_element_index": element_index, "operation": "move_line_endpoint", "reason": "target element already mutated by earlier operation"})
            continue
        element = element_map.get(element_index or -1)
        if element_index is None or element is None:
            skipped.append({"target_element_index": element_index, "operation": "move_line_endpoint", "reason": "target element not found"})
            continue
        capability = edit_capability_report(element, parent_map.get(element_index), transform_map.get(element_index))
        if capability["operations"]["endpoint_move"]["status"] != "supported":
            skipped.append(review_required_skip(candidate, element_index, "move_line_endpoint", capability, "endpoint_move"))
            continue
        result = move_line_endpoint(
            element,
            str(candidate.get("endpoint") or "end"),
            candidate,
            operation_id=str(candidate.get("operation_id") or f"endpoint_move_{len(applied) + 1:03d}"),
            transform_matrix=transform_map.get(element_index),
        )
        result["target_element_index"] = element_index
        result["program_cluster_id"] = candidate.get("program_cluster_id")
        result["program_role"] = candidate.get("program_role")
        result["endpoint_move_priority"] = candidate.get("endpoint_move_priority")
        if result.get("status") == "applied":
            applied.append(result)
            moved_indices.add(element_index)
        else:
            skipped.append(result)
    return {
        "endpoint_move_candidate_count": len(candidates),
        "same_layer_endpoint_move_count": len(applied),
        "endpoint_moved_element_indices": sorted(moved_indices),
        "endpoint_move_mutations": applied,
        "endpoint_move_skips": skipped,
        "locked_target_skips": locked_skips,
    }


def apply_opening_candidates(
    root: Element,
    plan: dict[str, Any],
    max_openings: int,
    locked_indices: set[int] | None = None,
    element_map: dict[int, Element] | None = None,
    parent_map: dict[int, Element | None] | None = None,
    transform_map: dict[int, Matrix] | None = None,
) -> dict[str, Any]:
    locked_indices = locked_indices or set()
    element_map = element_map or existing_elements_by_document_index(root)
    parent_map = parent_map or parents_by_document_index(root)
    transform_map = transform_map or element_matrices_by_document_index(root)
    candidates = sorted(plan.get("same_layer_opening_candidates", []), key=opening_sort_key, reverse=True)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    locked_skips: list[dict[str, Any]] = []
    opened_indices: set[int] = set()
    for candidate in candidates:
        if len(applied) >= max_openings:
            break
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            skip = locked_target_skip(candidate, element_index, "split_line_for_opening")
            skipped.append(skip)
            locked_skips.append(skip)
            continue
        element = element_map.get(element_index or -1)
        parent = parent_map.get(element_index or -1)
        if element_index is None or element is None or parent is None:
            skipped.append({"target_element_index": element_index, "reason": "target element or parent not found"})
            continue
        tag = local_tag(element)
        if tag == "path":
            action = "split_path_for_opening"
        elif tag == "polyline":
            action = "split_polyline_for_opening"
        else:
            action = "split_line_for_opening"
        capability = edit_capability_report(element, parent, transform_map.get(element_index))
        if capability["operations"]["opening_split"]["status"] != "supported":
            skipped.append(review_required_skip(candidate, element_index, action, capability, "opening_split"))
            continue
        if tag == "path":
            result = split_path_for_opening(
                parent,
                element,
                svg_float(candidate.get("opening_start_ratio", 0.42)),
                svg_float(candidate.get("opening_end_ratio", 0.58)),
                operation_id=str(candidate.get("operation_id") or f"opening_{len(applied) + 1:03d}"),
                transform_matrix=transform_map.get(element_index),
            )
        elif tag == "polyline":
            result = split_polyline_for_opening(
                parent,
                element,
                svg_float(candidate.get("opening_start_ratio", 0.42)),
                svg_float(candidate.get("opening_end_ratio", 0.58)),
                operation_id=str(candidate.get("operation_id") or f"opening_{len(applied) + 1:03d}"),
                transform_matrix=transform_map.get(element_index),
            )
        else:
            result = split_line_for_opening(
                parent,
                element,
                svg_float(candidate.get("opening_start_ratio", 0.42)),
                svg_float(candidate.get("opening_end_ratio", 0.58)),
                operation_id=str(candidate.get("operation_id") or f"opening_{len(applied) + 1:03d}"),
            )
        result["target_element_index"] = element_index
        result["program_cluster_id"] = candidate.get("program_cluster_id")
        result["program_role"] = candidate.get("program_role")
        result["connects_to_role"] = candidate.get("connects_to_role")
        result["connects_to_cluster_id"] = candidate.get("connects_to_cluster_id")
        result["adjacency_edge_id"] = candidate.get("adjacency_edge_id")
        result["adjacency_rationale"] = candidate.get("adjacency_rationale")
        result["topology_evidence"] = candidate.get("topology_evidence")
        result["opening_priority"] = candidate.get("opening_priority")
        if result.get("status") == "applied":
            applied.append(result)
            opened_indices.add(element_index)
        else:
            skipped.append(result)
    return {
        "opening_candidate_count": len(candidates),
        "same_layer_opening_split_count": len(applied),
        "same_layer_segment_added_count": sum(1 for item in applied if item.get("same_layer_segment_added")),
        "opened_element_indices": sorted(opened_indices),
        "opening_mutations": applied,
        "opening_skips": skipped,
        "locked_target_skips": locked_skips,
    }


def remove_mutable_partition_element(element: Element) -> dict[str, Any]:
    tag = local_tag(element)
    preserve_original_attrs(
        element,
        [
            "display",
            "visibility",
            "opacity",
            "stroke",
            "stroke-width",
            "stroke-opacity",
            "stroke-dasharray",
            "fill",
            "fill-opacity",
            "style",
            "points",
            "d",
        ],
    )
    geometry_mutated = collapse_linear_element_to_zero_length(element)
    element.set("display", "none")
    element.set("data-crab-action", "remove_internal_partition")
    element.set("data-crab-same-layer-removal", "true")
    element.set("data-crab-geometry-mutated", "true" if geometry_mutated else "visibility_removed")
    return {
        "action": element.attrib["data-crab-action"],
        "geometry_mutated": geometry_mutated,
        "same_layer_removed": True,
    }


def patch_existing_element(element: Element, candidate: dict[str, Any], mutation_index: int) -> dict[str, Any]:
    element.set("data-crab-same-layer-mutation", "same_layer_geometry_patch")
    element.set("data-crab-mutation-index", str(mutation_index))
    element.set("data-crab-source-element-index", str(candidate.get("element_index")))
    element.set("data-crab-addressing", str(candidate.get("addressing") or "source_svg_element_index"))
    element.set("data-crab-mutation-policy", str(candidate.get("mutation_policy") or "modify_or_remove_existing_element_only"))
    if candidate.get("program_cluster_id"):
        element.set("data-crab-program-cluster", str(candidate["program_cluster_id"]))
    if candidate.get("program_role"):
        element.set("data-crab-program-role", str(candidate["program_role"]))

    tag = local_tag(element)
    if tag in {"line", "polyline", "path"}:
        mutation = remove_mutable_partition_element(element)
    elif tag in {"rect", "polygon", "circle", "ellipse"}:
        preserve_original_attrs(element, ["stroke", "stroke-opacity", "fill-opacity", "style"])
        element.set("stroke", "#d04a02")
        element.set("stroke-opacity", "0.28")
        element.set("fill-opacity", "0.08")
        element.set("data-crab-action", "candidate_envelope_to_reconcile")
        element.set("data-crab-geometry-mutated", "false")
        mutation = {"action": element.attrib["data-crab-action"], "geometry_mutated": False, "same_layer_removed": False}
    else:
        element.set("data-crab-action", "candidate_attribute_patch")
        element.set("data-crab-geometry-mutated", "false")
        mutation = {"action": element.attrib["data-crab-action"], "geometry_mutated": False, "same_layer_removed": False}

    return {
        "element_index": candidate.get("element_index"),
        "tag": tag,
        "id": element.attrib.get("id"),
        "program_cluster_id": candidate.get("program_cluster_id"),
        "program_role": candidate.get("program_role"),
        "action": mutation["action"],
        "geometry_mutated": mutation["geometry_mutated"],
        "same_layer_removed": mutation["same_layer_removed"],
    }


def apply_same_layer_geometry_patch(
    root: Element,
    plan: dict[str, Any],
    max_mutations: int,
    apply_openings: bool = False,
    max_openings: int = 4,
    apply_endpoint_moves: bool = False,
    max_endpoint_moves: int = 4,
) -> dict[str, Any]:
    root.set("data-crab-candidate", "crab_archi_design_same_layer_engine_candidate")
    root.set("data-crab-engine", "same-layer-svg-engine")
    root.set("data-crab-mutation-strategy", "same_layer_geometry_patch")
    root.set("data-crab-overlay-elements-added", "0")

    element_map = existing_elements_by_document_index(root)
    parent_map = parents_by_document_index(root)
    transform_map = element_matrices_by_document_index(root)
    locked_indices = locked_candidate_indices(plan)
    locked_before = capture_locked_element_states(root, plan)
    capability_summary = edit_capability_summary(plan, element_map, parent_map, transform_map, locked_indices)
    capability_totals = edit_capability_totals(capability_summary)
    opening_summary = apply_opening_candidates(
        root,
        plan,
        max_openings,
        locked_indices=locked_indices,
        element_map=element_map,
        parent_map=parent_map,
        transform_map=transform_map,
    ) if apply_openings else {
        "opening_candidate_count": len(plan.get("same_layer_opening_candidates", [])),
        "same_layer_opening_split_count": 0,
        "same_layer_segment_added_count": 0,
        "opened_element_indices": [],
        "opening_mutations": [],
        "opening_skips": [],
        "locked_target_skips": [],
    }
    opened_indices = set(opening_summary["opened_element_indices"])
    endpoint_summary = apply_endpoint_move_candidates(
        root,
        plan,
        max_endpoint_moves,
        locked_indices=locked_indices,
        excluded_indices=opened_indices,
        element_map=element_map,
        parent_map=parent_map,
        transform_map=transform_map,
    ) if apply_endpoint_moves else {
        "endpoint_move_candidate_count": len(plan.get("same_layer_endpoint_move_candidates", [])),
        "same_layer_endpoint_move_count": 0,
        "endpoint_moved_element_indices": [],
        "endpoint_move_mutations": [],
        "endpoint_move_skips": [],
        "locked_target_skips": [],
    }
    endpoint_moved_indices = set(endpoint_summary["endpoint_moved_element_indices"])
    selected, mutable_locked_skips = select_candidates(plan, max_mutations, locked_indices=locked_indices)
    selected = [candidate for candidate in selected if selected_candidate_index(candidate) not in opened_indices and selected_candidate_index(candidate) not in endpoint_moved_indices]
    mutated: list[dict[str, Any]] = []
    partition_skips: list[dict[str, Any]] = []
    missing: list[int] = []
    for candidate in selected:
        element_index = selected_candidate_index(candidate) or 0
        element = element_map.get(element_index)
        if element is None:
            missing.append(element_index)
            continue
        capability = edit_capability_report(element, parent_map.get(element_index), transform_map.get(element_index))
        if capability["operations"]["partition_remove"]["status"] != "supported":
            partition_skips.append(review_required_skip(candidate, element_index, "remove_internal_partition", capability, "partition_remove"))
            continue
        mutated.append(patch_existing_element(element, candidate, len(mutated) + 1))

    locked_report = locked_preservation_report(locked_before)
    locked_target_skips = [*opening_summary["locked_target_skips"], *endpoint_summary["locked_target_skips"], *mutable_locked_skips]
    removal_geometry_mutation_count = sum(1 for item in mutated if item.get("geometry_mutated"))
    opening_program_cluster_count = sum(1 for item in opening_summary["opening_mutations"] if item.get("program_cluster_id"))
    endpoint_program_cluster_count = sum(1 for item in endpoint_summary["endpoint_move_mutations"] if item.get("program_cluster_id"))
    return {
        "mutation_strategy": "same_layer_geometry_patch",
        "patch_plan_status": plan.get("status"),
        "patch_plan_candidate_count": len(plan.get("same_layer_mutable_candidates", [])),
        "edit_capability_summary": capability_summary,
        "edit_capability_totals": capability_totals,
        "edit_capability_review_required_count": capability_totals["review_required_count"],
        "edit_capability_manual_review_count": capability_totals["manual_review_count"],
        "selected_candidate_count": len(selected),
        "same_layer_mutation_count": len(mutated),
        "same_layer_removal_count": sum(1 for item in mutated if item.get("same_layer_removed")),
        "same_layer_geometry_mutation_count": removal_geometry_mutation_count + opening_summary["same_layer_opening_split_count"] + endpoint_summary["same_layer_endpoint_move_count"],
        "same_layer_opening_split_count": opening_summary["same_layer_opening_split_count"],
        "same_layer_segment_added_count": opening_summary["same_layer_segment_added_count"],
        "same_layer_endpoint_move_count": endpoint_summary["same_layer_endpoint_move_count"],
        "program_cluster_mutation_count": sum(1 for item in mutated if item.get("program_cluster_id")) + opening_program_cluster_count + endpoint_program_cluster_count,
        "missing_element_indices": missing,
        "mutations": mutated,
        "partition_remove_skips": partition_skips,
        "opening_mutations": opening_summary["opening_mutations"],
        "opening_skips": opening_summary["opening_skips"],
        "endpoint_move_mutations": endpoint_summary["endpoint_move_mutations"],
        "endpoint_move_skips": endpoint_summary["endpoint_move_skips"],
        "locked_preservation": locked_report,
        "locked_target_skip_count": len(locked_target_skips),
        "locked_target_skips": locked_target_skips,
        "locked_targets_not_selected": not locked_target_skips,
    }
