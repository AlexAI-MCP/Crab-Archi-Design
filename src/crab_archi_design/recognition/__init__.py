"""Recognition IR contracts and role-classification entry points."""

from crab_archi_design.recognition.ir import PARSER_VERSION, RECOGNITION_IR_SCHEMA, stable_node_id
from crab_archi_design.recognition.parser import build_recognition_ir_v2

__all__ = ["PARSER_VERSION", "RECOGNITION_IR_SCHEMA", "build_recognition_ir_v2", "stable_node_id"]
