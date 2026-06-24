from __future__ import annotations

from typing import Any

RECOGNITION_IR_SCHEMA = "crab-archi-design-recognition-ir-v2"
PARSER_VERSION = "2.0.0"


def stable_node_id(index: int) -> str:
    return f"n{index:04d}"


def empty_recognition_ir(source_path: str) -> dict[str, Any]:
    return {
        "schema": RECOGNITION_IR_SCHEMA,
        "parser_version": PARSER_VERSION,
        "document": {
            "source_path": source_path,
            "coordinate_space": "world",
        },
        "nodes": [],
        "raster_nodes": [],
        "warnings": [],
        "summary": {
            "primitive_count": 0,
            "column_candidate_count": 0,
            "wall_candidate_count": 0,
            "room_envelope_candidate_count": 0,
            "label_count": 0,
            "protected_candidate_count": 0,
        },
    }
