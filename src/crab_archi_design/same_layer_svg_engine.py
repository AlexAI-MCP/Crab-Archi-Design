from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crab_archi_design.solver.svg_mutation import apply_same_layer_geometry_patch, count_images


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def latest_json(path: Path, pattern: str) -> Path | None:
    candidates = sorted(path.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name))
    return candidates[-1] if candidates else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-preview", action="store_true")
    parser.add_argument("--max-mutations", type=int, default=24)
    parser.add_argument("--apply-openings", action="store_true")
    parser.add_argument("--max-openings", type=int, default=4)
    parser.add_argument("--apply-endpoint-moves", action="store_true")
    parser.add_argument("--max-endpoint-moves", type=int, default=4)
    parser.add_argument("--apply-program-relabels", action="store_true")
    parser.add_argument("--max-program-relabels", type=int, default=16)
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
    summary = apply_same_layer_geometry_patch(
        root,
        patch_plan,
        max(1, args.max_mutations),
        apply_openings=args.apply_openings,
        max_openings=max(1, args.max_openings),
        apply_endpoint_moves=args.apply_endpoint_moves,
        max_endpoint_moves=max(1, args.max_endpoint_moves),
        apply_program_relabels=args.apply_program_relabels,
        max_program_relabels=max(1, args.max_program_relabels),
        intents=solver_input.get("intents", []),
    )
    output_svg = run_dir / "same_layer_engine_candidate.svg"
    tree.write(output_svg, encoding="utf-8", xml_declaration=True)

    output_root = ET.parse(output_svg).getroot()
    output_image_count = count_images(output_root)
    output_element_count = sum(1 for _ in output_root.iter())
    capability_summary = summary.get("edit_capability_summary", {})
    capability_totals = summary.get("edit_capability_totals", {})
    intent_coverage = summary.get("intent_role_coverage") or {}
    gates = {
        "native_svg_only": output_image_count == 0,
        "no_raster_overlay_added": output_image_count == source_image_count,
        "same_layer_patch_plan_available": True,
        "patch_plan_pass": patch_plan.get("status") == "pass",
        "edit_capability_summary_present": bool(capability_summary),
        "edit_capability_review_counts_reported": (
            isinstance(capability_totals, dict)
            and "review_required_count" in capability_totals
            and summary.get("edit_capability_review_required_count") == capability_totals.get("review_required_count")
        ),
        "source_element_addresses_used": summary["selected_candidate_count"] > 0 or summary["same_layer_opening_split_count"] > 0 or summary["same_layer_endpoint_move_count"] > 0 or summary["program_relabel_count"] > 0,
        "existing_elements_mutated": summary["same_layer_mutation_count"] > 0 or summary["same_layer_opening_split_count"] > 0 or summary["same_layer_endpoint_move_count"] > 0 or summary["program_relabel_count"] > 0,
        "existing_geometry_mutated": summary["same_layer_geometry_mutation_count"] > 0,
        "program_relabels_applied_or_not_requested": (not args.apply_program_relabels) or summary["program_relabel_count"] > 0,
        "same_layer_internal_partitions_removed": summary["same_layer_removal_count"] > 0 or summary["same_layer_opening_split_count"] > 0 or summary["same_layer_endpoint_move_count"] > 0,
        "same_layer_openings_applied_or_not_requested": (not args.apply_openings) or summary["same_layer_opening_split_count"] > 0,
        "same_layer_endpoint_moves_applied_or_not_requested": (not args.apply_endpoint_moves) or summary["same_layer_endpoint_move_count"] > 0,
        "intent_target_role_coverage_reported": bool(intent_coverage),
        "intent_repair_recommendations_reported": "intent_repair_recommendations" in summary,
        "program_cluster_targets_used": summary["program_cluster_mutation_count"] > 0,
        "locked_geometry_unchanged": summary["locked_preservation"]["locked_geometry_unchanged"],
        "locked_targets_not_selected": summary["locked_targets_not_selected"],
        "new_overlay_elements_added": output_element_count == source_element_count + summary["same_layer_segment_added_count"],
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
