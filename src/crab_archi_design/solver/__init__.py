"""Deterministic solver contracts for same-layer SVG mutation."""

from crab_archi_design.solver.contract import SOLVER_INPUT_SCHEMA, SOLVER_OUTPUT_SCHEMA
from crab_archi_design.solver.svg_mutation import apply_same_layer_geometry_patch

__all__ = ["SOLVER_INPUT_SCHEMA", "SOLVER_OUTPUT_SCHEMA", "apply_same_layer_geometry_patch"]
