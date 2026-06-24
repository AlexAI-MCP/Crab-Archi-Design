"""Deterministic solver contracts for same-layer SVG mutation."""

from crab_archi_design.solver.contract import SOLVER_INPUT_SCHEMA, SOLVER_OUTPUT_SCHEMA
from crab_archi_design.solver.feasible import build_feasible_report
from crab_archi_design.solver.objective import evaluate_topology_fit
from crab_archi_design.solver.patch_plan import build_endpoint_move_candidates, build_opening_candidates, patch_role_priority
from crab_archi_design.solver.scale import infer_architectural_scale
from crab_archi_design.solver.sizing import extract_program_targets, standard_role_rows_from_manifest
from crab_archi_design.solver.svg_mutation import apply_same_layer_geometry_patch

__all__ = [
    "SOLVER_INPUT_SCHEMA",
    "SOLVER_OUTPUT_SCHEMA",
    "apply_same_layer_geometry_patch",
    "build_feasible_report",
    "build_endpoint_move_candidates",
    "build_opening_candidates",
    "evaluate_topology_fit",
    "extract_program_targets",
    "infer_architectural_scale",
    "patch_role_priority",
    "standard_role_rows_from_manifest",
]
