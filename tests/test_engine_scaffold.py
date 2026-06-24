from crab_archi_design.intents import DESIGN_INTENT_SCHEMA, EDIT_INTENT_SCHEMA, validate_intent_schema
from crab_archi_design.qa import gate_status
from crab_archi_design.recognition import PARSER_VERSION, RECOGNITION_IR_SCHEMA, stable_node_id
from crab_archi_design.recognition.ir import empty_recognition_ir
from crab_archi_design.solver import SOLVER_INPUT_SCHEMA, SOLVER_OUTPUT_SCHEMA
from crab_archi_design.svg import BBox, apply_matrix, bbox_center, identity_matrix, multiply_matrix, point_in_polygon
from crab_archi_design.svg.geometry import polygon_area, quantize_point
from crab_archi_design.svg.transform import parse_transform


def test_engine_module_scaffold_exports_stable_contracts() -> None:
    assert RECOGNITION_IR_SCHEMA == "crab-archi-design-recognition-ir-v2"
    assert PARSER_VERSION == "2.0.0"
    assert stable_node_id(7) == "n0007"
    assert SOLVER_INPUT_SCHEMA == "crab-archi-design-solver-input-v1"
    assert SOLVER_OUTPUT_SCHEMA == "crab-archi-design-solver-output-v1"

    ir = empty_recognition_ir("/tmp/source.svg")
    assert ir["schema"] == RECOGNITION_IR_SCHEMA
    assert ir["document"]["coordinate_space"] == "world"
    assert ir["nodes"] == []


def test_svg_geometry_and_transform_helpers_are_deterministic() -> None:
    box = BBox(10.0, 20.0, 30.0, 40.0)
    assert box.area == 1200.0
    assert bbox_center(box) == (25.0, 40.0)
    assert polygon_area([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]) == 100.0
    assert point_in_polygon((5.0, 5.0), [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]) is True
    assert quantize_point((1.23456, 9.87654), 3) == (1.235, 9.877)

    translate = parse_transform("translate(10,20)")
    scale = parse_transform("scale(2)")
    matrix = multiply_matrix(translate, scale)
    assert apply_matrix(identity_matrix(), (3.0, 4.0)) == (3.0, 4.0)
    assert apply_matrix(matrix, (3.0, 4.0)) == (16.0, 28.0)


def test_intent_and_gate_contracts() -> None:
    assert validate_intent_schema({"schema": DESIGN_INTENT_SCHEMA, "goals": []}) == []
    assert validate_intent_schema({"schema": EDIT_INTENT_SCHEMA, "ops": []}) == []
    assert validate_intent_schema({"schema": "unknown"})
    assert gate_status({"a": True, "b": True}) == "pass"
    assert gate_status({"a": True, "b": False}) == "review_required"
