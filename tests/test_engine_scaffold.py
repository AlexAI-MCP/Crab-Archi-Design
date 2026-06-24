from crab_archi_design.intents import DESIGN_INTENT_SCHEMA, EDIT_INTENT_SCHEMA, validate_intent_schema
from crab_archi_design.qa import gate_status
from crab_archi_design.recognition import PARSER_VERSION, RECOGNITION_IR_SCHEMA, build_recognition_ir_v2, stable_node_id
from crab_archi_design.recognition.ir import empty_recognition_ir
from crab_archi_design.solver import SOLVER_INPUT_SCHEMA, SOLVER_OUTPUT_SCHEMA, build_endpoint_move_candidates, build_opening_candidates
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


def test_solver_patch_plan_uses_topology_adjacency_for_openings() -> None:
    candidates = [
        {
            "element_index": 11,
            "tag": "line",
            "bbox": {"x": 10, "y": 10, "width": 100, "height": 2},
            "center": [60, 11],
            "patch_priority": 700.0,
            "program_cluster_id": "cluster_lounge",
            "program_role": "greenery_lounge",
            "space_region_ids": ["space_lounge"],
        }
    ]
    topology = {
        "nodes": [
            {"id": "cluster_lounge", "type": "program_cluster", "role": "greenery_lounge", "bbox": {"x": 0, "y": 0, "width": 120, "height": 80}},
            {"id": "cluster_hall", "type": "program_cluster", "role": "hall_lobby", "bbox": {"x": 110, "y": 0, "width": 80, "height": 80}},
        ],
        "edges": [
            {
                "id": "edge_topology_001",
                "type": "ontology_cluster_adjacency_target",
                "source": "cluster_lounge",
                "target": "cluster_hall",
                "left_role": "greenery_lounge",
                "right_role": "hall_lobby",
                "rationale": "main hall anchors greenery lounge",
                "evidence": "OpenCrab topology prior",
            }
        ],
    }

    openings = build_opening_candidates(candidates, topology, 4)

    assert openings[0]["operation"] == "split_line_for_opening"
    assert openings[0]["connects_to_role"] == "hall_lobby"
    assert openings[0]["connects_to_cluster_id"] == "cluster_hall"
    assert openings[0]["adjacency_edge_id"] == "edge_topology_001"
    assert openings[0]["topology_evidence"] == "OpenCrab topology prior"
    assert openings[0]["opening_priority"] > candidates[0]["patch_priority"]


def test_solver_patch_plan_builds_endpoint_move_candidates() -> None:
    candidates = [
        {
            "element_index": 12,
            "tag": "line",
            "bbox": {"x": 10, "y": 10, "width": 100, "height": 2},
            "center": [60, 11],
            "patch_priority": 700.0,
            "program_cluster_id": "cluster_lounge",
            "program_role": "greenery_lounge",
            "space_region_ids": ["space_lounge"],
        }
    ]
    topology = {
        "nodes": [
            {"id": "cluster_lounge", "type": "program_cluster", "role": "greenery_lounge", "bbox": {"x": 0, "y": 0, "width": 120, "height": 80}},
            {"id": "cluster_hall", "type": "program_cluster", "role": "hall_lobby", "bbox": {"x": 160, "y": 0, "width": 80, "height": 80}},
        ],
        "edges": [
            {
                "id": "edge_topology_002",
                "type": "ontology_cluster_adjacency_target",
                "source": "cluster_lounge",
                "target": "cluster_hall",
                "left_role": "greenery_lounge",
                "right_role": "hall_lobby",
                "rationale": "main hall anchors greenery lounge",
                "evidence": "OpenCrab topology prior",
            }
        ],
    }

    moves = build_endpoint_move_candidates(candidates, topology, 4)

    assert moves[0]["operation"] == "move_line_endpoint"
    assert moves[0]["endpoint"] == "end"
    assert moves[0]["dx"] > 0
    assert moves[0]["dy"] == 0
    assert moves[0]["connects_to_role"] == "hall_lobby"
    assert moves[0]["topology_evidence"] == "OpenCrab topology prior"


def test_solver_patch_plan_builds_polyline_endpoint_move_candidates() -> None:
    candidates = [
        {
            "element_index": 22,
            "tag": "polyline",
            "bbox": {"x": 20, "y": 10, "width": 120, "height": 4},
            "center": [80, 12],
            "patch_priority": 680.0,
            "program_cluster_id": "cluster_fitness",
            "program_role": "fitness_gx",
            "space_region_ids": ["space_fitness"],
        }
    ]
    topology = {
        "nodes": [
            {"id": "cluster_fitness", "type": "program_cluster", "role": "fitness_gx", "bbox": {"x": 10, "y": 0, "width": 140, "height": 80}},
            {"id": "cluster_hall", "type": "program_cluster", "role": "hall_lobby", "bbox": {"x": 190, "y": 0, "width": 80, "height": 80}},
        ],
        "edges": [
            {
                "id": "edge_topology_003",
                "type": "ontology_cluster_adjacency_target",
                "source": "cluster_fitness",
                "target": "cluster_hall",
                "left_role": "fitness_gx",
                "right_role": "hall_lobby",
                "rationale": "fitness is tied to the main hall in the precedent topology",
                "evidence": "OpenCrab topology prior",
            }
        ],
    }

    moves = build_endpoint_move_candidates(candidates, topology, 4)

    assert moves[0]["operation"] == "move_line_endpoint"
    assert moves[0]["tag"] == "polyline"
    assert moves[0]["endpoint"] == "end"
    assert moves[0]["dx"] > 0
    assert moves[0]["connects_to_role"] == "hall_lobby"


def test_recognition_ir_v2_applies_nested_transforms(tmp_path) -> None:
    source = tmp_path / "nested.svg"
    source.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
          <g id="outer" transform="translate(10 5)">
            <g id="inner" transform="scale(2)">
              <rect id="r1" x="1" y="2" width="3" height="4" fill="none" stroke="#111" stroke-width="0.5"/>
              <text id="t1" x="5" y="6">라운지</text>
            </g>
          </g>
        </svg>
        """.strip(),
        encoding="utf-8",
    )
    ir = build_recognition_ir_v2(source)
    assert ir["schema"] == RECOGNITION_IR_SCHEMA
    assert ir["status"] == "active"
    assert ir["document"]["coordinate_space"] == "world"
    assert ir["document"]["unit_scale_mm"] == 1.0
    rect = next(node for node in ir["nodes"] if node["source_id"] == "r1")
    text = next(node for node in ir["nodes"] if node["source_id"] == "t1")
    assert rect["group_path"] == ["outer", "inner"]
    assert rect["bbox"] == {"x": 12.0, "y": 9.0, "w": 6.0, "h": 8.0}
    assert text["text"]["anchor"] == [20.0, 17.0]
    assert text["text"]["content"] == "라운지"


def test_recognition_ir_v2_strips_svg_doctype_without_resolving(tmp_path) -> None:
    source = tmp_path / "doctype.svg"
    source.write_text(
        """
        <?xml version="1.0"?>
        <!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
          <rect id="r1" x="1" y="1" width="2" height="3"/>
        </svg>
        """.strip(),
        encoding="utf-8",
    )
    ir = build_recognition_ir_v2(source)
    assert ir["status"] == "active"
    assert ir["summary"]["primitive_count"] == 1


def test_recognition_ir_v2_flattens_basic_paths_in_world_coordinates(tmp_path) -> None:
    source = tmp_path / "path.svg"
    source.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
          <g id="outer" transform="translate(10 5)">
            <g id="inner" transform="scale(2)">
              <path id="p1" d="M1 2 H4 V6 H1 Z" fill="none" stroke="#111" stroke-width="0.5"/>
            </g>
          </g>
        </svg>
        """.strip(),
        encoding="utf-8",
    )
    ir = build_recognition_ir_v2(source)
    path = next(node for node in ir["nodes"] if node["source_id"] == "p1")
    assert path["tag"] == "path"
    assert path["bbox"] == {"x": 12.0, "y": 9.0, "w": 6.0, "h": 8.0}
    assert path["is_closed"] is True
    assert path["analytic"]["subpath_count"] == 1
    assert ir["warnings"] == []


def test_recognition_ir_v2_classifies_basic_drawing_roles(tmp_path) -> None:
    source = tmp_path / "roles.svg"
    source.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
          <rect id="room" x="50" y="50" width="700" height="420" fill="none" stroke="#111" stroke-width="5"/>
          <rect id="column" x="100" y="100" width="22" height="22" fill="#111"/>
          <line id="wall" x1="90" y1="260" x2="690" y2="260" stroke="#111" stroke-width="7"/>
          <text id="label" x="80" y="90">피트니스</text>
        </svg>
        """.strip(),
        encoding="utf-8",
    )
    ir = build_recognition_ir_v2(source)
    roles = {node["source_id"]: node["role_hint"] for node in ir["nodes"]}
    assert roles["room"] == "room_envelope"
    assert roles["column"] == "column"
    assert roles["wall"] == "wall"
    assert roles["label"] == "label"
    assert ir["summary"]["room_envelope_candidate_count"] == 1
    assert ir["summary"]["column_candidate_count"] == 1
    assert ir["summary"]["wall_candidate_count"] == 1
