from __future__ import annotations

from typing import Any


ENGINE_WORKFLOW_CONTRACT_SCHEMA = "crab-archi-design-engine-workflow-contract-v1"
WORKFLOW_STATE_AUDIT_SCHEMA = "crab-archi-design-workflow-state-audit-v1"

DEFAULT_OPENCRAB_HOMEPAGE = "https://opencrab.sh"
DEFAULT_PRODUCTION_ENGINE = "same-layer-svg-engine"

STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "recognition_ir",
        "title": "Recognition IR",
        "engine_boundary": "recognition_engine",
        "module_targets": ["svg/*", "recognition/*"],
        "purpose": "Parse the original SVG into deterministic world-coordinate architectural primitives.",
        "required_artifacts": ["source_svg"],
        "output_artifacts": ["recognition_ir_v2"],
        "hard_gates": ["safe_svg_parse", "world_coordinate_ir", "source_document_indexes_present"],
        "soft_gates": ["text_labels_positioned", "columns_detected", "wall_candidates_detected"],
        "llm_policy": "LLM may inspect summaries only; it must not redraw SVG geometry.",
    },
    {
        "id": "topology_constraints",
        "title": "Topology And Constraints",
        "engine_boundary": "topology_engine",
        "module_targets": ["recognition/*", "solver/feasible.py"],
        "purpose": "Convert recognized primitives and user doodle boundaries into shell, mutable, lock, no-go, and adjacency graph evidence.",
        "required_artifacts": ["recognition_ir_v2", "constraint_manifest"],
        "output_artifacts": ["topology_manifest", "recognition_audit"],
        "hard_gates": ["community_shell_confirmed", "mutable_zone_confirmed", "protected_zone_confirmed", "topology_manifest_active"],
        "soft_gates": ["space_regions_detected", "program_clusters_detected"],
        "llm_policy": "LLM may request constraint review; it cannot override locked parking, core, ramp, column, or egress geometry.",
    },
    {
        "id": "opencrab_projection",
        "title": "OpenCrab Ontology Projection",
        "engine_boundary": "opencrab_engine",
        "module_targets": ["opencrab-request", "opencrab-sync", "topology-build"],
        "purpose": "Read ontology packs, superior-case topology, evidence, claims, and standards priors through OpenCrab MCP before design mutation.",
        "required_artifacts": ["topology_manifest", "standards_manifest", "ontology_pack"],
        "output_artifacts": ["opencrab_sync", "evidence_manifest"],
        "hard_gates": ["opencrab_evidence_verified", "ontology_pack_present", "evidence_backed_topology_prior"],
        "soft_gates": ["precedent_adjacency_targets_present", "program_standard_rows_matched"],
        "llm_policy": "LLM may summarize OpenCrab evidence into DesignIntent/EditIntent JSON with evidence references.",
    },
    {
        "id": "intent_compilation",
        "title": "Intent Compilation",
        "engine_boundary": "intent_engine",
        "module_targets": ["intents/*", "prompt-edit", "sketch-intent", "edit-brief"],
        "purpose": "Turn natural language, reference goals, standards, and doodle strokes into bounded DesignIntent/EditIntent JSON.",
        "required_artifacts": ["recognition_ir_v2", "topology_manifest", "evidence_manifest", "standards_manifest"],
        "output_artifacts": ["edit_intents", "edit_brief", "design_handoff"],
        "hard_gates": ["intent_schema_valid", "intent_count_positive", "intent_targets_mapped_to_roles"],
        "soft_gates": ["doodle_points_inside_viewbox", "intent_evidence_references_present"],
        "llm_policy": "LLM output is declarative intent only; final coordinates must be generated or verified by deterministic engines.",
    },
    {
        "id": "patch_planning",
        "title": "Same-Layer Patch Planning",
        "engine_boundary": "solver_engine",
        "module_targets": ["solver/sizing.py", "solver/placement.py", "solver/localsearch.py", "solver/patch_plan.py"],
        "purpose": "Project ontology-backed goals onto recognized mutable source SVG elements and rank same-layer edit candidates.",
        "required_artifacts": ["recognition_ir_v2", "topology_manifest", "constraint_manifest", "standards_manifest", "evidence_manifest", "edit_intents"],
        "output_artifacts": ["svg_patch_plan"],
        "hard_gates": ["same_layer_mutable_candidates_found", "locked_candidates_marked", "overlay_generation_disallowed"],
        "soft_gates": ["local_search_reported", "intent_target_role_coverage_reported", "intent_repair_recommendations_reported"],
        "llm_policy": "LLM may choose priorities; the solver selects source element addresses and rejects hard-constraint violations.",
    },
    {
        "id": "native_svg_mutation",
        "title": "Native SVG Mutation",
        "engine_boundary": "svg_mutation_engine",
        "module_targets": ["same_layer_svg_engine.py", "solver/svg_mutation.py", "solver/svg_edit_ops.py"],
        "purpose": "Mutate existing SVG lines, paths, polylines, and endpoints in place instead of adding zoning overlays or raster images.",
        "required_artifacts": ["source_svg", "svg_patch_plan", "edit_intents"],
        "output_artifacts": ["alternative_svg", "engine_report"],
        "hard_gates": [
            "mutation_strategy_same_layer",
            "source_element_addresses_used",
            "existing_geometry_mutated",
            "no_raster_overlay_added",
            "locked_geometry_unchanged",
            "locked_targets_not_selected",
        ],
        "soft_gates": ["same_layer_openings_applied_or_not_requested", "same_layer_endpoint_moves_applied_or_not_requested", "edit_capability_summary_present"],
        "llm_policy": "LLM cannot directly add a replacement drawing layer; unsupported primitives must be reported for review.",
    },
    {
        "id": "qa_release",
        "title": "QA And Release",
        "engine_boundary": "qa_engine",
        "module_targets": ["qa/*", "doctor", "release-audit", "export-package"],
        "purpose": "Prove the candidate is native SVG, evidence-backed, constraint-safe, packaged, and reviewable.",
        "required_artifacts": ["alternative_svg", "engine_report", "apply_edit_report", "candidate_quality_report"],
        "output_artifacts": ["review_panel", "export_package", "release_audit"],
        "hard_gates": ["candidate_hard_gates_pass", "native_svg_no_images", "package_verify_pass", "doctor_pass"],
        "soft_gates": ["candidate_soft_gates_reported", "review_panel_available"],
        "llm_policy": "LLM may explain results and repair recommendations, but release status is determined by QA gates.",
    },
)


def workflow_stage_ids() -> list[str]:
    return [stage["id"] for stage in STAGE_DEFINITIONS]


def build_engine_workflow_contract(
    opencrab_homepage: str = DEFAULT_OPENCRAB_HOMEPAGE,
    production_engine: str = DEFAULT_PRODUCTION_ENGINE,
) -> dict[str, Any]:
    return {
        "schema": ENGINE_WORKFLOW_CONTRACT_SCHEMA,
        "version": "1.0.0",
        "name": "Crab Archi Design engine workflow contract",
        "production_engine": production_engine,
        "opencrab": {
            "required": True,
            "homepage": opencrab_homepage,
            "evidence_gate": "opencrab_evidence_verified",
            "decision_rule": "final SVG candidates require OpenCrab ontology evidence before native SVG mutation can pass production QA",
        },
        "cad_like_svg_policy": {
            "allowed": [
                "mutate original SVG elements by source document index",
                "split existing line/polyline/path elements for openings",
                "move supported endpoints in source coordinates through transform-aware conversion",
                "preserve locked/protected geometry byte-for-byte",
            ],
            "disallowed": [
                "raster overlays",
                "new zoning-image layers as final output",
                "freehand LLM coordinate redraws without solver verification",
                "parking/core/ramp/column/egress intrusion",
            ],
        },
        "llm_boundary": {
            "codex_role": "architectural reasoning, evidence synthesis, intent authoring, repair recommendation review",
            "allowed_outputs": ["DesignIntent JSON", "EditIntent JSON", "human-readable design rationale", "repair instructions"],
            "disallowed_outputs": ["unverified final SVG coordinates", "overlay-only final candidates", "hard constraint overrides"],
        },
        "stages": [dict(stage) for stage in STAGE_DEFINITIONS],
        "stage_order": workflow_stage_ids(),
    }


def _truthy_map(values: dict[str, Any] | None) -> dict[str, bool]:
    return {str(key): bool(value) for key, value in (values or {}).items()}


def audit_workflow_state(state: dict[str, Any], contract: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = contract or build_engine_workflow_contract()
    artifacts = _truthy_map(state.get("artifacts"))
    gates = _truthy_map(state.get("gates"))
    stage_results: list[dict[str, Any]] = []
    blocked_at_stage: str | None = None
    for stage in contract["stages"]:
        missing_artifacts = [name for name in stage["required_artifacts"] if not artifacts.get(name)]
        failing_gates = [name for name in stage["hard_gates"] if not gates.get(name)]
        ready = not missing_artifacts and not failing_gates
        if not ready and blocked_at_stage is None:
            blocked_at_stage = stage["id"]
        stage_results.append(
            {
                "id": stage["id"],
                "title": stage["title"],
                "ready": ready,
                "missing_artifacts": missing_artifacts,
                "failing_hard_gates": failing_gates,
                "soft_gates_reported": [name for name in stage["soft_gates"] if name in gates],
                "next_action": next_stage_action(stage, missing_artifacts, failing_gates),
            }
        )
    return {
        "schema": WORKFLOW_STATE_AUDIT_SCHEMA,
        "status": "pass" if blocked_at_stage is None else "review_required",
        "blocked_at_stage": blocked_at_stage,
        "stage_count": len(stage_results),
        "ready_stage_count": sum(1 for item in stage_results if item["ready"]),
        "stage_results": stage_results,
    }


def next_stage_action(stage: dict[str, Any], missing_artifacts: list[str], failing_gates: list[str]) -> str:
    if missing_artifacts:
        return f"create or attach required artifact(s): {', '.join(missing_artifacts)}"
    if failing_gates:
        return f"repair hard gate(s): {', '.join(failing_gates)}"
    return f"stage ready: produce {', '.join(stage['output_artifacts'])}"
