from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def local_tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def count_images(root: ET.Element) -> int:
    return sum(1 for element in root.iter() if local_tag(element) == "image")


def latest_json(path: Path, pattern: str) -> Path | None:
    candidates = sorted(path.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name))
    return candidates[-1] if candidates else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def existing_elements_by_document_index(root: ET.Element) -> dict[int, ET.Element]:
    return {index: element for index, element in enumerate(root.iter(), start=1)}


def original_attr_name(name: str) -> str:
    return f"data-crab-original-{name}"


def preserve_original_attr(element: ET.Element, name: str) -> None:
    original_name = original_attr_name(name)
    if original_name not in element.attrib and name in element.attrib:
        element.set(original_name, str(element.attrib[name]))


def preserve_original_attrs(element: ET.Element, names: list[str]) -> None:
    for name in names:
        preserve_original_attr(element, name)


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


def collapse_line_to_zero_length(element: ET.Element) -> bool:
    if not {"x1", "y1", "x2", "y2"} <= set(element.attrib):
        return False
    preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
    element.set("x2", element.attrib["x1"])
    element.set("y2", element.attrib["y1"])
    return True


def remove_mutable_partition_element(element: ET.Element) -> dict[str, Any]:
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


def patch_existing_element(element: ET.Element, candidate: dict[str, Any], mutation_index: int) -> dict[str, Any]:
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


def apply_same_layer_patch(root: ET.Element, plan: dict[str, Any], max_mutations: int) -> dict[str, Any]:
    root.set("data-crab-candidate", "crab_archi_design_same_layer_engine_candidate")
    root.set("data-crab-engine", "same-layer-svg-engine")
    root.set("data-crab-mutation-strategy", "same_layer_geometry_patch")
    root.set("data-crab-overlay-elements-added", "0")

    element_map = existing_elements_by_document_index(root)
    selected = select_candidates(plan, max_mutations)
    mutated: list[dict[str, Any]] = []
    missing: list[int] = []
    for candidate in selected:
        try:
            element_index = int(candidate.get("element_index") or 0)
        except (TypeError, ValueError):
            element_index = 0
        element = element_map.get(element_index)
        if element is None:
            missing.append(element_index)
            continue
        mutated.append(patch_existing_element(element, candidate, len(mutated) + 1))

    return {
        "mutation_strategy": "same_layer_geometry_patch",
        "patch_plan_status": plan.get("status"),
        "patch_plan_candidate_count": len(plan.get("same_layer_mutable_candidates", [])),
        "selected_candidate_count": len(selected),
        "same_layer_mutation_count": len(mutated),
        "same_layer_removal_count": sum(1 for item in mutated if item.get("same_layer_removed")),
        "same_layer_geometry_mutation_count": sum(1 for item in mutated if item.get("geometry_mutated")),
        "program_cluster_mutation_count": sum(1 for item in mutated if item.get("program_cluster_id")),
        "missing_element_indices": missing,
        "mutations": mutated,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-preview", action="store_true")
    parser.add_argument("--max-mutations", type=int, default=24)
    args, _ = parser.parse_known_args()

    solver_input_path = Path(os.environ["CRAB_ARCHI_SOLVER_INPUT"])
    run_dir = Path(os.environ["CRAB_ARCHI_RUN_DIR"])
    project_dir = Path(os.environ["CRAB_ARCHI_PROJECT_DIR"])
    solver_input = read_json(solver_input_path)
    source_svg = Path(os.environ.get("CRAB_ARCHI_SOURCE_SVG") or solver_input["manifest"]["source_svg"]).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)

    patch_plan_path = latest_json(project_dir / "patch_plans", "svg_patch_plan_*.json")
    if patch_plan_path is None:
        report = {
            "schema": "crab-archi-design-same-layer-engine-report-v1",
            "created_at": now(),
            "status": "review_required",
            "engine": "same-layer-svg-engine",
            "source_svg": str(source_svg),
            "solver_input": str(solver_input_path),
            "output_svg": None,
            "summary": {"reason": "missing_svg_patch_plan"},
            "quality": {"gates": {"same_layer_patch_plan_available": False}},
        }
        report_path = run_dir / "same_layer_engine_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report_path)
        return

    patch_plan = read_json(patch_plan_path)
    tree = ET.parse(source_svg)
    root = tree.getroot()
    source_image_count = count_images(root)
    source_element_count = sum(1 for _ in root.iter())
    summary = apply_same_layer_patch(root, patch_plan, max(1, args.max_mutations))
    output_svg = run_dir / "same_layer_engine_candidate.svg"
    tree.write(output_svg, encoding="utf-8", xml_declaration=True)

    output_root = ET.parse(output_svg).getroot()
    output_image_count = count_images(output_root)
    output_element_count = sum(1 for _ in output_root.iter())
    gates = {
        "native_svg_only": output_image_count == 0,
        "no_raster_overlay_added": output_image_count == source_image_count,
        "same_layer_patch_plan_available": True,
        "patch_plan_pass": patch_plan.get("status") == "pass",
        "source_element_addresses_used": summary["selected_candidate_count"] > 0,
        "existing_elements_mutated": summary["same_layer_mutation_count"] > 0,
        "existing_geometry_mutated": summary["same_layer_geometry_mutation_count"] > 0,
        "same_layer_internal_partitions_removed": summary["same_layer_removal_count"] > 0,
        "program_cluster_targets_used": summary["program_cluster_mutation_count"] > 0,
        "new_overlay_elements_added": output_element_count == source_element_count,
        "mutation_strategy_same_layer": summary["mutation_strategy"] == "same_layer_geometry_patch",
    }
    report = {
        "schema": "crab-archi-design-same-layer-engine-report-v1",
        "created_at": now(),
        "status": "pass" if all(gates.values()) else "review_required",
        "engine": "same-layer-svg-engine",
        "reference_only": False,
        "source_svg": str(source_svg),
        "solver_input": str(solver_input_path),
        "patch_plan": str(patch_plan_path),
        "output_svg": str(output_svg),
        "summary": summary,
        "quality": {"gates": gates},
    }
    report_path = run_dir / "same_layer_engine_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    print(output_svg)


if __name__ == "__main__":
    main()
