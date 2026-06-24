from __future__ import annotations

from typing import Any


HARD_APPLY_GATES = {
    "engine_returncode_zero",
    "engine_report_quality_found",
    "engine_report_status_pass",
    "engine_quality_gates_pass",
    "candidate_svg_exists",
    "native_svg_no_images",
    "recognition_manifest_active",
    "topology_manifest_active",
    "opencrab_evidence_verified",
    "constraint_manifest_active",
    "standards_manifest_active",
    "intent_count_positive",
}

HARD_ENGINE_GATES = {
    "native_svg_only",
    "no_raster_overlay_added",
    "same_layer_patch_plan_available",
    "patch_plan_pass",
    "source_element_addresses_used",
    "existing_elements_mutated",
    "existing_geometry_mutated",
    "same_layer_internal_partitions_removed",
    "locked_geometry_unchanged",
    "locked_targets_not_selected",
    "new_overlay_elements_added",
    "mutation_strategy_same_layer",
    "community_shell_found",
    "mutable_zone_or_shell_used",
    "rooms_inside_community_shell",
    "no_go_intrusion_free",
    "redesign_cleanup_mask_applied",
    "recognized_columns_preserved",
}

SOFT_ENGINE_GATES = {
    "layout_coverage_sufficient",
    "room_aspect_efficiency",
    "door_openings_planned",
    "corridor_axis_planned",
    "standard_programs_planned",
    "large_programs_present",
    "large_program_hierarchy",
    "program_cluster_targets_used",
    "same_layer_openings_applied_or_not_requested",
    "same_layer_endpoint_moves_applied_or_not_requested",
    "edit_capability_summary_present",
    "edit_capability_review_counts_reported",
    "intent_target_role_coverage_reported",
    "layout_layer_added",
    "plan_detail_layer_added",
    "standards_available",
    "room_count_positive",
    "reference_layer_added",
    "source_mutation_uses_only_additive_reference_layer",
    "opencrab_evidence_available",
    "constraints_available",
}


def gate_status(gates: dict[str, bool]) -> str:
    return "pass" if gates and all(gates.values()) else "review_required"


def flatten_engine_gate_groups(engine_report_quality: dict[str, Any] | None) -> dict[str, bool]:
    flattened: dict[str, bool] = {}
    for group_name, gates in (engine_report_quality or {}).get("gates", {}).items():
        if not isinstance(gates, dict):
            continue
        for gate, value in gates.items():
            flattened[f"{group_name}.{gate}"] = bool(value)
    return flattened


def select_named_gates(gates: dict[str, bool], names: set[str]) -> dict[str, bool]:
    selected: dict[str, bool] = {}
    for key, value in gates.items():
        name = key.rsplit(".", 1)[-1]
        if name in names:
            selected[key] = bool(value)
    return selected


def build_candidate_quality_report(
    apply_checks: dict[str, Any] | None,
    engine_report_quality: dict[str, Any] | None,
) -> dict[str, Any]:
    apply_gates = {key: bool(value) for key, value in (apply_checks or {}).items() if key in HARD_APPLY_GATES}
    flattened_engine_gates = flatten_engine_gate_groups(engine_report_quality)
    hard_engine_gates = select_named_gates(flattened_engine_gates, HARD_ENGINE_GATES)
    soft_engine_gates = select_named_gates(flattened_engine_gates, SOFT_ENGINE_GATES)
    hard_gates = {**apply_gates, **hard_engine_gates}
    hard_failures = [key for key, value in hard_gates.items() if not value]
    soft_failures = [key for key, value in soft_engine_gates.items() if not value]
    return {
        "schema": "crab-archi-design-candidate-quality-v1",
        "status": "pass" if hard_gates and not hard_failures else "review_required",
        "hard_status": "pass" if hard_gates and not hard_failures else "review_required",
        "soft_status": "pass" if soft_engine_gates and not soft_failures else "review_required",
        "hard_gate_count": len(hard_gates),
        "soft_gate_count": len(soft_engine_gates),
        "hard_failure_count": len(hard_failures),
        "soft_failure_count": len(soft_failures),
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "gate_groups": {
            "apply_hard": apply_gates,
            "engine_hard": hard_engine_gates,
            "engine_soft": soft_engine_gates,
        },
        "classification": {
            "hard": sorted(HARD_APPLY_GATES | HARD_ENGINE_GATES),
            "soft": sorted(SOFT_ENGINE_GATES),
        },
    }
