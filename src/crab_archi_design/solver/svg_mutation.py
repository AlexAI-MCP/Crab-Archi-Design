from __future__ import annotations

from copy import deepcopy
from typing import Any
from xml.etree.ElementTree import Element


def local_tag(element: Element) -> str:
    return element.tag.split("}")[-1]


def count_images(root: Element) -> int:
    return sum(1 for element in root.iter() if local_tag(element) == "image")


def existing_elements_by_document_index(root: Element) -> dict[int, Element]:
    return {index: element for index, element in enumerate(root.iter(), start=1)}


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


def original_attr_name(name: str) -> str:
    return f"data-crab-original-{name}"


def preserve_original_attr(element: Element, name: str) -> None:
    original_name = original_attr_name(name)
    if original_name not in element.attrib and name in element.attrib:
        element.set(original_name, str(element.attrib[name]))


def preserve_original_attrs(element: Element, names: list[str]) -> None:
    for name in names:
        preserve_original_attr(element, name)


def svg_float(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def format_svg_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def line_points(element: Element) -> tuple[float, float, float, float] | None:
    if local_tag(element) != "line" or not {"x1", "y1", "x2", "y2"} <= set(element.attrib):
        return None
    return (
        svg_float(element.attrib["x1"]),
        svg_float(element.attrib["y1"]),
        svg_float(element.attrib["x2"]),
        svg_float(element.attrib["y2"]),
    )


def interpolate_line_point(coords: tuple[float, float, float, float], ratio: float) -> tuple[float, float]:
    x1, y1, x2, y2 = coords
    return (x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio)


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, int]:
    bbox = candidate.get("bbox") or {}
    area = float(bbox.get("width") or 0.0) * float(bbox.get("height") or 0.0)
    return (float(candidate.get("patch_priority") or 0.0), area, int(candidate.get("element_index") or 0))


def select_candidates(plan: dict[str, Any], max_mutations: int) -> list[dict[str, Any]]:
    candidates = [
        candidate
        for candidate in plan.get("same_layer_mutable_candidates", [])
        if candidate.get("mutation_policy") == "modify_or_remove_existing_element_only"
    ]
    candidates = sorted(candidates, key=candidate_sort_key, reverse=True)

    wall_candidates = [candidate for candidate in candidates if candidate.get("role_hint") == "wall_candidate"]
    clustered = [candidate for candidate in wall_candidates if candidate.get("program_cluster_id")]
    selected = clustered or wall_candidates or candidates
    return selected[:max_mutations]


def selected_candidate_index(candidate: dict[str, Any]) -> int | None:
    raw_index = candidate.get("element_index", candidate.get("target_element_index"))
    try:
        index = int(raw_index or 0)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def collapse_line_to_zero_length(element: Element) -> bool:
    if not {"x1", "y1", "x2", "y2"} <= set(element.attrib):
        return False
    preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
    element.set("x2", element.attrib["x1"])
    element.set("y2", element.attrib["y1"])
    return True


def split_line_for_opening(
    parent: Element,
    element: Element,
    opening_start_ratio: float,
    opening_end_ratio: float,
    operation_id: str = "door_opening",
) -> dict[str, Any]:
    coords = line_points(element)
    children = list(parent)
    if coords is None or element not in children or not (0.0 < opening_start_ratio < opening_end_ratio < 1.0):
        return {
            "action": "split_line_for_opening",
            "status": "skipped",
            "reason": "requires direct child line and 0 < start < end < 1",
            "geometry_mutated": False,
            "same_layer_segment_added": False,
        }

    preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
    start_x, start_y = interpolate_line_point(coords, opening_start_ratio)
    end_x, end_y = interpolate_line_point(coords, opening_end_ratio)

    after_segment = deepcopy(element)
    source_id = element.attrib.get("id")
    if source_id:
        after_segment.set("id", f"{source_id}__crab_{operation_id}_after")
        after_segment.set("data-crab-derived-from", source_id)

    element.set("x2", format_svg_number(start_x))
    element.set("y2", format_svg_number(start_y))
    element.set("data-crab-action", "split_line_for_opening")
    element.set("data-crab-same-layer-mutation", "same_layer_line_opening_split")
    element.set("data-crab-opening-operation", operation_id)
    element.set("data-crab-opening-segment", "before")
    element.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    element.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    element.set("data-crab-geometry-mutated", "true")

    after_segment.set("x1", format_svg_number(end_x))
    after_segment.set("y1", format_svg_number(end_y))
    after_segment.set("x2", format_svg_number(coords[2]))
    after_segment.set("y2", format_svg_number(coords[3]))
    after_segment.set("data-crab-action", "split_line_for_opening")
    after_segment.set("data-crab-same-layer-mutation", "same_layer_line_opening_split")
    after_segment.set("data-crab-opening-operation", operation_id)
    after_segment.set("data-crab-opening-segment", "after")
    after_segment.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    after_segment.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    after_segment.set("data-crab-generated-same-layer-segment", "true")
    after_segment.set("data-crab-geometry-mutated", "true")

    parent.insert(children.index(element) + 1, after_segment)
    return {
        "action": "split_line_for_opening",
        "status": "applied",
        "operation_id": operation_id,
        "source_id": source_id,
        "before_segment": {"x1": coords[0], "y1": coords[1], "x2": start_x, "y2": start_y},
        "opening": {"x1": start_x, "y1": start_y, "x2": end_x, "y2": end_y},
        "after_segment": {"x1": end_x, "y1": end_y, "x2": coords[2], "y2": coords[3]},
        "geometry_mutated": True,
        "same_layer_segment_added": True,
    }


def opening_sort_key(candidate: dict[str, Any]) -> tuple[float, int]:
    return (float(candidate.get("opening_priority", candidate.get("patch_priority") or 0.0)), int(candidate.get("target_element_index") or 0))


def apply_opening_candidates(root: Element, plan: dict[str, Any], max_openings: int) -> dict[str, Any]:
    element_map = existing_elements_by_document_index(root)
    parent_map = parents_by_document_index(root)
    candidates = sorted(plan.get("same_layer_opening_candidates", []), key=opening_sort_key, reverse=True)[:max_openings]
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    opened_indices: set[int] = set()
    for candidate in candidates:
        element_index = selected_candidate_index(candidate)
        element = element_map.get(element_index or -1)
        parent = parent_map.get(element_index or -1)
        if element_index is None or element is None or parent is None:
            skipped.append({"target_element_index": element_index, "reason": "target element or parent not found"})
            continue
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
    geometry_mutated = collapse_line_to_zero_length(element) if tag == "line" else False
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


def apply_same_layer_geometry_patch(root: Element, plan: dict[str, Any], max_mutations: int, apply_openings: bool = False, max_openings: int = 4) -> dict[str, Any]:
    root.set("data-crab-candidate", "crab_archi_design_same_layer_engine_candidate")
    root.set("data-crab-engine", "same-layer-svg-engine")
    root.set("data-crab-mutation-strategy", "same_layer_geometry_patch")
    root.set("data-crab-overlay-elements-added", "0")

    element_map = existing_elements_by_document_index(root)
    opening_summary = apply_opening_candidates(root, plan, max_openings) if apply_openings else {
        "opening_candidate_count": len(plan.get("same_layer_opening_candidates", [])),
        "same_layer_opening_split_count": 0,
        "same_layer_segment_added_count": 0,
        "opened_element_indices": [],
        "opening_mutations": [],
        "opening_skips": [],
    }
    opened_indices = set(opening_summary["opened_element_indices"])
    selected = [candidate for candidate in select_candidates(plan, max_mutations) if selected_candidate_index(candidate) not in opened_indices]
    mutated: list[dict[str, Any]] = []
    missing: list[int] = []
    for candidate in selected:
        element_index = selected_candidate_index(candidate) or 0
        element = element_map.get(element_index)
        if element is None:
            missing.append(element_index)
            continue
        mutated.append(patch_existing_element(element, candidate, len(mutated) + 1))

    removal_geometry_mutation_count = sum(1 for item in mutated if item.get("geometry_mutated"))
    opening_program_cluster_count = sum(1 for item in opening_summary["opening_mutations"] if item.get("program_cluster_id"))
    return {
        "mutation_strategy": "same_layer_geometry_patch",
        "patch_plan_status": plan.get("status"),
        "patch_plan_candidate_count": len(plan.get("same_layer_mutable_candidates", [])),
        "selected_candidate_count": len(selected),
        "same_layer_mutation_count": len(mutated),
        "same_layer_removal_count": sum(1 for item in mutated if item.get("same_layer_removed")),
        "same_layer_geometry_mutation_count": removal_geometry_mutation_count + opening_summary["same_layer_opening_split_count"],
        "same_layer_opening_split_count": opening_summary["same_layer_opening_split_count"],
        "same_layer_segment_added_count": opening_summary["same_layer_segment_added_count"],
        "program_cluster_mutation_count": sum(1 for item in mutated if item.get("program_cluster_id")) + opening_program_cluster_count,
        "missing_element_indices": missing,
        "mutations": mutated,
        "opening_mutations": opening_summary["opening_mutations"],
        "opening_skips": opening_summary["opening_skips"],
    }
