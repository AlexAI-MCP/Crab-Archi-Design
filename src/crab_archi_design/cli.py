from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import html
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = Path("projects")
OPENCRAB_HOMEPAGE = "https://opencrab.sh"
PROJECT_NAME = "crab-archi-design"
PROJECT_VERSION = "0.1.0"
SVG_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
BUILTIN_ENGINE_ADAPTERS = {
    "reference-svg-engine": "reference_svg_engine.py",
    "builtin-reference": "reference_svg_engine.py",
    "crab-reference-engine": "reference_svg_engine.py",
    "layout-svg-engine": "layout_svg_engine.py",
    "builtin-layout": "layout_svg_engine.py",
    "crab-layout-engine": "layout_svg_engine.py",
    "room-envelope-engine": "layout_svg_engine.py",
    "same-layer-svg-engine": "same_layer_svg_engine.py",
    "builtin-same-layer": "same_layer_svg_engine.py",
    "crab-same-layer-engine": "same_layer_svg_engine.py",
    "native-svg-patch-engine": "same_layer_svg_engine.py",
}
SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crab_archi_design.qa import build_candidate_quality_report
from crab_archi_design.solver.objective import evaluate_topology_fit
from crab_archi_design.solver.patch_plan import annotate_candidates_with_solver_plan, build_endpoint_move_candidates, build_opening_candidates, patch_role_priority
from crab_archi_design.solver.sizing import standard_role_rows_from_manifest
from crab_archi_design.workflow_contract import audit_workflow_state, build_engine_workflow_contract


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "project"


def is_builtin_engine_adapter(adapter: str | None) -> bool:
    return bool(adapter and adapter in BUILTIN_ENGINE_ADAPTERS)


def engine_adapter_policy(adapter: str | None, allow_custom: bool = False) -> dict[str, Any]:
    if not adapter:
        return {
            "adapter": adapter,
            "allowed": False,
            "adapter_type": "missing",
            "reason": "missing_engine_adapter",
        }
    if is_builtin_engine_adapter(adapter):
        return {
            "adapter": adapter,
            "allowed": True,
            "adapter_type": "builtin",
            "script": BUILTIN_ENGINE_ADAPTERS[adapter],
            "reason": "builtin_adapter_allowlisted",
        }
    return {
        "adapter": adapter,
        "allowed": bool(allow_custom),
        "adapter_type": "custom",
        "script": None,
        "reason": "custom_adapter_explicitly_allowed" if allow_custom else "custom_adapter_requires_explicit_allow_custom_engine",
    }


def require_engine_adapter_allowed(adapter: str, allow_custom: bool = False) -> dict[str, Any]:
    policy = engine_adapter_policy(adapter, allow_custom)
    if not policy["allowed"]:
        allowed = ", ".join(sorted(BUILTIN_ENGINE_ADAPTERS))
        raise SystemExit(
            f"Engine adapter is not allowlisted: {adapter}. "
            f"Use a built-in adapter ({allowed}) or pass --allow-custom-engine for a trusted local adapter."
        )
    return policy


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def project_dir(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return root / slugify(project_id)


def manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "project_manifest.json"


def load_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any]:
    path = manifest_path(project_id, root)
    if not path.exists():
        raise SystemExit(f"Missing project manifest: {path}")
    return read_json(path)


def command_init(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    out_dir = project_dir(args.project_id, root)
    source_svg = Path(args.source_svg).expanduser()
    if not source_svg.exists() and not args.allow_missing_source:
        raise SystemExit(f"Missing source SVG: {source_svg}")

    manifest = {
        "schema": "crab-archi-design-project-v1",
        "created_at": now(),
        "updated_at": now(),
        "project_id": args.project_id,
        "source_svg": str(source_svg),
        "household_count": args.households,
        "standards_files": [str(Path(item).expanduser()) for item in args.standards],
        "ontology_pack": args.ontology_pack,
        "engine_adapter": args.engine_adapter,
        "allow_custom_engine_adapter": bool(getattr(args, "allow_custom_engine", False)),
        "opencrab_mcp": {
            "required": True,
            "homepage": args.opencrab_homepage,
            "mcp_server": args.opencrab_mcp_server,
            "ontology_pack": args.ontology_pack,
            "workflow": [
                "query installed ontology pack and source evidence through OpenCrab MCP",
                "retrieve topology, program adjacency, standards evidence, and reusable design claims",
                "project source ontology constraints onto target drawing recognition IR",
                "compile LLM design intent only after OpenCrab evidence is attached",
                "send native SVG solver candidates back through QA with evidence references",
            ],
            "decision_rule": "design alternatives must cite OpenCrab ontology evidence before SVG mutation",
        },
        "artifacts": {
            "project_dir": str(out_dir),
            "recognition_dir": str(out_dir / "recognition"),
            "topology_dir": str(out_dir / "topology"),
            "constraints_dir": str(out_dir / "constraints"),
            "alternatives_dir": str(out_dir / "alternatives"),
            "edit_intents_dir": str(out_dir / "edit_intents"),
            "evidence_dir": str(out_dir / "evidence"),
            "standards_dir": str(out_dir / "standards"),
            "briefs_dir": str(out_dir / "briefs"),
            "handoffs_dir": str(out_dir / "handoffs"),
            "runs_dir": str(out_dir / "runs"),
            "qa_dir": str(out_dir / "qa"),
            "status_dir": str(out_dir / "status"),
        },
        "hard_constraints": [
            "preserve parking count and parking geometry",
            "preserve columns",
            "preserve cores, stairs, ramps, and egress",
            "preserve community outer shell",
            "native SVG only; no raster overlay",
        ],
    }
    write_json(manifest_path(args.project_id, root), manifest)
    print(manifest_path(args.project_id, root))


def infer_prompt_operations(text: str) -> tuple[str, list[dict[str, Any]]]:
    lowered = text.lower()
    if "aggressive" in lowered or "공격" in text:
        strategy = "aggressive"
    elif "conservative" in lowered or "보수" in text:
        strategy = "conservative"
    else:
        strategy = "balanced"

    operations: list[dict[str, Any]] = []
    if any(term in text for term in ["그리너리", "라운지", "카페", "도서관", "greenery", "lounge", "cafe", "library"]):
        operations.append(
            {
                "target": "greenery_lounge",
                "action": "increase_visual_openness_to_hall" if any(term in text for term in ["열", "개방", "넓", "open", "wide"]) else "revise_lounge_quality",
                "method": "adjust_opening_or_partition",
            }
        )
    if any(term in text for term in ["홀", "로비", "동선", "출입", "hall", "lobby", "circulation", "entry"]):
        operations.append({"target": "main_hall", "action": "improve_connectivity", "method": "re-score_circulation_axis"})
    if any(term in text for term in ["피트니스", "gx", "운동", "fitness"]):
        operations.append({"target": "fitness_gx", "action": "improve_area_efficiency", "method": "merge_fragmented_partitions"})
    if any(term in text for term in ["골프", "스크린", "golf", "screen"]):
        operations.append({"target": "golf_screen", "action": "keep_screen_inside_golf", "method": "validate_golf_cluster_envelope"})
    if any(term in text for term in ["사우나", "샤워", "락커", "라커", "sauna", "shower", "locker"]):
        operations.append({"target": "sauna_locker_shower", "action": "tighten_wellness_cluster", "method": "validate_wet_cluster_adjacency"})
    if any(term in text for term in ["주차", "코어", "기둥", "램프", "계단", "건드리지", "유지", "parking", "core", "column", "ramp", "stair", "preserve", "lock"]):
        operations.append({"target": "protected_constraints", "action": "lock", "method": "enforce_no_go_lock_zones"})
    if not operations:
        operations.append({"target": "design_alternative", "action": "interpret_user_direction", "method": "requires_llm_design_brief_compiler"})
    return strategy, operations


def command_prompt_edit(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    intent_dir = base / "edit_intents"
    seq = len(list(intent_dir.glob("prompt_edit_*.json"))) + 1
    strategy, operations = infer_prompt_operations(args.text)
    intent = {
        "schema": "crab-archi-design-natural-language-edit-intent-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source": {"type": "natural_language", "text": args.text},
        "strategy": strategy,
        "operations": operations,
        "hard_constraints": manifest.get("hard_constraints", []),
        "knowledge_inputs": {
            "standards_files": manifest.get("standards_files", []),
            "ontology_pack": manifest.get("ontology_pack"),
            "opencrab_mcp": manifest.get("opencrab_mcp"),
        },
        "solver_contract": {
            "input": "edit intent + recognition IR + constraint graph + room envelopes",
            "output": "native SVG patch candidate",
            "required_qa": ["no_go_intersection", "lock_intersection", "native_svg_no_images"],
        },
    }
    out = intent_dir / f"prompt_edit_{seq:03d}.json"
    write_json(out, intent)
    print(out)


def command_sketch_intent(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    sketch_path = Path(args.sketch).expanduser()
    sketch = read_json(sketch_path)
    operations = []
    for stroke in sketch.get("strokes", []):
        operations.append(
            {
                "edit_type": stroke.get("mode") or stroke.get("tool") or "mark_up",
                "target_hint": stroke.get("target_hint"),
                "stroke_id": stroke.get("stroke_id"),
                "point_count": len(stroke.get("points", [])),
                "snap_policy": "nearest_wall_room_or_zone",
                "requires_user_confirmation": True,
            }
        )

    base = project_dir(args.project_id, root)
    intent_dir = base / "edit_intents"
    seq = len(list(intent_dir.glob("sketch_edit_*.json"))) + 1
    intent = {
        "schema": "crab-archi-design-doodle-edit-intent-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source": {"type": "vector_sketch_layer", "path": str(sketch_path.resolve())},
        "coordinate_space": sketch.get("coordinate_space", "source_svg_viewbox"),
        "operations": operations,
        "hard_constraints": manifest.get("hard_constraints", []),
        "knowledge_inputs": {
            "standards_files": manifest.get("standards_files", []),
            "ontology_pack": manifest.get("ontology_pack"),
            "opencrab_mcp": manifest.get("opencrab_mcp"),
        },
    }
    out = intent_dir / f"sketch_edit_{seq:03d}.json"
    write_json(out, intent)
    print(out)


def evidence_manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "evidence" / "evidence_manifest.json"


def load_evidence_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = evidence_manifest_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def count_evidence_items(manifest: dict[str, Any] | None) -> int:
    if not manifest:
        return 0
    return len(manifest.get("evidence_items", []))


def evidence_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "verified" if count_evidence_items(manifest) > 0 else "missing"


def constraint_manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "constraints" / "constraint_manifest.json"


def load_constraint_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = constraint_manifest_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def count_constraint_items(manifest: dict[str, Any] | None, enforced_only: bool = False) -> int:
    if not manifest:
        return 0
    items = manifest.get("constraint_items", [])
    if not enforced_only:
        return len(items)
    return sum(1 for item in items if item.get("role") in {"community_shell", "lock", "no_go", "protect", "mutable", "projectable"})


def constraint_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "active" if count_constraint_items(manifest, enforced_only=True) > 0 else "missing"


def standards_manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "standards" / "standards_manifest.json"


def load_standards_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = standards_manifest_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def count_standard_items(manifest: dict[str, Any] | None) -> int:
    if not manifest:
        return 0
    return len(manifest.get("standard_items", []))


def standards_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "active" if count_standard_items(manifest) > 0 else "missing"


def scale_manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "scale" / "scale_manifest.json"


def load_scale_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = scale_manifest_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def scale_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "active" if manifest.get("selected_scale", {}).get("mm_per_world") else "review_required"


def recognition_manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "recognition" / "recognition_manifest.json"


def recognition_ir_v2_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "recognition" / "recognition_ir_v2.json"


def load_recognition_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = recognition_manifest_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def load_recognition_ir_v2(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = recognition_ir_v2_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def recognition_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "active" if manifest.get("source_svg_info", {}).get("xml_parse") == "ok" else "review_required"


def recognition_ir_v2_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "active" if manifest.get("schema") == "crab-archi-design-recognition-ir-v2" else "review_required"


def topology_manifest_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    return project_dir(project_id, root) / "topology" / "topology_manifest.json"


def load_topology_manifest(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> dict[str, Any] | None:
    path = topology_manifest_path(project_id, root)
    if not path.exists():
        return None
    return read_json(path)


def topology_status(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return "missing"
    if manifest.get("status"):
        return str(manifest["status"])
    return "active" if manifest.get("node_count", 0) > 0 and manifest.get("edge_count", 0) > 0 else "review_required"


def classify_label_role(text: str) -> str | None:
    lowered = text.lower()
    rules = [
        ("greenery_lounge", ["그리너리", "라운지", "카페", "도서관", "greenery", "lounge", "cafe", "library"]),
        ("fitness_gx", ["피트니스", "gx", "운동", "fitness"]),
        ("golf_screen", ["골프", "스크린", "golf", "screen"]),
        ("sauna_locker_shower", ["사우나", "샤워", "락커", "라커", "sauna", "shower", "locker"]),
        ("hall_lobby", ["홀", "로비", "lobby", "hall"]),
        ("management_support", ["관리", "방재", "당직", "회의", "탕비", "소장", "용역", "management"]),
        ("toilet_wet_core", ["화장실", "탈의", "욕실", "toilet", "restroom"]),
        ("parking", ["주차", "parking"]),
        ("core_stair_ramp", ["코어", "계단", "램프", "엘리베이터", "stair", "ramp", "elevator"]),
    ]
    for role, terms in rules:
        if any(term in text or term in lowered for term in terms):
            return role
    return None


def element_text(el: ET.Element) -> str:
    return " ".join(part.strip() for part in el.itertext() if part and part.strip())


def svg_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(SVG_NUMBER_RE, str(value).replace(",", ""))
    return float(match.group(0)) if match else default


def svg_numbers(value: Any) -> list[float]:
    if value is None:
        return []
    return [float(item) for item in re.findall(SVG_NUMBER_RE, str(value).replace(",", ""))]


def text_position(el: ET.Element) -> tuple[float | None, float | None, str | None]:
    raw_x = el.attrib.get("x")
    raw_y = el.attrib.get("y")
    if raw_x is not None and raw_y is not None:
        return svg_float(raw_x), svg_float(raw_y), "xy"

    transform = el.attrib.get("transform", "")
    matrix_match = re.search(r"matrix\(([^)]*)\)", transform)
    if matrix_match:
        numbers = svg_numbers(matrix_match.group(1))
        if len(numbers) >= 6:
            return numbers[4], numbers[5], "matrix_transform"

    translate_match = re.search(r"translate\(([^)]*)\)", transform)
    if translate_match:
        numbers = svg_numbers(translate_match.group(1))
        if len(numbers) >= 2:
            return numbers[0], numbers[1], "translate_transform"

    return None, None, None


def svg_points_bbox(raw_points: Any) -> dict[str, float] | None:
    points: list[tuple[float, float]] = []
    if not raw_points:
        return None
    if isinstance(raw_points, str):
        numbers = svg_numbers(raw_points)
        points = list(zip(numbers[0::2], numbers[1::2]))
    elif isinstance(raw_points, list):
        for point in raw_points:
            if isinstance(point, list) and len(point) >= 2:
                points.append((svg_float(point[0]), svg_float(point[1])))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x <= min_x or max_y <= min_y:
        return None
    return {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}


def element_bbox(el: ET.Element, tag: str) -> dict[str, float] | None:
    if tag == "rect":
        width = svg_float(el.attrib.get("width"))
        height = svg_float(el.attrib.get("height"))
        if width > 0 and height > 0:
            return {"x": svg_float(el.attrib.get("x")), "y": svg_float(el.attrib.get("y")), "width": width, "height": height}
    if tag == "line":
        x1 = svg_float(el.attrib.get("x1"))
        y1 = svg_float(el.attrib.get("y1"))
        x2 = svg_float(el.attrib.get("x2"))
        y2 = svg_float(el.attrib.get("y2"))
        min_x, max_x = min(x1, x2), max(x1, x2)
        min_y, max_y = min(y1, y2), max(y1, y2)
        stroke = max(svg_float(el.attrib.get("stroke-width"), 1.0), 1.0)
        if max_x == min_x:
            min_x -= stroke * 0.5
            max_x += stroke * 0.5
        if max_y == min_y:
            min_y -= stroke * 0.5
            max_y += stroke * 0.5
        return {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}
    if tag in {"polyline", "polygon"}:
        return svg_points_bbox(el.attrib.get("points"))
    if tag in {"circle", "ellipse"}:
        cx = svg_float(el.attrib.get("cx"))
        cy = svg_float(el.attrib.get("cy"))
        rx = svg_float(el.attrib.get("rx"), svg_float(el.attrib.get("r")))
        ry = svg_float(el.attrib.get("ry"), svg_float(el.attrib.get("r")))
        if rx > 0 and ry > 0:
            return {"x": cx - rx, "y": cy - ry, "width": rx * 2, "height": ry * 2}
    if tag == "path":
        numbers = svg_numbers(el.attrib.get("d", ""))
        points = list(zip(numbers[0::2], numbers[1::2]))
        return svg_points_bbox([[x, y] for x, y in points])
    return None


def bbox_area(box: dict[str, float]) -> float:
    return max(0.0, box.get("width", 0.0)) * max(0.0, box.get("height", 0.0))


def bbox_aspect(box: dict[str, float]) -> float:
    width = max(0.001, box.get("width", 0.0))
    height = max(0.001, box.get("height", 0.0))
    return max(width / height, height / width)


def bbox_center(box: dict[str, float]) -> tuple[float, float]:
    return (box.get("x", 0.0) + box.get("width", 0.0) * 0.5, box.get("y", 0.0) + box.get("height", 0.0) * 0.5)


def point_distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5


def point_in_bbox(point: tuple[float, float], box: dict[str, float], pad: float = 0.0) -> bool:
    x, y = point
    return box.get("x", 0.0) - pad <= x <= box.get("x", 0.0) + box.get("width", 0.0) + pad and box.get("y", 0.0) - pad <= y <= box.get("y", 0.0) + box.get("height", 0.0) + pad


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            at_x = (previous_x - current_x) * (y - current_y) / ((previous_y - current_y) or 1e-9) + current_x
            if x < at_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def bbox_sample_points(box: dict[str, float]) -> list[tuple[float, float]]:
    x = box.get("x", 0.0)
    y = box.get("y", 0.0)
    width = box.get("width", 0.0)
    height = box.get("height", 0.0)
    return [
        (x, y),
        (x + width, y),
        (x, y + height),
        (x + width, y + height),
        (x + width * 0.5, y + height * 0.5),
    ]


def bbox_touches_polygon(box: dict[str, float], polygon: list[tuple[float, float]]) -> bool:
    if not box or not polygon:
        return False
    return any(point_in_polygon(point, polygon) for point in bbox_sample_points(box)) or any(point_in_bbox(point, box) for point in polygon)


def label_point(label: dict[str, Any]) -> tuple[float, float] | None:
    if label.get("x") is None or label.get("y") is None:
        return None
    return (svg_float(label.get("x")), svg_float(label.get("y")))


def normalize_bbox_dict(box: dict[str, Any] | None) -> dict[str, float]:
    box = box or {}
    return {
        "x": svg_float(box.get("x")),
        "y": svg_float(box.get("y")),
        "width": svg_float(box.get("width", box.get("w"))),
        "height": svg_float(box.get("height", box.get("h"))),
    }


def recognition_ir_v2_program_labels(ir: dict[str, Any] | None, max_labels: int = 500) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    if recognition_ir_v2_status(ir) != "active":
        return labels
    for node in ir.get("nodes", []):
        if node.get("tag") != "text":
            continue
        text_info = node.get("text") or {}
        content = str(text_info.get("content") or "").strip()
        role = classify_label_role(content)
        anchor = text_info.get("anchor")
        if not content or not role or not isinstance(anchor, list) or len(anchor) < 2:
            continue
        labels.append(
            {
                "text": content,
                "role_hint": role,
                "x": float(anchor[0]),
                "y": float(anchor[1]),
                "position_source": "recognition_ir_v2.text_anchor",
                "source_node_id": node.get("id"),
                "source_id": node.get("source_id"),
            }
        )
        if len(labels) >= max_labels:
            break
    return labels


def recognition_ir_v2_geometry_candidates(ir: dict[str, Any] | None, max_items: int = 1200) -> dict[str, Any]:
    candidates = {
        "column_candidates": [],
        "wall_candidates": [],
        "room_envelope_candidates": [],
        "summary": {
            "column_candidate_count": 0,
            "wall_candidate_count": 0,
            "room_envelope_candidate_count": 0,
        },
    }
    if recognition_ir_v2_status(ir) != "active":
        return candidates
    role_to_key = {
        "column": "column_candidates",
        "wall": "wall_candidates",
        "room_envelope": "room_envelope_candidates",
    }
    total_counts = {key: 0 for key in candidates["summary"]}
    for node in ir.get("nodes", []):
        key = role_to_key.get(str(node.get("role_hint")))
        if not key:
            continue
        summary_key = key.replace("s", "_count")
        total_counts[summary_key] = total_counts.get(summary_key, 0) + 1
        if len(candidates[key]) >= max_items:
            continue
        candidates[key].append(
            {
                "id": node.get("id"),
                "source_id": node.get("source_id"),
                "index": node.get("source_document_index") if node.get("editable_source") else None,
                "source_document_index": node.get("source_document_index"),
                "instance_document_index": node.get("instance_document_index"),
                "instance_source_id": node.get("instance_source_id"),
                "from_use_instance": bool(node.get("from_use_instance")),
                "editable": bool(node.get("editable_source")),
                "addressing": "source_svg_element_index",
                "tag": node.get("tag"),
                "bbox": normalize_bbox_dict(node.get("bbox")),
                "bbox_mm": node.get("bbox_mm"),
                "area_m2": node.get("area_m2"),
                "area_mm2": node.get("area_mm2"),
                "perimeter_mm": node.get("perimeter_mm"),
                "role_hint": f"{node.get('role_hint')}_candidate",
                "role_confidence": node.get("role_confidence"),
                "polygon": node.get("polygon", [])[:64],
                "class": (node.get("style") or {}).get("class"),
                "stroke_width": (node.get("style") or {}).get("stroke_width"),
                "fill": (node.get("style") or {}).get("fill"),
                "stroke": (node.get("style") or {}).get("stroke"),
                "source": "recognition_ir_v2.nodes",
            }
        )
    candidates["summary"] = total_counts
    return candidates


SPACE_REGION_ROLES = {
    "greenery_lounge",
    "fitness_gx",
    "golf_screen",
    "hall_lobby",
    "management_support",
    "sauna_locker_shower",
    "toilet_wet_core",
}


def recognition_ir_v2_axis_walls(ir: dict[str, Any] | None, min_length: float = 20.0) -> list[dict[str, Any]]:
    walls: list[dict[str, Any]] = []
    if recognition_ir_v2_status(ir) != "active":
        return walls
    for node in ir.get("nodes", []):
        if node.get("role_hint") != "wall":
            continue
        box = normalize_bbox_dict(node.get("bbox"))
        width = box["width"]
        height = box["height"]
        axis = None
        if width >= min_length and width >= max(1.0, height * 4.0):
            axis = "horizontal"
        elif height >= min_length and height >= max(1.0, width * 4.0):
            axis = "vertical"
        if axis:
            walls.append({"id": node.get("id"), "axis": axis, "bbox": box, "tag": node.get("tag")})
    return walls


def boundary_candidates(point: tuple[float, float], axis_walls: list[dict[str, Any]], max_distance: float = 800.0) -> dict[str, list[dict[str, Any]]]:
    x, y = point
    candidates = {"left": [], "right": [], "top": [], "bottom": []}
    for wall in axis_walls:
        box = wall["bbox"]
        if wall["axis"] == "vertical" and box["y"] - 20.0 <= y <= box["y"] + box["height"] + 20.0:
            distance = box["x"] - x
            if -max_distance <= distance < 0:
                candidates["left"].append({**wall, "distance": abs(distance), "line": box["x"]})
            elif 0 < distance <= max_distance:
                candidates["right"].append({**wall, "distance": distance, "line": box["x"]})
        elif wall["axis"] == "horizontal" and box["x"] - 20.0 <= x <= box["x"] + box["width"] + 20.0:
            distance = box["y"] - y
            if -max_distance <= distance < 0:
                candidates["top"].append({**wall, "distance": abs(distance), "line": box["y"]})
            elif 0 < distance <= max_distance:
                candidates["bottom"].append({**wall, "distance": distance, "line": box["y"]})
    for side in candidates:
        candidates[side] = sorted(candidates[side], key=lambda item: (float(item["distance"]), str(item.get("id"))))[:8]
    return candidates


def choose_space_boundary(candidates: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for left in candidates["left"]:
        for right in candidates["right"]:
            width = float(right["line"]) - float(left["line"])
            if width < 45.0 or width > 900.0:
                continue
            for top in candidates["top"]:
                for bottom in candidates["bottom"]:
                    height = float(bottom["line"]) - float(top["line"])
                    if height < 35.0 or height > 700.0:
                        continue
                    close_edge_penalty = sum(20.0 - min(20.0, float(item["distance"])) for item in [left, right, top, bottom])
                    score = sum(float(item["distance"]) for item in [left, right, top, bottom]) + close_edge_penalty * 4.0
                    option = {
                        "bbox": {"x": float(left["line"]), "y": float(top["line"]), "width": width, "height": height},
                        "boundary_walls": {"left": left["id"], "right": right["id"], "top": top["id"], "bottom": bottom["id"]},
                        "boundary_distances": {
                            "left": float(left["distance"]),
                            "right": float(right["distance"]),
                            "top": float(top["distance"]),
                            "bottom": float(bottom["distance"]),
                        },
                        "score": round(score, 3),
                        "confidence": round(max(0.25, min(0.85, 0.85 - close_edge_penalty / 120.0)), 3),
                    }
                    if best is None or option["score"] < best["score"]:
                        best = option
    return best


def recognition_ir_v2_space_regions(labels: list[dict[str, Any]], ir: dict[str, Any] | None, max_regions: int = 200) -> list[dict[str, Any]]:
    axis_walls = recognition_ir_v2_axis_walls(ir)
    regions: list[dict[str, Any]] = []
    for label in labels:
        role = label.get("role_hint")
        point = label_point(label)
        if role not in SPACE_REGION_ROLES or point is None:
            continue
        boundary = choose_space_boundary(boundary_candidates(point, axis_walls))
        if not boundary:
            continue
        regions.append(
            {
                "label": label.get("text"),
                "role": role,
                "point": list(point),
                "bbox": boundary["bbox"],
                "boundary_walls": boundary["boundary_walls"],
                "boundary_distances": boundary["boundary_distances"],
                "confidence": boundary["confidence"],
                "source_node_id": label.get("source_node_id"),
                "source": "recognition_ir_v2.wall_ray_space_region",
            }
        )
        if len(regions) >= max_regions:
            break
    return regions


def geometry_role_hint(tag: str, box: dict[str, float], el: ET.Element, viewbox: list[float]) -> str | None:
    max_dim = max(viewbox[2], viewbox[3])
    drawing_area = max(1.0, viewbox[2] * viewbox[3])
    area = bbox_area(box)
    stroke_width = svg_float(el.attrib.get("stroke-width"))
    fill = str(el.attrib.get("fill", "")).strip().lower()
    class_text = " ".join(str(el.attrib.get(key, "")) for key in ["id", "class"]).lower()
    width = box.get("width", 0.0)
    height = box.get("height", 0.0)
    min_side = min(width, height)
    max_side = max(width, height)

    if "column" in class_text or "기둥" in class_text:
        return "column_candidate"
    if tag == "rect" and bbox_aspect(box) <= 1.35 and max_dim * 0.004 <= min_side <= max_dim * 0.04 and fill not in {"none", "transparent"}:
        return "column_candidate"
    if stroke_width >= max_dim * 0.0015 or (min_side > 0 and max_side / max(0.001, min_side) >= 8 and area <= drawing_area * 0.015):
        return "wall_candidate"
    if tag in {"rect", "polygon", "path"} and area >= drawing_area * 0.01 and bbox_aspect(box) <= 8:
        return "room_envelope_candidate"
    return None


def collect_geometry_candidates(root: ET.Element, viewbox: list[float]) -> dict[str, Any]:
    primitive_bbox_candidates: list[dict[str, Any]] = []
    column_candidates: list[dict[str, Any]] = []
    wall_candidates: list[dict[str, Any]] = []
    room_envelope_candidates: list[dict[str, Any]] = []
    drawing_area = max(1.0, viewbox[2] * viewbox[3])

    for index, el in enumerate(root.iter(), start=1):
        tag = local_tag(el)
        if tag not in {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}:
            continue
        box = element_bbox(el, tag)
        if not box or bbox_area(box) <= 0:
            continue
        role_hint = geometry_role_hint(tag, box, el, viewbox)
        item = {
            "index": index,
            "tag": tag,
            "id": el.attrib.get("id"),
            "class": el.attrib.get("class"),
            "role_hint": role_hint,
            "bbox": box,
            "stroke_width": svg_float(el.attrib.get("stroke-width")),
            "fill": el.attrib.get("fill"),
            "stroke": el.attrib.get("stroke"),
        }
        primitive_bbox_candidates.append(item)
        if role_hint == "column_candidate":
            column_candidates.append(item)
        if role_hint == "wall_candidate":
            wall_candidates.append(item)
        if role_hint == "room_envelope_candidate" or (tag in {"rect", "polygon", "path"} and bbox_area(box) >= drawing_area * 0.01 and bbox_aspect(box) <= 8):
            room_envelope_candidates.append(item)

    return {
        "primitive_bbox_candidates": primitive_bbox_candidates[:800],
        "column_candidates": column_candidates[:300],
        "wall_candidates": wall_candidates[:500],
        "room_envelope_candidates": room_envelope_candidates[:300],
        "summary": {
            "primitive_bbox_count": len(primitive_bbox_candidates),
            "column_candidate_count": len(column_candidates),
            "wall_candidate_count": len(wall_candidates),
            "room_envelope_candidate_count": len(room_envelope_candidates),
            "protected_geometry_candidate_count": len(column_candidates),
        },
    }


def analyze_svg_recognition(path: Path, max_labels: int = 500) -> dict[str, Any]:
    source_info = inspect_svg(path)
    if source_info.get("xml_parse") != "ok":
        return {
            "status": "review_required",
            "source_svg_info": source_info,
            "element_counts": {},
            "primitive_count": 0,
            "label_candidates": [],
            "program_label_candidates": [],
            "geometry_candidates": {
                "primitive_bbox_candidates": [],
                "column_candidates": [],
                "wall_candidates": [],
                "room_envelope_candidates": [],
                "summary": {
                    "primitive_bbox_count": 0,
                    "column_candidate_count": 0,
                    "wall_candidate_count": 0,
                    "room_envelope_candidate_count": 0,
                    "protected_geometry_candidate_count": 0,
                },
            },
        }

    root = ET.parse(path).getroot()
    element_counts: dict[str, int] = {}
    label_candidates = []
    program_label_candidates = []
    primitive_tags = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse", "text", "use", "image"}
    viewbox = parse_viewbox(root.attrib.get("viewBox"))
    geometry_candidates = collect_geometry_candidates(root, viewbox)

    for el in root.iter():
        tag = local_tag(el)
        element_counts[tag] = element_counts.get(tag, 0) + 1
        if tag in {"text", "tspan"}:
            text = element_text(el)
            if text and len(label_candidates) < max_labels:
                role = classify_label_role(text)
                x, y, position_source = text_position(el)
                label = {
                    "text": text,
                    "role_hint": role,
                    "tag": tag,
                    "id": el.attrib.get("id"),
                    "class": el.attrib.get("class"),
                    "x": x,
                    "y": y,
                    "position_source": position_source,
                }
                label_candidates.append(label)
                if role:
                    program_label_candidates.append(label)

    primitive_count = sum(element_counts.get(tag, 0) for tag in primitive_tags)
    return {
        "status": "active",
        "source_svg_info": source_info,
        "element_counts": element_counts,
        "primitive_count": primitive_count,
        "label_count": len(label_candidates),
        "program_label_count": len(program_label_candidates),
        "label_candidates": label_candidates,
        "program_label_candidates": program_label_candidates,
        "geometry_candidates": geometry_candidates,
    }


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    for encoding in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8-replace"


def row_matches_households(row: dict[str, str], households: int | None) -> bool:
    if households is None:
        return False
    needle = str(households)
    for key, value in row.items():
        text = f"{key} {value}"
        if needle in text and any(term in text.lower() for term in ["세대", "household", "households", "unit", "units"]):
            return True
    return False


def parse_csv_standards(path: Path, households: int | None, max_rows: int = 200) -> dict[str, Any]:
    text, encoding = read_text_with_fallback(path)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    normalized_rows = [{str(key or "").strip(): str(value or "").strip() for key, value in row.items()} for row in rows]
    matched = [row for row in normalized_rows if row_matches_households(row, households)]
    selected = matched or normalized_rows[: min(len(normalized_rows), max_rows)]
    return {
        "parser": "csv.DictReader",
        "encoding": encoding,
        "row_count": len(normalized_rows),
        "matched_household_row_count": len(matched),
        "selected_row_count": len(selected),
        "selected_rows": selected[:max_rows],
    }


def infer_constraint_role(mode: Any, target_hint: Any, override: str | None = None) -> str:
    if override:
        return override
    text = " ".join(str(item or "").lower() for item in [mode, target_hint])
    if any(term in text for term in ["community_shell", "outer_shell", "shell", "커뮤니티", "외곽"]):
        return "community_shell"
    if any(term in text for term in ["no_go", "parking", "ramp", "core", "column", "stair", "주차", "램프", "코어", "기둥", "계단"]):
        return "no_go"
    if any(term in text for term in ["projectable", "투영"]):
        return "projectable"
    if any(term in text for term in ["protect", "보호"]):
        return "protect"
    if any(term in text for term in ["lock", "keep", "preserve", "protect", "유지", "잠금", "보존"]):
        return "lock"
    if any(term in text for term in ["mutable", "변형", "가변", "허용"]):
        return "mutable"
    return "guide"


def parse_metadata(items: list[str]) -> dict[str, str]:
    metadata = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Metadata must use key=value format: {item}")
        key, value = item.split("=", 1)
        metadata[key] = value
    return metadata


def command_evidence_attach(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    evidence_dir = project_dir(args.project_id, root) / "evidence"
    existing = load_evidence_manifest(args.project_id, root) or {
        "schema": "crab-archi-design-evidence-manifest-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "opencrab_mcp": manifest.get("opencrab_mcp"),
        "evidence_items": [],
    }

    source_file = Path(args.source_file).expanduser() if args.source_file else None
    source_payload: dict[str, Any] = {}
    if source_file:
        if not source_file.exists():
            raise SystemExit(f"Missing evidence source file: {source_file}")
        if source_file.suffix.lower() == ".json":
            try:
                source_payload = read_json(source_file)
            except json.JSONDecodeError:
                source_payload = {"text": source_file.read_text(encoding="utf-8")}
        else:
            source_payload = {"text": source_file.read_text(encoding="utf-8")}

    metadata = parse_metadata(args.metadata or [])
    item = {
        "id": args.evidence_id or f"evidence_{len(existing.get('evidence_items', [])) + 1:03d}",
        "attached_at": now(),
        "source": args.source,
        "source_file": str(source_file) if source_file else None,
        "pack_id": args.pack_id or manifest.get("ontology_pack"),
        "query": args.query,
        "summary": args.summary,
        "metadata": metadata,
        "payload": source_payload,
    }
    existing.setdefault("evidence_items", []).append(item)
    existing["updated_at"] = now()
    existing["status"] = "verified" if existing["evidence_items"] else "missing"
    existing["evidence_count"] = len(existing["evidence_items"])
    existing["required_for_final_svg"] = True

    out = evidence_dir / "evidence_manifest.json"
    write_json(out, existing)
    print(out)
    print(json.dumps({"status": existing["status"], "evidence_count": existing["evidence_count"]}, ensure_ascii=False))


def normalize_opencrab_evidence_item(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": f"opencrab_item_{index:03d}",
            "text": str(item),
            "score": None,
            "source": None,
            "metadata": {},
            "raw": item,
        }
    return {
        "id": item.get("id") or item.get("node_id") or item.get("document_id") or f"opencrab_item_{index:03d}",
        "document_id": item.get("document_id"),
        "workspace_id": item.get("workspace_id"),
        "text": item.get("text") or item.get("content") or item.get("summary") or item.get("answer"),
        "score": item.get("score"),
        "source": item.get("source") or item.get("source_file") or item.get("title"),
        "metadata": item.get("metadata", {}),
        "retrieval": item.get("retrieval", {}),
        "raw": item,
    }


def normalize_opencrab_result(result: Any) -> dict[str, Any]:
    if isinstance(result, list):
        evidence = result
        answer = None
        query = None
        status = None
    elif isinstance(result, dict):
        evidence = result.get("evidence") or result.get("items") or result.get("results") or result.get("chunks") or []
        if not isinstance(evidence, list):
            evidence = [evidence]
        answer = result.get("answer") or result.get("summary")
        query = result.get("query") or result.get("question")
        status = result.get("status")
    else:
        evidence = [result]
        answer = None
        query = None
        status = None
    normalized = [normalize_opencrab_evidence_item(item, idx) for idx, item in enumerate(evidence, start=1)]
    return {
        "status": status,
        "query": query,
        "answer": answer,
        "evidence_count": len(normalized),
        "evidence": normalized,
    }


def read_opencrab_result_inputs(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    raw_results: list[dict[str, Any]] = []
    source_labels: list[str] = []
    for raw_path in args.result_file or []:
        path = Path(raw_path).expanduser()
        if not path.exists():
            raise SystemExit(f"Missing OpenCrab result file: {path}")
        raw_results.append(read_json(path))
        source_labels.append(str(path))
    for idx, raw_text in enumerate(args.result_json or [], start=1):
        raw_results.append(json.loads(raw_text))
        source_labels.append(f"inline_json_{idx:03d}")
    if not raw_results and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            raw_results.append(json.loads(text))
            source_labels.append("stdin")
    if not raw_results:
        raise SystemExit("No OpenCrab result supplied. Pass --result-file, --result-json, or pipe JSON to stdin.")
    return raw_results, source_labels


def opencrab_request_query(manifest: dict[str, Any], status: dict[str, Any] | None, args: argparse.Namespace) -> str:
    if args.query:
        return args.query
    pack_id = args.pack_id or manifest.get("ontology_pack") or "community_svg_topology_ontology_v2"
    households = manifest.get("household_count") or args.households or "unknown"
    hard_constraints = ", ".join(str(item) for item in manifest.get("hard_constraints", []))
    metrics = (status or {}).get("metrics", {})
    metric_text = ", ".join(
        f"{key}={value}"
        for key, value in {
            "program_labels": metrics.get("program_label_count"),
            "room_envelopes": metrics.get("room_envelope_candidate_count"),
            "columns": metrics.get("column_candidate_count"),
            "walls": metrics.get("wall_candidate_count"),
        }.items()
        if value is not None
    )
    intent = args.intent or "Generate evidence-backed community layout topology guidance for greenery lounge, fitness/GX, golf/screen golf, sauna/locker/shower, hall/lobby, and support spaces."
    return (
        f"Crab Archi Design OpenCrab MCP request for project {manifest.get('project_id')}. "
        f"Ontology pack: {pack_id}. Household target: {households}. "
        f"Intent: {intent} "
        f"Return precedent topology, adjacency levers, area criteria, protected/mutable zone rules, claims, and evidence references. "
        f"Hard constraints: {hard_constraints}. "
        f"Recognition metrics: {metric_text or 'not available'}. "
        "The response must be usable by opencrab-sync and include answer/summary plus evidence items with source references."
    )


def build_opencrab_request_markdown(request: dict[str, Any]) -> str:
    command = request["next_commands"]["sync_result"]
    arguments = json.dumps(request["recommended_tool_call"]["arguments"], ensure_ascii=False, indent=2)
    return "\n".join(
        [
            f"# OpenCrab MCP Request: {request['project_id']}",
            "",
            f"- Tool: `{request['recommended_tool_call']['tool']}`",
            f"- Ontology pack: `{request.get('pack_id')}`",
            f"- Expected result file: `{request['expected_result_file']}`",
            f"- Next command: `{command}`",
            "",
            "## Tool Arguments",
            "",
            "```json",
            arguments,
            "```",
            "",
            "## Query",
            "",
            request["query"],
            "",
            "## Response Contract",
            "",
            "- Save the OpenCrab MCP response JSON to the expected result file.",
            "- The response should include `answer` or `summary` plus `evidence`, `items`, `results`, or `chunks`.",
            "- Run the next command to attach normalized evidence before any final SVG mutation.",
            "",
        ]
    )


def command_opencrab_request(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    status: dict[str, Any] | None = None
    try:
        status = build_project_status(args.project_id, root)
    except SystemExit:
        status = None
    base = project_dir(args.project_id, root)
    request_dir = Path(args.output_dir).expanduser() if args.output_dir else base / "opencrab"
    if not request_dir.is_absolute():
        request_dir = Path.cwd() / request_dir
    seq = next_sequence(request_dir, "opencrab_request_*.json")
    request_path = request_dir / f"opencrab_request_{seq:03d}.json"
    markdown_path = request_dir / f"opencrab_request_{seq:03d}.md"
    expected_result_file = Path(args.expected_result_file).expanduser() if args.expected_result_file else request_dir / f"opencrab_result_{seq:03d}.json"
    if not expected_result_file.is_absolute():
        expected_result_file = Path.cwd() / expected_result_file
    pack_id = args.pack_id or manifest.get("ontology_pack")
    query = opencrab_request_query(manifest, status, args)
    sync_command = (
        f"crab-archi-design --project-root {shlex.quote(str(root))} opencrab-sync "
        f"--project-id {shlex.quote(args.project_id)} "
        f"--result-file {shlex.quote(str(expected_result_file))} "
        f"--source-tool {shlex.quote(args.source_tool)}"
    )
    request = {
        "schema": "crab-archi-design-opencrab-request-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source_tool": args.source_tool,
        "opencrab_mcp_server": args.opencrab_mcp_server,
        "opencrab_homepage": args.opencrab_homepage,
        "workspace_id": args.workspace_id,
        "pack_id": pack_id,
        "max_results": args.max_results,
        "query": query,
        "recommended_tool_call": {
            "tool": args.source_tool,
            "arguments": {
                "query": query,
                "pack_id": pack_id,
                "workspace_id": args.workspace_id,
                "limit": args.max_results,
            },
        },
        "expected_result_file": str(expected_result_file),
        "next_commands": {
            "sync_result": sync_command,
            "build_topology": f"crab-archi-design --project-root {shlex.quote(str(root))} topology-build --project-id {shlex.quote(args.project_id)}",
        },
        "project_context": {
            "source_svg": manifest.get("source_svg"),
            "households": manifest.get("household_count") or args.households,
            "hard_constraints": manifest.get("hard_constraints", []),
            "opencrab_mcp": manifest.get("opencrab_mcp"),
            "status": status.get("overall_status") if status else None,
            "gates": status.get("gates") if status else {},
            "metrics": status.get("metrics") if status else {},
            "latest_artifacts": status.get("latest_artifacts") if status else {},
        },
        "response_contract": {
            "accepted_top_level_fields": ["answer", "summary", "evidence", "items", "results", "chunks", "query", "status"],
            "evidence_item_fields": ["id", "document_id", "workspace_id", "text", "content", "summary", "source", "title", "score", "metadata"],
            "normalizer": "opencrab-sync",
        },
    }
    write_json(request_path, request)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(build_opencrab_request_markdown(request), encoding="utf-8")
    print(request_path)
    print(markdown_path)
    print(json.dumps({"status": "created", "query": query, "expected_result_file": str(expected_result_file)}, ensure_ascii=False))


def command_opencrab_sync(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    raw_results, source_labels = read_opencrab_result_inputs(args)
    normalized_results = [normalize_opencrab_result(result) for result in raw_results]
    normalized_evidence = [item for result in normalized_results for item in result["evidence"]]
    result_queries = [result.get("query") for result in normalized_results if result.get("query")]
    result_answers = [result.get("answer") for result in normalized_results if result.get("answer")]
    query = args.query or " | ".join(str(item) for item in result_queries if item) or None
    summary = args.summary or "\n\n".join(str(item) for item in result_answers if item) or "OpenCrab MCP evidence synchronized."

    sync_dir = base / "opencrab"
    seq = next_sequence(sync_dir, "opencrab_sync_*.json")
    sync_payload = {
        "schema": "crab-archi-design-opencrab-sync-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source_tool": args.source_tool,
        "workspace_id": args.workspace_id,
        "pack_id": args.pack_id or manifest.get("ontology_pack"),
        "query": query,
        "summary": summary,
        "source_labels": source_labels,
        "normalized_evidence_count": len(normalized_evidence),
        "normalized_results": normalized_results,
        "raw_results": raw_results,
    }
    sync_path = sync_dir / f"opencrab_sync_{seq:03d}.json"
    write_json(sync_path, sync_payload)

    existing = None if args.replace else load_evidence_manifest(args.project_id, root)
    evidence_manifest = existing or {
        "schema": "crab-archi-design-evidence-manifest-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "opencrab_mcp": manifest.get("opencrab_mcp"),
        "evidence_items": [],
    }
    item = {
        "id": args.evidence_id or f"opencrab_sync_{len(evidence_manifest.get('evidence_items', [])) + 1:03d}",
        "attached_at": now(),
        "source": "opencrab_mcp",
        "source_tool": args.source_tool,
        "source_file": str(sync_path),
        "source_labels": source_labels,
        "pack_id": args.pack_id or manifest.get("ontology_pack"),
        "workspace_id": args.workspace_id,
        "query": query,
        "summary": summary,
        "metadata": {
            **parse_metadata(args.metadata or []),
            "sync_file": str(sync_path),
            "normalized_evidence_count": str(len(normalized_evidence)),
        },
        "payload": {
            "schema": "crab-archi-design-opencrab-evidence-payload-v1",
            "normalized_evidence_count": len(normalized_evidence),
            "evidence": normalized_evidence,
        },
    }
    evidence_manifest.setdefault("evidence_items", []).append(item)
    evidence_manifest["updated_at"] = now()
    evidence_manifest["status"] = "verified" if count_evidence_items(evidence_manifest) > 0 and len(normalized_evidence) > 0 else "review_required"
    evidence_manifest["evidence_count"] = len(evidence_manifest["evidence_items"])
    evidence_manifest["required_for_final_svg"] = True
    out = evidence_manifest_path(args.project_id, root)
    write_json(out, evidence_manifest)
    print(sync_path)
    print(out)
    print(
        json.dumps(
            {
                "status": evidence_manifest["status"],
                "evidence_count": evidence_manifest["evidence_count"],
                "normalized_evidence_count": len(normalized_evidence),
            },
            ensure_ascii=False,
        )
    )


def command_constraint_attach(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    sketch_path = Path(args.sketch).expanduser()
    if not sketch_path.exists():
        raise SystemExit(f"Missing sketch file: {sketch_path}")
    sketch = read_json(sketch_path)
    source_info = inspect_svg(Path(manifest["source_svg"]).expanduser())
    fallback_viewbox = parse_viewbox(source_info.get("viewBox"))
    analysis = analyze_sketch(sketch, fallback_viewbox)
    existing = None if args.replace else load_constraint_manifest(args.project_id, root)
    constraint_manifest = existing or {
        "schema": "crab-archi-design-constraint-manifest-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source_svg": manifest.get("source_svg"),
        "constraint_items": [],
    }

    offset = len(constraint_manifest.get("constraint_items", []))
    coordinate_space = sketch.get("coordinate_space", "source_svg_viewbox")
    viewbox = analysis.get("viewBox")
    for index, stroke in enumerate(sketch.get("strokes", []), start=1):
        points = stroke.get("points", [])
        role = infer_constraint_role(stroke.get("mode") or stroke.get("tool"), stroke.get("target_hint"), args.role)
        item = {
            "id": f"constraint_{offset + index:03d}",
            "attached_at": now(),
            "source": args.source,
            "source_file": str(sketch_path.resolve()),
            "role": role,
            "mode": stroke.get("mode") or stroke.get("tool"),
            "target_hint": stroke.get("target_hint"),
            "stroke_id": stroke.get("stroke_id"),
            "coordinate_space": coordinate_space,
            "viewBox": viewbox,
            "point_count": len(points),
            "points": points,
            "solver_policy": "enforce" if role != "guide" else "advisory",
        }
        constraint_manifest.setdefault("constraint_items", []).append(item)

    enforced_count = count_constraint_items(constraint_manifest, enforced_only=True)
    constraint_manifest["updated_at"] = now()
    constraint_manifest["status"] = "active" if enforced_count > 0 and analysis["out_of_bounds_points"] == 0 else "review_required"
    constraint_manifest["constraint_count"] = count_constraint_items(constraint_manifest)
    constraint_manifest["enforced_constraint_count"] = enforced_count
    constraint_manifest["latest_sketch_analysis"] = analysis
    constraint_manifest["required_for_final_svg"] = True

    out = constraint_manifest_path(args.project_id, root)
    write_json(out, constraint_manifest)
    print(out)
    print(
        json.dumps(
            {
                "status": constraint_manifest["status"],
                "constraint_count": constraint_manifest["constraint_count"],
                "enforced_constraint_count": enforced_count,
                "out_of_bounds_points": analysis["out_of_bounds_points"],
            },
            ensure_ascii=False,
        )
    )


def command_standards_attach(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    households = args.households if args.households is not None else manifest.get("household_count")
    raw_files = args.file or manifest.get("standards_files", [])
    if not raw_files:
        raise SystemExit("No standards files supplied. Pass --file or initialize the project with --standards.")

    existing = None if args.replace else load_standards_manifest(args.project_id, root)
    standards_manifest = existing or {
        "schema": "crab-archi-design-standards-manifest-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "household_count": households,
        "standard_items": [],
    }

    offset = len(standards_manifest.get("standard_items", []))
    for index, raw_file in enumerate(raw_files, start=1):
        standard_file = Path(raw_file).expanduser()
        if not standard_file.exists():
            raise SystemExit(f"Missing standards file: {standard_file}")
        suffix = standard_file.suffix.lower()
        payload: dict[str, Any]
        parser = "file_reference"
        if suffix == ".csv":
            payload = parse_csv_standards(standard_file, households)
            parser = payload["parser"]
        elif suffix == ".json":
            payload = {"json": read_json(standard_file)}
            parser = "json"
        else:
            payload = {"note": "File verified and attached; detailed parsing should be handled by the engine adapter."}

        item = {
            "id": args.standard_id or f"standard_{offset + index:03d}",
            "attached_at": now(),
            "source": args.source,
            "source_file": str(standard_file.resolve()),
            "file_type": suffix.lstrip(".") or "unknown",
            "parser": parser,
            "household_count": households,
            "summary": args.summary,
            "metadata": parse_metadata(args.metadata or []),
            "payload": payload,
        }
        standards_manifest.setdefault("standard_items", []).append(item)

    standards_manifest["updated_at"] = now()
    standards_manifest["household_count"] = households
    standards_manifest["status"] = "active" if standards_manifest["standard_items"] else "missing"
    standards_manifest["standard_count"] = count_standard_items(standards_manifest)
    standards_manifest["required_for_final_svg"] = True

    out = standards_manifest_path(args.project_id, root)
    write_json(out, standards_manifest)
    print(out)
    print(
        json.dumps(
            {
                "status": standards_manifest["status"],
                "standard_count": standards_manifest["standard_count"],
                "household_count": households,
            },
            ensure_ascii=False,
        )
    )


def compute_mm_per_world(args: argparse.Namespace) -> float:
    if args.mm_per_world is not None:
        value = float(args.mm_per_world)
    elif args.known_mm is not None and args.known_world_length is not None:
        value = float(args.known_mm) / float(args.known_world_length)
    else:
        raise SystemExit("Pass --mm-per-world or both --known-mm and --known-world-length.")
    if value <= 0:
        raise SystemExit("Scale must be positive.")
    return value


def command_scale_attach(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    mm_per_world = compute_mm_per_world(args)
    confidence = max(0.0, min(1.0, float(args.confidence)))
    existing = None if args.replace else load_scale_manifest(args.project_id, root)
    scale_manifest = existing or {
        "schema": "crab-archi-design-scale-manifest-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source_svg": manifest.get("source_svg"),
        "scale_items": [],
    }
    item = {
        "id": args.scale_id or f"scale_{len(scale_manifest.get('scale_items', [])) + 1:03d}",
        "attached_at": now(),
        "source": args.source,
        "method": args.method,
        "mm_per_world": round(mm_per_world, 6),
        "m_per_world": round(mm_per_world / 1000.0, 9),
        "known_mm": args.known_mm,
        "known_world_length": args.known_world_length,
        "confidence": round(confidence, 3),
        "evidence": args.evidence,
        "note": args.note,
        "metadata": parse_metadata(args.metadata or []),
    }
    scale_manifest.setdefault("scale_items", []).append(item)
    selected = sorted(scale_manifest["scale_items"], key=lambda value: (float(value.get("confidence") or 0.0), str(value.get("id") or "")), reverse=True)[0]
    scale_manifest["selected_scale"] = selected
    scale_manifest["updated_at"] = now()
    scale_manifest["status"] = "active" if selected.get("mm_per_world") and float(selected.get("confidence") or 0.0) >= 0.5 else "review_required"
    scale_manifest["scale_count"] = len(scale_manifest["scale_items"])
    scale_manifest["required_for_area_comparison"] = True
    out = scale_manifest_path(args.project_id, root)
    write_json(out, scale_manifest)
    print(out)
    print(
        json.dumps(
            {
                "status": scale_manifest["status"],
                "scale_count": scale_manifest["scale_count"],
                "selected_mm_per_world": selected.get("mm_per_world"),
                "selected_confidence": selected.get("confidence"),
            },
            ensure_ascii=False,
        )
    )


def command_recognize_svg(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    source_svg = Path(args.source_svg).expanduser() if args.source_svg else Path(manifest["source_svg"]).expanduser()
    if not source_svg.exists():
        raise SystemExit(f"Missing source SVG: {source_svg}")

    analysis = analyze_svg_recognition(source_svg, max_labels=args.max_labels)
    recognition_manifest = {
        "schema": "crab-archi-design-recognition-manifest-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "source_svg": str(source_svg),
        "status": analysis["status"],
        "coordinate_space": "source_svg_viewbox",
        "source_svg_info": analysis["source_svg_info"],
        "element_counts": analysis["element_counts"],
        "primitive_count": analysis["primitive_count"],
        "label_count": analysis.get("label_count", 0),
        "program_label_count": analysis.get("program_label_count", 0),
        "label_candidates": analysis["label_candidates"],
        "program_label_candidates": analysis["program_label_candidates"],
        "geometry_candidates": analysis.get("geometry_candidates", {}),
        "geometry_summary": analysis.get("geometry_candidates", {}).get("summary", {}),
        "required_for_final_svg": True,
        "recognition_scope": [
            "svg_xml_parse",
            "viewBox",
            "primitive_counts",
            "primitive_bounding_boxes",
            "column_candidates",
            "wall_candidates",
            "room_envelope_candidates",
            "text_label_candidates",
            "program_role_hints",
            "native_svg_image_detection",
        ],
    }
    out = recognition_manifest_path(args.project_id, root)
    write_json(out, recognition_manifest)
    print(out)
    print(
        json.dumps(
            {
                "status": recognition_manifest["status"],
                "primitive_count": recognition_manifest["primitive_count"],
                "label_count": recognition_manifest["label_count"],
                "program_label_count": recognition_manifest["program_label_count"],
                "column_candidate_count": recognition_manifest["geometry_summary"].get("column_candidate_count", 0),
                "wall_candidate_count": recognition_manifest["geometry_summary"].get("wall_candidate_count", 0),
                "image_elements": recognition_manifest["source_svg_info"].get("image_elements"),
            },
            ensure_ascii=False,
        )
    )


def command_recognize_svg_v2(args: argparse.Namespace) -> None:
    from crab_archi_design.recognition import build_recognition_ir_v2

    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    source_svg = Path(args.source_svg).expanduser() if args.source_svg else Path(manifest["source_svg"]).expanduser()
    if not source_svg.exists():
        raise SystemExit(f"Missing source SVG: {source_svg}")
    ir = build_recognition_ir_v2(source_svg)
    ir["project_id"] = args.project_id
    ir["created_at"] = now()
    ir["required_for_final_svg"] = False
    out = project_dir(args.project_id, root) / "recognition" / "recognition_ir_v2.json"
    write_json(out, ir)
    print(out)
    print(
        json.dumps(
            {
                "status": ir.get("status"),
                "schema": ir.get("schema"),
                "parser_version": ir.get("parser_version"),
                "node_count": len(ir.get("nodes", [])),
                "raster_count": len(ir.get("raster_nodes", [])),
                "warning_count": len(ir.get("warnings", [])),
                "summary": ir.get("summary", {}),
            },
            ensure_ascii=False,
        )
    )


def row_area_hint(row: dict[str, str]) -> float | None:
    for key, value in row.items():
        text = f"{key} {value}"
        if not any(term in text.lower() for term in ["면적", "area", "평", "sqm", "m2", "㎡"]):
            continue
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if match:
            return float(match.group(0))
    for value in row.values():
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if match:
            return float(match.group(0))
    return None


def standard_role_rows(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return standard_role_rows_from_manifest(manifest)


def node_type_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        node_type = str(node.get("type") or "unknown")
        counts[node_type] = counts.get(node_type, 0) + 1
    return counts


def edge_type_counts(edges: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges:
        edge_type = str(edge.get("type") or "unknown")
        counts[edge_type] = counts.get(edge_type, 0) + 1
    return counts


def add_topology_edge(edges: list[dict[str, Any]], source: str, target: str, edge_type: str, **properties: Any) -> None:
    edges.append(
        {
            "id": f"edge_{len(edges) + 1:04d}",
            "source": source,
            "target": target,
            "type": edge_type,
            **properties,
        }
    )


def nearest_envelope(point: tuple[float, float], envelope_nodes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float | None]:
    best_node: dict[str, Any] | None = None
    best_distance: float | None = None
    for node in envelope_nodes:
        box = node.get("bbox") or {}
        distance = point_distance(point, bbox_center(box))
        if best_distance is None or distance < best_distance:
            best_node = node
            best_distance = distance
    return best_node, best_distance


def containing_envelope(point: tuple[float, float], envelope_nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    containers = [node for node in envelope_nodes if point_in_bbox(point, node.get("bbox") or {})]
    if not containers:
        return None
    return sorted(containers, key=lambda node: bbox_area(node.get("bbox") or {}))[0]


def constraint_points(item: dict[str, Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for point in item.get("points", []):
        if isinstance(point, list) and len(point) >= 2:
            points.append((svg_float(point[0]), svg_float(point[1])))
    return points


def points_bbox(points: list[tuple[float, float]]) -> dict[str, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}


def bbox_union(boxes: list[dict[str, float]]) -> dict[str, float] | None:
    boxes = [box for box in boxes if box and box.get("width", 0.0) >= 0 and box.get("height", 0.0) >= 0]
    if not boxes:
        return None
    min_x = min(box["x"] for box in boxes)
    min_y = min(box["y"] for box in boxes)
    max_x = max(box["x"] + box["width"] for box in boxes)
    max_y = max(box["y"] + box["height"] for box in boxes)
    return {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}


def bbox_intersects(left: dict[str, float], right: dict[str, float], pad: float = 0.0) -> bool:
    if not left or not right:
        return False
    return not (
        left["x"] + left["width"] < right["x"] - pad
        or right["x"] + right["width"] < left["x"] - pad
        or left["y"] + left["height"] < right["y"] - pad
        or right["y"] + right["height"] < left["y"] - pad
    )


def nodes_touching_constraint(nodes: list[dict[str, Any]], points: list[tuple[float, float]], limit: int) -> list[dict[str, Any]]:
    if len(points) < 3:
        return nodes[:limit]
    matches = [node for node in nodes if bbox_touches_polygon(node.get("bbox") or {}, points)]
    return matches[:limit]


def labels_inside_constraint(nodes: list[dict[str, Any]], points: list[tuple[float, float]], limit: int) -> list[dict[str, Any]]:
    if len(points) < 3:
        return nodes[:limit]
    matches = []
    for node in nodes:
        point_raw = node.get("point")
        if point_raw and point_in_polygon((float(point_raw[0]), float(point_raw[1])), points):
            matches.append(node)
    return matches[:limit]


def build_program_clusters(space_region_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_role: dict[str, list[dict[str, Any]]] = {}
    for region in space_region_nodes:
        role = str(region.get("role") or "")
        if role:
            by_role.setdefault(role, []).append(region)
    clusters: list[dict[str, Any]] = []
    cluster_index = 1
    for role in sorted(by_role):
        for regions in connected_region_groups(by_role[role]):
            box = bbox_union([region.get("bbox") or {} for region in regions])
            if not box:
                continue
            confidences = [float(region.get("confidence") or 0.0) for region in regions]
            labels = sorted({str(region.get("label")) for region in regions if region.get("label")})
            clusters.append(
                {
                    "id": f"program_cluster_{cluster_index:03d}",
                    "type": "program_cluster",
                    "role": role,
                    "labels": labels,
                    "space_region_ids": [region["id"] for region in regions],
                    "bbox": box,
                    "area": round(bbox_area(box), 3),
                    "space_region_count": len(regions),
                    "confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
                    "source": "topology.space_region_role_cluster",
                }
            )
            cluster_index += 1
    return clusters


def connected_region_groups(regions: list[dict[str, Any]], max_gap: float = 260.0) -> list[list[dict[str, Any]]]:
    ordered = sorted(regions, key=lambda region: (normalize_bbox_dict(region.get("bbox"))["x"], normalize_bbox_dict(region.get("bbox"))["y"], region["id"]))
    groups: list[list[dict[str, Any]]] = []
    unvisited = {region["id"] for region in ordered}
    by_id = {region["id"]: region for region in ordered}
    for region in ordered:
        if region["id"] not in unvisited:
            continue
        group: list[dict[str, Any]] = []
        queue = [region]
        unvisited.remove(region["id"])
        while queue:
            current = queue.pop(0)
            group.append(current)
            current_box = normalize_bbox_dict(current.get("bbox"))
            for other_id in list(unvisited):
                other = by_id[other_id]
                if bbox_gap_distance(current_box, normalize_bbox_dict(other.get("bbox"))) <= max_gap:
                    unvisited.remove(other_id)
                    queue.append(other)
        groups.append(group)
    return groups


def bbox_gap_distance(left: dict[str, float], right: dict[str, float]) -> float:
    left_max_x = left["x"] + left["width"]
    left_max_y = left["y"] + left["height"]
    right_max_x = right["x"] + right["width"]
    right_max_y = right["y"] + right["height"]
    gap_x = max(0.0, max(left["x"], right["x"]) - min(left_max_x, right_max_x))
    gap_y = max(0.0, max(left["y"], right["y"]) - min(left_max_y, right_max_y))
    return (gap_x**2 + gap_y**2) ** 0.5


def build_topology_manifest(project_id: str, root: Path) -> dict[str, Any]:
    project = load_manifest(project_id, root)
    recognition = load_recognition_manifest(project_id, root)
    recognition_ir = load_recognition_ir_v2(project_id, root)
    constraints = load_constraint_manifest(project_id, root)
    standards = load_standards_manifest(project_id, root)
    evidence = load_evidence_manifest(project_id, root)
    use_ir_v2 = recognition_ir_v2_status(recognition_ir) == "active"
    geometry = recognition_ir_v2_geometry_candidates(recognition_ir) if use_ir_v2 else (recognition or {}).get("geometry_candidates", {})
    program_labels = recognition_ir_v2_program_labels(recognition_ir) if use_ir_v2 else (recognition or {}).get("program_label_candidates", [])
    space_regions = recognition_ir_v2_space_regions(program_labels, recognition_ir) if use_ir_v2 else []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    role_to_nodes: dict[str, list[str]] = {}

    def register_role(role: str | None, node_id: str) -> None:
        if role:
            role_to_nodes.setdefault(role, []).append(node_id)

    for index, item in enumerate(program_labels, start=1):
        point = label_point(item)
        node = {
            "id": f"program_label_{index:03d}",
            "type": "program_label",
            "label": item.get("text"),
            "role": item.get("role_hint"),
            "point": list(point) if point else None,
            "source": item.get("source", "recognition_ir_v2.program_labels" if use_ir_v2 else "recognition.program_label_candidates"),
            "source_node_id": item.get("source_node_id"),
        }
        nodes.append(node)
        register_role(node.get("role"), node["id"])

    for index, item in enumerate(geometry.get("room_envelope_candidates", []), start=1):
        node = {
            "id": f"room_envelope_{index:03d}",
            "type": "room_envelope",
            "bbox": normalize_bbox_dict(item.get("bbox")),
            "tag": item.get("tag"),
            "source_id": item.get("id"),
            "source": item.get("source", "recognition_ir_v2.nodes" if use_ir_v2 else "recognition.geometry_candidates.room_envelope_candidates"),
        }
        nodes.append(node)

    for index, item in enumerate(space_regions, start=1):
        node = {
            "id": f"space_region_{index:03d}",
            "type": "space_region",
            "label": item.get("label"),
            "role": item.get("role"),
            "point": item.get("point"),
            "bbox": normalize_bbox_dict(item.get("bbox")),
            "boundary_walls": item.get("boundary_walls"),
            "boundary_distances": item.get("boundary_distances"),
            "confidence": item.get("confidence"),
            "source_node_id": item.get("source_node_id"),
            "source": item.get("source"),
        }
        nodes.append(node)

    for index, item in enumerate(geometry.get("column_candidates", []), start=1):
        node = {
            "id": f"column_{index:03d}",
            "type": "structural_column",
            "bbox": normalize_bbox_dict(item.get("bbox")),
            "tag": item.get("tag"),
            "source_id": item.get("id"),
            "protected": True,
            "source": item.get("source", "recognition_ir_v2.nodes" if use_ir_v2 else "recognition.geometry_candidates.column_candidates"),
        }
        nodes.append(node)

    for index, item in enumerate(geometry.get("wall_candidates", []), start=1):
        node = {
            "id": f"wall_{index:03d}",
            "type": "wall_candidate",
            "bbox": normalize_bbox_dict(item.get("bbox")),
            "tag": item.get("tag"),
            "source_id": item.get("id"),
            "source": item.get("source", "recognition_ir_v2.nodes" if use_ir_v2 else "recognition.geometry_candidates.wall_candidates"),
        }
        nodes.append(node)

    for index, item in enumerate((constraints or {}).get("constraint_items", []), start=1):
        points = constraint_points(item)
        node = {
            "id": f"constraint_{index:03d}",
            "type": "constraint",
            "role": item.get("role"),
            "mode": item.get("mode"),
            "target_hint": item.get("target_hint"),
            "point_count": item.get("point_count"),
            "points": [[point[0], point[1]] for point in points],
            "bbox": points_bbox(points),
            "solver_policy": item.get("solver_policy"),
            "source": "constraints.constraint_items",
        }
        nodes.append(node)

    for role, entry in standard_role_rows(standards).items():
        node = {
            "id": f"standard_program_{role}",
            "type": "standard_program",
            "role": role,
            "row_count": len(entry["rows"]),
            "target_area_hint": entry.get("target_area_hint"),
            "target_area_pyeong": entry.get("target_area_pyeong"),
            "target_area_m2": entry.get("target_area_m2"),
            "aggregation_policy": entry.get("aggregation_policy"),
            "source": "standards.selected_rows",
        }
        nodes.append(node)
        register_role(role, node["id"])

    envelope_nodes = [node for node in nodes if node.get("type") == "room_envelope"]
    space_region_nodes = [node for node in nodes if node.get("type") == "space_region"]
    program_cluster_nodes = build_program_clusters(space_region_nodes)
    for cluster in program_cluster_nodes:
        nodes.append(cluster)
        register_role(cluster.get("role"), cluster["id"])
    label_nodes = [node for node in nodes if node.get("type") == "program_label"]
    column_nodes = [node for node in nodes if node.get("type") == "structural_column"]
    wall_nodes = [node for node in nodes if node.get("type") == "wall_candidate"]
    standard_nodes = [node for node in nodes if node.get("type") == "standard_program"]
    constraint_nodes = [node for node in nodes if node.get("type") == "constraint"]
    wall_by_source_id = {node.get("source_id"): node for node in wall_nodes if node.get("source_id")}

    for node in label_nodes:
        point_raw = node.get("point")
        if not point_raw:
            continue
        point = (float(point_raw[0]), float(point_raw[1]))
        envelope = containing_envelope(point, envelope_nodes)
        if envelope:
            add_topology_edge(edges, node["id"], envelope["id"], "label_inside_envelope", evidence="recognized_label_point")
            continue
        envelope, distance = nearest_envelope(point, envelope_nodes)
        if envelope:
            add_topology_edge(edges, node["id"], envelope["id"], "label_nearest_envelope", distance=distance, evidence="recognized_label_point")

    for region in space_region_nodes:
        for label in label_nodes:
            if region.get("source_node_id"):
                if region.get("source_node_id") != label.get("source_node_id"):
                    continue
                add_topology_edge(edges, label["id"], region["id"], "label_inside_space_region", evidence="wall_ray_boundary")
                break
            point_raw = label.get("point")
            if point_raw and label.get("role") == region.get("role") and point_in_bbox((float(point_raw[0]), float(point_raw[1])), region.get("bbox") or {}):
                add_topology_edge(edges, label["id"], region["id"], "label_inside_space_region", evidence="wall_ray_boundary")
                break
        for side, wall_source_id in (region.get("boundary_walls") or {}).items():
            wall = wall_by_source_id.get(wall_source_id)
            if wall:
                add_topology_edge(edges, region["id"], wall["id"], "space_region_bounded_by_wall", side=side, evidence="wall_ray_boundary")
        for cluster in program_cluster_nodes:
            if region["id"] in cluster.get("space_region_ids", []):
                add_topology_edge(edges, region["id"], cluster["id"], "space_region_member_of_program_cluster", role=cluster.get("role"))
                break

    for column in column_nodes:
        center = bbox_center(column.get("bbox") or {})
        for envelope in envelope_nodes:
            if point_in_bbox(center, envelope.get("bbox") or {}):
                add_topology_edge(edges, column["id"], envelope["id"], "column_inside_envelope", protected=True)
                break

    for standard in standard_nodes:
        role = standard.get("role")
        if not role:
            continue
        for label in label_nodes:
            if label.get("role") == role:
                add_topology_edge(edges, standard["id"], label["id"], "standard_applies_to_program", role=role)

    protected_constraint_roles = {"community_shell", "lock", "no_go", "protect"}
    for constraint in constraint_nodes:
        role = constraint.get("role")
        points = [(svg_float(point[0]), svg_float(point[1])) for point in constraint.get("points", []) if isinstance(point, list) and len(point) >= 2]
        if role in protected_constraint_roles:
            for column in nodes_touching_constraint(column_nodes, points, 240):
                add_topology_edge(edges, constraint["id"], column["id"], "constraint_protects_geometry", role=role)
        elif role in {"mutable", "projectable"}:
            for label in labels_inside_constraint(label_nodes, points, 60):
                add_topology_edge(edges, constraint["id"], label["id"], "constraint_guides_program", role=role)

    adjacency_pairs = [
        ("greenery_lounge", "hall_lobby", "main hall anchors greenery lounge"),
        ("fitness_gx", "hall_lobby", "fitness and GX connect to main hall"),
        ("golf_screen", "hall_lobby", "golf access connects to main hall"),
        ("sauna_locker_shower", "golf_screen", "wellness locker/shower cluster supports golf"),
        ("management_support", "hall_lobby", "management support remains visible from hall"),
    ]
    for left_role, right_role, rationale in adjacency_pairs:
        left_nodes = role_to_nodes.get(left_role) or []
        right_nodes = role_to_nodes.get(right_role) or []
        if left_nodes and right_nodes:
            add_topology_edge(
                edges,
                left_nodes[0],
                right_nodes[0],
                "ontology_adjacency_target",
                left_role=left_role,
                right_role=right_role,
                rationale=rationale,
                evidence="OpenCrab topology prior",
            )
        left_clusters = [node for node in program_cluster_nodes if node.get("role") == left_role]
        right_clusters = [node for node in program_cluster_nodes if node.get("role") == right_role]
        if left_clusters and right_clusters:
            add_topology_edge(
                edges,
                left_clusters[0]["id"],
                right_clusters[0]["id"],
                "ontology_cluster_adjacency_target",
                left_role=left_role,
                right_role=right_role,
                rationale=rationale,
                evidence="OpenCrab topology prior",
            )

    recognition_ok = recognition_status(recognition) == "active" or use_ir_v2
    status = "active" if recognition_ok and nodes and edges else "review_required"
    node_counts = node_type_counts(nodes)
    edge_counts = edge_type_counts(edges)
    protected_count = sum(1 for node in nodes if node.get("protected") is True or node.get("type") == "structural_column")
    return {
        "schema": "crab-archi-design-topology-manifest-v1",
        "created_at": now(),
        "project_id": project_id,
        "source_svg": project.get("source_svg"),
        "status": status,
        "coordinate_space": "source_svg_viewbox",
        "required_for_final_svg": True,
        "source_manifests": {
            "recognition_manifest": str(recognition_manifest_path(project_id, root)) if recognition else None,
            "recognition_ir_v2": str(recognition_ir_v2_path(project_id, root)) if recognition_ir else None,
            "standards_manifest": str(standards_manifest_path(project_id, root)) if standards else None,
            "evidence_manifest": str(evidence_manifest_path(project_id, root)) if evidence else None,
            "constraint_manifest": str(constraint_manifest_path(project_id, root)) if constraints else None,
        },
        "node_count": len(nodes),
        "edge_count": len(edges),
        "graph_summary": {
            "node_counts_by_type": node_counts,
            "edge_counts_by_type": edge_counts,
            "program_role_count": len(role_to_nodes),
            "protected_node_count": protected_count,
            "roles": sorted(role_to_nodes),
            "recognition_source": "recognition_ir_v2" if use_ir_v2 else "recognition_manifest",
            "recognition_ir_v2_summary": (recognition_ir or {}).get("summary", {}) if use_ir_v2 else {},
        },
        "nodes": nodes,
        "edges": edges,
        "quality_gates": {
            "recognition_manifest_active": recognition_status(recognition) == "active",
            "recognition_ir_v2_active": use_ir_v2,
            "recognition_input_active": recognition_ok,
            "has_program_or_standard_nodes": bool(label_nodes or standard_nodes),
            "has_geometry_nodes": bool(envelope_nodes or column_nodes),
            "has_topology_edges": bool(edges),
        },
    }


def command_topology_build(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    load_manifest(args.project_id, root)
    topology = build_topology_manifest(args.project_id, root)
    out = topology_manifest_path(args.project_id, root)
    write_json(out, topology)
    print(out)
    print(
        json.dumps(
            {
                "status": topology["status"],
                "node_count": topology["node_count"],
                "edge_count": topology["edge_count"],
                "node_counts_by_type": topology["graph_summary"]["node_counts_by_type"],
            },
            ensure_ascii=False,
        )
    )


def latest_recognition_audit_path(project_id: str, root: Path = DEFAULT_PROJECT_ROOT) -> Path | None:
    return latest_file(project_dir(project_id, root) / "audits", "recognition_audit_*.json")


def constraint_roles(manifest: dict[str, Any] | None) -> set[str]:
    return {str(item.get("role")) for item in (manifest or {}).get("constraint_items", []) if item.get("role")}


def topology_edge_counts(manifest: dict[str, Any] | None) -> dict[str, int]:
    summary = (manifest or {}).get("graph_summary", {})
    counts = summary.get("edge_counts_by_type")
    if isinstance(counts, dict):
        return {str(key): int(value) for key, value in counts.items()}
    return edge_type_counts((manifest or {}).get("edges", []))


def recognition_audit_blocking_issues(gates: dict[str, bool]) -> list[str]:
    messages = {
        "source_svg_parse_ok": "Source SVG must parse before architectural recognition.",
        "recognition_manifest_active": "Run recognize-svg and verify that the recognition manifest is active.",
        "program_labels_detected": "No community program labels were detected.",
        "program_labels_positioned": "Program labels need usable SVG coordinates, including transform-derived coordinates.",
        "wall_candidates_detected": "Wall candidates were not detected strongly enough for plan topology.",
        "column_candidates_detected": "Column candidates were not detected; final redesign must not proceed without column preservation evidence.",
        "community_shell_confirmed": "A user-confirmed or inferred community shell is required before mutation.",
        "mutable_zone_confirmed": "A mutable/projectable community interior zone is required before mutation.",
        "protected_zone_confirmed": "No-go/lock/protect constraints for parking, cores, ramps, or structure are required.",
        "topology_manifest_active": "Run topology-build and verify that target drawing topology is active.",
        "label_topology_edges_present": "Program labels are not connected to room envelopes or wall-bounded space regions in the topology graph.",
    }
    return [message for key, message in messages.items() if not gates.get(key)]


def build_recognition_audit(project_id: str, root: Path) -> dict[str, Any]:
    manifest = load_manifest(project_id, root)
    source_svg = Path(manifest["source_svg"]).expanduser()
    source_info = inspect_svg(source_svg)
    recognition = load_recognition_manifest(project_id, root)
    recognition_ir = load_recognition_ir_v2(project_id, root)
    topology = load_topology_manifest(project_id, root)
    constraints = load_constraint_manifest(project_id, root)
    use_ir_v2 = recognition_ir_v2_status(recognition_ir) == "active"
    geometry_summary = (recognition_ir or {}).get("summary", {}) if use_ir_v2 else (recognition or {}).get("geometry_summary", {})
    program_labels = recognition_ir_v2_program_labels(recognition_ir) if use_ir_v2 else (recognition or {}).get("program_label_candidates", [])
    positioned_program_labels = [label for label in program_labels if label_point(label) is not None]
    roles = constraint_roles(constraints)
    edge_counts = topology_edge_counts(topology)
    label_space_region_edge_count = edge_counts.get("label_inside_space_region", 0)
    label_topology_edge_count = edge_counts.get("label_inside_envelope", 0) + edge_counts.get("label_nearest_envelope", 0) + label_space_region_edge_count
    recognition_input_active = recognition_status(recognition) == "active" or use_ir_v2
    gates = {
        "source_svg_parse_ok": source_info.get("xml_parse") == "ok",
        "recognition_manifest_active": recognition_input_active,
        "program_labels_detected": len(program_labels) > 0,
        "program_labels_positioned": len(program_labels) > 0 and len(positioned_program_labels) == len(program_labels),
        "wall_candidates_detected": int(geometry_summary.get("wall_candidate_count", 0) or 0) > 0,
        "column_candidates_detected": int(geometry_summary.get("column_candidate_count", 0) or 0) > 0,
        "community_shell_confirmed": "community_shell" in roles,
        "mutable_zone_confirmed": bool(roles & {"mutable", "projectable"}),
        "protected_zone_confirmed": bool(roles & {"no_go", "lock", "protect"}),
        "topology_manifest_active": topology_status(topology) == "active",
        "label_topology_edges_present": label_topology_edge_count > 0,
    }
    blocking_issues = recognition_audit_blocking_issues(gates)
    hard_gate_keys = list(gates)
    hard_gates = gates
    next_actions = [
        "Fix SVG recognition before running layout generation." if not gates["recognition_manifest_active"] else None,
        "Add or correct a community shell constraint from actual community outer walls." if not gates["community_shell_confirmed"] else None,
        "Add a tight mutable zone only inside the existing community boundary." if not gates["mutable_zone_confirmed"] else None,
        "Add no-go/lock constraints for parking, cores, ramps, and structural zones." if not gates["protected_zone_confirmed"] else None,
        "Improve column detection or attach confirmed column lock geometry." if not gates["column_candidates_detected"] else None,
        "Rebuild topology after recognition/constraint fixes." if not gates["label_topology_edges_present"] else None,
    ]
    return {
        "schema": "crab-archi-design-recognition-audit-v1",
        "created_at": now(),
        "project_id": project_id,
        "source_svg": str(source_svg),
        "status": "pass" if all(hard_gates.values()) else "review_required",
        "purpose": "Gate design generation until the target drawing is understood as an architectural SVG, not a blank zoning canvas.",
        "design_generation_policy": "allow_projection_and_svg_mutation" if all(hard_gates.values()) else "blocked_until_recognition_audit_passes",
        "gates": gates,
        "hard_gate_keys": hard_gate_keys,
        "blocking_issues": blocking_issues,
        "metrics": {
            "program_label_count": len(program_labels),
            "positioned_program_label_count": len(positioned_program_labels),
            "wall_candidate_count": int(geometry_summary.get("wall_candidate_count", 0) or 0),
            "column_candidate_count": int(geometry_summary.get("column_candidate_count", 0) or 0),
            "room_envelope_candidate_count": int(geometry_summary.get("room_envelope_candidate_count", 0) or 0),
            "recognition_source": "recognition_ir_v2" if use_ir_v2 else "recognition_manifest",
            "recognition_ir_v2_active": use_ir_v2,
            "constraint_roles": sorted(roles),
            "topology_node_count": (topology or {}).get("node_count", 0),
            "topology_edge_count": (topology or {}).get("edge_count", 0),
            "label_topology_edge_count": label_topology_edge_count,
            "label_space_region_edge_count": label_space_region_edge_count,
        },
        "next_actions": [action for action in next_actions if action],
    }


def command_recognition_audit(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    load_manifest(args.project_id, root)
    audit = build_recognition_audit(args.project_id, root)
    out_dir = project_dir(args.project_id, root) / "audits"
    seq = next_sequence(out_dir, "recognition_audit_*.json")
    out = out_dir / f"recognition_audit_{seq:03d}.json"
    write_json(out, audit)
    print(out)
    print(
        json.dumps(
            {
                "status": audit["status"],
                "design_generation_policy": audit["design_generation_policy"],
                "blocking_issue_count": len(audit["blocking_issues"]),
                "failed_gates": [key for key in audit.get("hard_gate_keys", audit["gates"].keys()) if not audit["gates"].get(key)],
                "metrics": audit["metrics"],
            },
            ensure_ascii=False,
        )
    )


def constraint_polygons(manifest: dict[str, Any] | None, roles: set[str]) -> list[dict[str, Any]]:
    polygons = []
    for item in (manifest or {}).get("constraint_items", []):
        role = item.get("role")
        if role not in roles:
            continue
        points: list[tuple[float, float]] = []
        for point in item.get("points", []):
            if isinstance(point, list) and len(point) >= 2:
                points.append((svg_float(point[0]), svg_float(point[1])))
        if len(points) >= 3:
            polygons.append(
                {
                    "id": item.get("id"),
                    "role": role,
                    "stroke_id": item.get("stroke_id"),
                    "target_hint": item.get("target_hint"),
                    "points": points,
                }
            )
    return polygons


def candidate_patch_record(item: dict[str, Any], classification: str, reason: str) -> dict[str, Any]:
    box = item.get("bbox") or {}
    record = {
        "element_index": item.get("index"),
        "source_document_index": item.get("source_document_index", item.get("index")),
        "recognition_node_id": item.get("id") if item.get("source") == "recognition_ir_v2.nodes" else None,
        "recognition_source": item.get("source"),
        "tag": item.get("tag"),
        "id": item.get("source_id") or item.get("id"),
        "class": item.get("class"),
        "role_hint": item.get("role_hint"),
        "bbox": box,
        "center": list(bbox_center(box)) if box else None,
        "classification": classification,
        "reason": reason,
        "addressing": "source_svg_element_index",
        "mutation_policy": "modify_or_remove_existing_element_only" if classification == "mutable_candidate" else "lock_existing_element",
    }
    if item.get("program_cluster_id"):
        record["program_cluster_id"] = item.get("program_cluster_id")
        record["program_role"] = item.get("program_role")
        record["space_region_ids"] = item.get("space_region_ids", [])
    return record


def topology_nodes_of_type(manifest: dict[str, Any] | None, node_type: str) -> list[dict[str, Any]]:
    return [node for node in (manifest or {}).get("nodes", []) if node.get("type") == node_type]


def candidate_program_cluster(box: dict[str, float], clusters: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [cluster for cluster in clusters if bbox_intersects(box, normalize_bbox_dict(cluster.get("bbox")), pad=12.0)]
    if not matches:
        return None
    return sorted(matches, key=lambda cluster: bbox_area(normalize_bbox_dict(cluster.get("bbox"))))[0]


def program_anchors_from_topology(topology: dict[str, Any] | None, mutable_polygons: list[dict[str, Any]], shell_polygons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not topology:
        return []
    nodes = {node["id"]: node for node in topology.get("nodes", []) if node.get("id")}
    space_region_to_cluster: dict[str, dict[str, Any]] = {}
    for edge in topology.get("edges", []):
        if edge.get("type") != "space_region_member_of_program_cluster":
            continue
        region = nodes.get(edge.get("source"))
        cluster = nodes.get(edge.get("target"))
        if region and cluster:
            space_region_to_cluster[region["id"]] = cluster
    anchors: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for edge in topology.get("edges", []):
        if edge.get("type") != "label_inside_space_region":
            continue
        label = nodes.get(edge.get("source"))
        region = nodes.get(edge.get("target"))
        if not label or not region or label["id"] in seen_labels:
            continue
        point_raw = label.get("point")
        if not point_raw:
            continue
        point = (float(point_raw[0]), float(point_raw[1]))
        in_mutable = any(point_in_polygon(point, polygon["points"]) for polygon in mutable_polygons)
        in_shell = any(point_in_polygon(point, polygon["points"]) for polygon in shell_polygons) if shell_polygons else True
        if not (in_mutable and in_shell):
            continue
        cluster = space_region_to_cluster.get(region["id"])
        anchors.append(
            {
                "text": label.get("label"),
                "role_hint": label.get("role"),
                "point": list(point),
                "space_region_id": region.get("id"),
                "space_region_bbox": region.get("bbox"),
                "program_cluster_id": cluster.get("id") if cluster else None,
                "program_cluster_bbox": cluster.get("bbox") if cluster else None,
                "position_source": "topology.label_inside_space_region",
                "use": "anchor_same_layer_patch_to_existing_wall_bounded_space",
            }
        )
        seen_labels.add(label["id"])
    return anchors


def build_svg_patch_plan(project_id: str, root: Path, max_candidates: int = 240) -> dict[str, Any]:
    project = load_manifest(project_id, root)
    recognition = load_recognition_manifest(project_id, root)
    recognition_ir = load_recognition_ir_v2(project_id, root)
    topology = load_topology_manifest(project_id, root)
    constraints = load_constraint_manifest(project_id, root)
    standards = load_standards_manifest(project_id, root)
    scale_manifest = load_scale_manifest(project_id, root)
    recognition_audit_path = latest_recognition_audit_path(project_id, root)
    recognition_audit = read_json(recognition_audit_path) if recognition_audit_path else None
    use_ir_v2 = recognition_ir_v2_status(recognition_ir) == "active"
    geometry = recognition_ir_v2_geometry_candidates(recognition_ir, max(max_candidates * 5, 1200)) if use_ir_v2 else (recognition or {}).get("geometry_candidates", {})
    geometry_source = "recognition_ir_v2" if use_ir_v2 else "recognition_manifest"
    mutable_polygons = constraint_polygons(constraints, {"mutable", "projectable"})
    shell_polygons = constraint_polygons(constraints, {"community_shell"})
    protected_polygons = constraint_polygons(constraints, {"no_go", "lock", "protect"})
    program_clusters = topology_nodes_of_type(topology, "program_cluster")
    solver_objective = evaluate_topology_fit(topology, standards, constraints, recognition_ir, scale_manifest)
    mutable_candidates: list[dict[str, Any]] = []
    locked_candidates: list[dict[str, Any]] = []

    geometry_items = [
        *geometry.get("wall_candidates", []),
        *geometry.get("room_envelope_candidates", []),
        *geometry.get("column_candidates", []),
    ]
    seen: set[tuple[Any, Any]] = set()
    for item in geometry_items:
        if item.get("editable") is False or item.get("index") is None:
            continue
        key = (item.get("index"), item.get("tag"))
        if key in seen:
            continue
        seen.add(key)
        box = item.get("bbox") or {}
        if not box:
            continue
        role_hint = item.get("role_hint")
        in_mutable = any(bbox_touches_polygon(box, polygon["points"]) for polygon in mutable_polygons)
        in_shell = any(bbox_touches_polygon(box, polygon["points"]) for polygon in shell_polygons) if shell_polygons else True
        in_protected = any(bbox_touches_polygon(box, polygon["points"]) for polygon in protected_polygons)
        if role_hint == "column_candidate":
            locked_candidates.append(candidate_patch_record(item, "locked_candidate", "recognized column candidate"))
            continue
        if in_protected:
            locked_candidates.append(candidate_patch_record(item, "locked_candidate", "inside protected no-go/lock/protect polygon"))
            continue
        if in_mutable and in_shell and role_hint in {"wall_candidate", "room_envelope_candidate"}:
            cluster = candidate_program_cluster(box, program_clusters)
            item_for_record = dict(item)
            reason = "existing SVG element intersects mutable community zone"
            if cluster:
                item_for_record["program_cluster_id"] = cluster.get("id")
                item_for_record["program_role"] = cluster.get("role")
                item_for_record["space_region_ids"] = cluster.get("space_region_ids", [])
                reason = "existing SVG element intersects wall-bounded program cluster and mutable community zone"
            record = candidate_patch_record(item_for_record, "mutable_candidate", reason)
            record["patch_priority"] = round(
                (1000.0 if cluster else 0.0)
                + patch_role_priority(cluster.get("role") if cluster else None)
                + max(float(box.get("width", 0.0)), float(box.get("height", 0.0)))
                + min(250.0, bbox_area(box) / 50.0),
                3,
            )
            mutable_candidates.append(record)

    mutable_candidates = annotate_candidates_with_solver_plan(mutable_candidates, solver_objective)
    mutable_candidates = sorted(mutable_candidates, key=lambda item: (float(item.get("patch_priority") or 0.0), bbox_area(item.get("bbox") or {})), reverse=True)
    opening_candidates = build_opening_candidates(mutable_candidates, topology, min(max_candidates, 24))
    endpoint_move_candidates = build_endpoint_move_candidates(mutable_candidates, topology, min(max_candidates, 24))

    program_anchors = program_anchors_from_topology(topology, mutable_polygons, shell_polygons)
    if not program_anchors:
        for item in (recognition or {}).get("program_label_candidates", []):
            point = label_point(item)
            if not point:
                continue
            in_mutable = any(point_in_polygon(point, polygon["points"]) for polygon in mutable_polygons)
            in_shell = any(point_in_polygon(point, polygon["points"]) for polygon in shell_polygons) if shell_polygons else True
            if in_mutable and in_shell:
                program_anchors.append(
                    {
                        "text": item.get("text"),
                        "role_hint": item.get("role_hint"),
                        "point": list(point),
                        "position_source": item.get("position_source"),
                        "use": "anchor_same_layer_patch_to_existing_room_label",
                    }
                )

    gates = {
        "recognition_manifest_active": recognition_status(recognition) == "active" or use_ir_v2,
        "topology_manifest_active": topology_status(topology) == "active",
        "recognition_audit_pass": recognition_audit is not None and recognition_audit.get("status") == "pass",
        "mutable_zone_found": bool(mutable_polygons),
        "protected_zone_found": bool(protected_polygons),
        "same_layer_mutable_candidates_found": bool(mutable_candidates),
        "program_anchors_found": bool(program_anchors),
        "overlay_generation_disallowed": True,
    }
    return {
        "schema": "crab-archi-design-svg-patch-plan-v1",
        "created_at": now(),
        "project_id": project_id,
        "source_svg": project.get("source_svg"),
        "recognition_source": geometry_source,
        "status": "pass" if all(gates.values()) else "review_required",
        "mutation_strategy": "same_layer_element_patch",
        "non_goal": "Do not create a new zoning overlay layer or mask the existing plan as the final design.",
        "required_before_svg_mutation": True,
        "gates": gates,
        "blocking_issues": [
            "Recognition audit must pass before same-layer SVG mutation." if not gates["recognition_audit_pass"] else None,
            "Mutable candidates were not found inside the confirmed mutable zone." if not gates["same_layer_mutable_candidates_found"] else None,
            "Program label anchors were not found inside the mutable community zone." if not gates["program_anchors_found"] else None,
            "Protected no-go/lock/protect zones must be confirmed before editing existing SVG elements." if not gates["protected_zone_found"] else None,
        ],
        "constraints_used": {
            "mutable_polygon_count": len(mutable_polygons),
            "community_shell_polygon_count": len(shell_polygons),
            "protected_polygon_count": len(protected_polygons),
        },
        "solver_objective": solver_objective,
        "solver_plan_projection": {
            "source": "solver.local_search",
            "status": solver_objective.get("local_search", {}).get("status"),
            "final_program_order": solver_objective.get("local_search", {}).get("final_program_order", []),
            "satisfied_adjacency_count": solver_objective.get("local_search", {}).get("satisfied_adjacency_count"),
            "use": "rank_same_layer_svg_mutation_candidates_before_native_geometry_patch",
        },
        "program_anchors": program_anchors[:max_candidates],
        "same_layer_mutable_candidates": mutable_candidates[:max_candidates],
        "same_layer_opening_candidates": opening_candidates[:max_candidates],
        "same_layer_endpoint_move_candidates": endpoint_move_candidates[:max_candidates],
        "locked_candidates": locked_candidates[:max_candidates],
        "operation_templates": [
            {
                "operation": "delete_or_trim_internal_partition",
                "target": "same_layer_mutable_candidates",
                "rule": "Only remove or trim existing wall/partition elements whose bbox intersects mutable zone and avoids protected zones.",
            },
            {
                "operation": "move_or_extend_existing_wall_segment",
                "target": "same_layer_endpoint_move_candidates",
                "rule": "Move endpoints of existing line segments by explicit x/y or dx/dy values; do not create a full new zoning block.",
            },
            {
                "operation": "split_line_for_opening",
                "target": "same_layer_opening_candidates",
                "rule": "Split an existing same-layer wall line into before/after segments to create a door opening without drawing an overlay.",
            },
            {
                "operation": "retarget_room_label_anchor",
                "target": "program_anchors",
                "rule": "Use existing program label positions to keep edits tied to the original drawing topology.",
            },
            {
                "operation": "prioritize_program_cluster_boundary_edits",
                "target": "program_clusters",
                "rule": "Use wall-bounded space clusters to select existing SVG elements before any same-layer mutation.",
            },
        ],
        "metrics": {
            "mutable_candidate_count": len(mutable_candidates),
            "opening_candidate_count": len(opening_candidates),
            "endpoint_move_candidate_count": len(endpoint_move_candidates),
            "program_cluster_candidate_count": sum(1 for item in mutable_candidates if item.get("program_cluster_id")),
            "program_cluster_count": len(program_clusters),
            "locked_candidate_count": len(locked_candidates),
            "program_anchor_count": len(program_anchors),
            "source_geometry_candidate_count": len(geometry_items),
            "editable_source_geometry_candidate_count": sum(1 for item in geometry_items if item.get("editable") is not False and item.get("index") is not None),
            "noneditable_source_geometry_candidate_count": sum(1 for item in geometry_items if item.get("editable") is False or item.get("index") is None),
            "target_program_count": solver_objective.get("target_program_count", 0),
            "covered_target_role_count": solver_objective.get("covered_target_role_count", 0),
            "feasible_available_area_estimate": solver_objective.get("feasible_report", {}).get("available_area_estimate"),
            "initial_placement_status": solver_objective.get("initial_placement", {}).get("status"),
            "initial_placement_program_count": solver_objective.get("initial_placement", {}).get("program_count"),
            "local_search_status": solver_objective.get("local_search", {}).get("status"),
            "local_search_final_score": solver_objective.get("local_search", {}).get("final_score"),
            "local_search_satisfied_adjacency_count": solver_objective.get("local_search", {}).get("satisfied_adjacency_count"),
            "local_search_ranked_mutable_candidate_count": sum(1 for item in mutable_candidates if item.get("local_search_rank") is not None),
            "scale_calibration_status": solver_objective.get("scale_calibration", {}).get("status"),
            "scale_calibration_confidence": solver_objective.get("scale_calibration", {}).get("confidence"),
        },
        "program_clusters": program_clusters[:max_candidates],
    }


def command_svg_patch_plan(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    load_manifest(args.project_id, root)
    plan = build_svg_patch_plan(args.project_id, root, args.max_candidates)
    plan["blocking_issues"] = [issue for issue in plan["blocking_issues"] if issue]
    out_dir = project_dir(args.project_id, root) / "patch_plans"
    seq = next_sequence(out_dir, "svg_patch_plan_*.json")
    out = out_dir / f"svg_patch_plan_{seq:03d}.json"
    write_json(out, plan)
    print(out)
    print(
        json.dumps(
            {
                "status": plan["status"],
                "mutation_strategy": plan["mutation_strategy"],
                "blocking_issue_count": len(plan["blocking_issues"]),
                "metrics": plan["metrics"],
                "gates": plan["gates"],
            },
            ensure_ascii=False,
        )
    )


def sorted_json_files(path: Path, pattern: str) -> list[Path]:
    return sorted(path.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name))


def latest_file(path: Path, pattern: str) -> Path | None:
    candidates = sorted(path.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name))
    return candidates[-1] if candidates else None


def resolve_intent_paths(base: Path, selector: str) -> list[Path]:
    intent_dir = base / "edit_intents"
    if selector == "latest":
        candidates = sorted_json_files(intent_dir, "*.json")
        if not candidates:
            raise SystemExit(f"No edit intents found in {intent_dir}")
        return [candidates[-1]]
    if selector == "all":
        candidates = sorted_json_files(intent_dir, "*.json")
        if not candidates:
            raise SystemExit(f"No edit intents found in {intent_dir}")
        return candidates

    path = Path(selector).expanduser()
    if not path.is_absolute():
        path = base / selector
    if not path.exists():
        raise SystemExit(f"Missing intent file: {path}")
    return [path]


def next_sequence(path: Path, pattern: str) -> int:
    return len(list(path.glob(pattern))) + 1


def infer_engine_cwd(adapter: Path, fallback: Path) -> Path:
    if adapter.exists() and adapter.parent.name == "scripts":
        return adapter.parent.parent
    if adapter.exists() and adapter.is_file():
        return adapter.parent
    return fallback


def build_engine_command(adapter: str, extra_args: list[str], allow_custom: bool = False) -> tuple[list[str], Path | None]:
    policy = require_engine_adapter_allowed(adapter, allow_custom)
    if policy["adapter_type"] == "builtin":
        adapter_path = Path(__file__).resolve().parent / str(policy["script"])
        return [sys.executable, str(adapter_path), *extra_args], adapter_path
    adapter_path = Path(adapter).expanduser()
    if adapter_path.exists():
        if adapter_path.suffix == ".py":
            return [sys.executable, str(adapter_path), *extra_args], adapter_path
        return [str(adapter_path), *extra_args], adapter_path
    return [*shlex.split(adapter), *extra_args], None


def find_existing_paths_in_text(text: str) -> list[Path]:
    paths: list[Path] = []
    for match in re.findall(r"/[^\s'\"<>]+?\.(?:svg|json|png|jpg|jpeg|md)", text):
        path = Path(match)
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def existing_artifact_path(value: str) -> Path | None:
    if "\n" in value or "\r" in value or len(value) > 500:
        return None
    if not value.startswith("/") and not value.startswith("~"):
        return None
    path = Path(value).expanduser()
    try:
        return path if path.exists() else None
    except OSError:
        return None


def iter_json_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            values.extend(iter_json_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(iter_json_values(item))
    elif isinstance(value, str):
        values.append(value)
    return values


def discover_engine_outputs(stdout: str, stderr: str, explicit_paths: list[str]) -> dict[str, list[Path]]:
    discovered = {"svg": [], "report": [], "preview": []}

    for item in explicit_paths:
        if not item:
            continue
        path = Path(item).expanduser()
        if path.exists():
            suffix = path.suffix.lower()
            if suffix == ".svg":
                discovered["svg"].append(path)
            elif suffix == ".json" or suffix == ".md":
                discovered["report"].append(path)
            elif suffix in {".png", ".jpg", ".jpeg"}:
                discovered["preview"].append(path)

    text_paths = find_existing_paths_in_text(stdout + "\n" + stderr)
    for path in text_paths:
        suffix = path.suffix.lower()
        if suffix == ".svg" and path not in discovered["svg"]:
            discovered["svg"].append(path)
        elif suffix in {".json", ".md"} and path not in discovered["report"]:
            discovered["report"].append(path)
        elif suffix in {".png", ".jpg", ".jpeg"} and path not in discovered["preview"]:
            discovered["preview"].append(path)

    for report in list(discovered["report"]):
        if report.suffix.lower() != ".json":
            continue
        try:
            data = read_json(report)
        except (OSError, json.JSONDecodeError):
            continue
        for value in iter_json_values(data):
            path = existing_artifact_path(value)
            if path is None:
                continue
            suffix = path.suffix.lower()
            if suffix == ".svg" and path not in discovered["svg"]:
                discovered["svg"].append(path)
            elif suffix in {".png", ".jpg", ".jpeg"} and path not in discovered["preview"]:
                discovered["preview"].append(path)
            elif suffix in {".json", ".md"} and path not in discovered["report"]:
                discovered["report"].append(path)
    return discovered


def local_tag(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def inspect_svg(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except OSError as exc:
        return {"xml_parse": "missing", "error": str(exc), "image_elements": None}
    except ET.ParseError as exc:
        return {"xml_parse": "error", "error": str(exc), "image_elements": None}
    return {
        "xml_parse": "ok",
        "viewBox": root.attrib.get("viewBox"),
        "image_elements": sum(1 for el in root.iter() if local_tag(el) == "image"),
        "text_elements": sum(1 for el in root.iter() if local_tag(el) == "text"),
    }


def parse_viewbox(raw: Any) -> list[float] | None:
    if isinstance(raw, list) and len(raw) == 4:
        try:
            values = [float(item) for item in raw]
        except (TypeError, ValueError):
            return None
        return values if values[2] > 0 and values[3] > 0 else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        values = [float(item) for item in re.split(r"[,\s]+", raw.strip()) if item]
    except ValueError:
        return None
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        return None
    return values


def point_inside_viewbox(point: Any, viewbox: list[float] | None) -> bool:
    if viewbox is None:
        return True
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return False
    try:
        x = float(point[0])
        y = float(point[1])
    except (TypeError, ValueError):
        return False
    min_x, min_y, width, height = viewbox
    return min_x <= x <= min_x + width and min_y <= y <= min_y + height


def load_sketch_from_intent(intent: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None]:
    if intent.get("source", {}).get("type") != "vector_sketch_layer":
        return None, None
    raw_path = intent.get("source", {}).get("path")
    if not raw_path:
        return None, None
    path = Path(raw_path).expanduser()
    if not path.exists():
        return path, None
    return path, read_json(path)


def analyze_sketch(sketch: dict[str, Any] | None, fallback_viewbox: list[float] | None) -> dict[str, Any]:
    if sketch is None:
        return {
            "source_available": False,
            "stroke_count": 0,
            "point_count": 0,
            "out_of_bounds_points": 0,
            "viewBox": fallback_viewbox,
            "modes": [],
            "target_hints": [],
        }
    viewbox = fallback_viewbox or parse_viewbox(sketch.get("viewBox"))
    stroke_count = 0
    point_count = 0
    out_of_bounds = 0
    modes: set[str] = set()
    target_hints: set[str] = set()
    for stroke in sketch.get("strokes", []):
        stroke_count += 1
        mode = stroke.get("mode") or stroke.get("tool")
        if mode:
            modes.add(str(mode))
        target_hint = stroke.get("target_hint")
        if target_hint:
            target_hints.add(str(target_hint))
        for point in stroke.get("points", []):
            point_count += 1
            if not point_inside_viewbox(point, viewbox):
                out_of_bounds += 1
    return {
        "source_available": True,
        "stroke_count": stroke_count,
        "point_count": point_count,
        "out_of_bounds_points": out_of_bounds,
        "viewBox": viewbox,
        "modes": sorted(modes),
        "target_hints": sorted(target_hints),
    }


def flatten_operations(intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for intent in intents:
        for op in intent.get("operations", []):
            rows.append(
                {
                    "schema": intent.get("schema"),
                    "source": intent.get("source", {}).get("type"),
                    "target": op.get("target") or op.get("target_hint"),
                    "action": op.get("action") or op.get("edit_type"),
                    "method": op.get("method") or op.get("snap_policy"),
                    "stroke_id": op.get("stroke_id"),
                }
            )
    return rows


def build_edit_brief_markdown(brief: dict[str, Any]) -> str:
    lines = [
        f"# {brief['project_id']} Edit Brief",
        "",
        f"- Status: `{brief['status']}`",
        f"- Created: `{brief['created_at']}`",
        f"- Intent count: `{brief['intent_count']}`",
        f"- Evidence status: `{brief['evidence_status']}`",
        f"- Constraint status: `{brief['constraint_status']}`",
        f"- Standards status: `{brief['standards_status']}`",
        f"- Recognition status: `{brief['recognition_status']}`",
        f"- Topology status: `{brief['topology_status']}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in brief["checks"].items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    lines.extend(["", "## Operations", ""])
    if brief["operations"]:
        for op in brief["operations"]:
            label = op.get("target") or op.get("action") or "operation"
            detail = ", ".join(str(item) for item in [op.get("action"), op.get("method"), op.get("stroke_id")] if item)
            lines.append(f"- `{label}`: {detail or 'review'}")
    else:
        lines.append("- No operations.")
    lines.extend(["", "## Sketch Analysis", ""])
    if brief["sketch_analysis"]:
        for item in brief["sketch_analysis"]:
            lines.append(
                f"- `{item['path']}`: strokes `{item['stroke_count']}`, points `{item['point_count']}`, "
                f"out_of_bounds `{item['out_of_bounds_points']}`, modes `{', '.join(item['modes']) or '-'}"
            )
    else:
        lines.append("- No sketch intent attached.")
    lines.extend(["", "## Constraints", ""])
    if brief["constraint_summary"]["items"]:
        for item in brief["constraint_summary"]["items"]:
            lines.append(
                f"- `{item['role']}`: {item.get('target_hint') or item.get('mode') or item['id']} "
                f"(points `{item['point_count']}`, policy `{item['solver_policy']}`)"
            )
    else:
        lines.append("- No constraint manifest attached.")
    lines.extend(["", "## Standards", ""])
    if brief["standards_summary"]["items"]:
        for item in brief["standards_summary"]["items"]:
            lines.append(
                f"- `{item['file_type']}`: {item['source_file']} "
                f"(households `{item['household_count']}`, rows `{item.get('selected_row_count', '-')}`)"
            )
    else:
        lines.append("- No standards manifest attached.")
    lines.extend(["", "## Recognition", ""])
    if brief["recognition_summary"]["status"] != "missing":
        summary = brief["recognition_summary"]
        lines.append(f"- Status: `{summary['status']}`")
        lines.append(f"- Primitives: `{summary['primitive_count']}`")
        lines.append(f"- Labels: `{summary['label_count']}`")
        lines.append(f"- Program labels: `{summary['program_label_count']}`")
    else:
        lines.append("- No recognition manifest attached.")
    lines.extend(["", "## Topology", ""])
    if brief["topology_summary"]["status"] != "missing":
        summary = brief["topology_summary"]
        lines.append(f"- Status: `{summary['status']}`")
        lines.append(f"- Nodes: `{summary['node_count']}`")
        lines.append(f"- Edges: `{summary['edge_count']}`")
        lines.append(f"- Roles: `{', '.join(summary.get('roles', [])) or '-'}`")
    else:
        lines.append("- No topology manifest attached.")
    lines.extend(["", "## Next Solver Instruction", "", brief["next_solver_instruction"], ""])
    return "\n".join(lines)


def command_edit_brief(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    intent_paths = resolve_intent_paths(base, args.intent)
    intents = [read_json(path) for path in intent_paths]
    source_info = inspect_svg(Path(manifest["source_svg"]).expanduser())
    fallback_viewbox = parse_viewbox(source_info.get("viewBox"))
    sketch_analysis = []

    for intent in intents:
        sketch_path, sketch = load_sketch_from_intent(intent)
        if sketch_path is None:
            continue
        analysis = analyze_sketch(sketch, fallback_viewbox)
        analysis["path"] = str(sketch_path)
        sketch_analysis.append(analysis)

    evidence_manifest = load_evidence_manifest(args.project_id, root)
    constraint_manifest = load_constraint_manifest(args.project_id, root)
    standards_manifest = load_standards_manifest(args.project_id, root)
    recognition_manifest = load_recognition_manifest(args.project_id, root)
    topology_manifest = load_topology_manifest(args.project_id, root)
    checks = {
        "source_svg_parse_ok": source_info.get("xml_parse") == "ok",
        "recognition_manifest_active": recognition_status(recognition_manifest) == "active",
        "topology_manifest_active": topology_status(topology_manifest) == "active",
        "opencrab_evidence_verified": evidence_status(evidence_manifest) == "verified",
        "constraint_manifest_active": constraint_status(constraint_manifest) == "active",
        "standards_manifest_active": standards_status(standards_manifest) == "active",
        "intent_count_positive": len(intent_paths) > 0,
        "sketch_sources_available": all(item["source_available"] for item in sketch_analysis),
        "sketch_points_inside_viewbox": all(item["out_of_bounds_points"] == 0 for item in sketch_analysis),
    }
    operations = flatten_operations(intents)
    brief = {
        "schema": "crab-archi-design-edit-brief-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "status": "pass" if all(checks.values()) else "review_required",
        "manifest": {
            "source_svg": manifest.get("source_svg"),
            "household_count": manifest.get("household_count"),
            "ontology_pack": manifest.get("ontology_pack"),
        },
        "intent_paths": [str(path) for path in intent_paths],
        "intent_count": len(intent_paths),
        "operations": operations,
        "source_svg_info": source_info,
        "evidence_status": evidence_status(evidence_manifest),
        "evidence_count": count_evidence_items(evidence_manifest),
        "recognition_status": recognition_status(recognition_manifest),
        "topology_status": topology_status(topology_manifest),
        "constraint_status": constraint_status(constraint_manifest),
        "constraint_count": count_constraint_items(constraint_manifest),
        "enforced_constraint_count": count_constraint_items(constraint_manifest, enforced_only=True),
        "standards_status": standards_status(standards_manifest),
        "standard_count": count_standard_items(standards_manifest),
        "constraint_summary": {
            "items": [
                {
                    "id": item.get("id"),
                    "role": item.get("role"),
                    "mode": item.get("mode"),
                    "target_hint": item.get("target_hint"),
                    "point_count": item.get("point_count"),
                    "solver_policy": item.get("solver_policy"),
                }
                for item in (constraint_manifest or {}).get("constraint_items", [])
            ]
        },
        "standards_summary": {
            "items": [
                {
                    "id": item.get("id"),
                    "source_file": item.get("source_file"),
                    "file_type": item.get("file_type"),
                    "parser": item.get("parser"),
                    "household_count": item.get("household_count"),
                    "selected_row_count": item.get("payload", {}).get("selected_row_count"),
                    "matched_household_row_count": item.get("payload", {}).get("matched_household_row_count"),
                }
                for item in (standards_manifest or {}).get("standard_items", [])
            ]
        },
        "recognition_summary": {
            "status": recognition_status(recognition_manifest),
            "primitive_count": (recognition_manifest or {}).get("primitive_count", 0),
            "label_count": (recognition_manifest or {}).get("label_count", 0),
            "program_label_count": (recognition_manifest or {}).get("program_label_count", 0),
            "source_svg_info": (recognition_manifest or {}).get("source_svg_info"),
        },
        "topology_summary": {
            "status": topology_status(topology_manifest),
            "node_count": (topology_manifest or {}).get("node_count", 0),
            "edge_count": (topology_manifest or {}).get("edge_count", 0),
            "roles": (topology_manifest or {}).get("graph_summary", {}).get("roles", []),
            "graph_summary": (topology_manifest or {}).get("graph_summary", {}),
        },
        "checks": checks,
        "sketch_analysis": sketch_analysis,
        "hard_constraints": manifest.get("hard_constraints", []),
        "next_solver_instruction": (
            "Apply only native SVG edits inside mutable community zones. Preserve parking count, columns, cores, "
            "stairs, ramps, egress, and the community outer shell. Follow attached household standards, recognition manifest, and topology manifest. "
            "Treat doodle strokes as intent anchors, not final geometry."
        ),
    }
    briefs_dir = base / "briefs"
    seq = next_sequence(briefs_dir, "edit_brief_*.json")
    json_path = briefs_dir / f"edit_brief_{seq:03d}.json"
    md_path = briefs_dir / f"edit_brief_{seq:03d}.md"
    write_json(json_path, brief)
    md_path.write_text(build_edit_brief_markdown(brief), encoding="utf-8")
    print(json_path)
    print(md_path)
    print(json.dumps({"status": brief["status"], "checks": checks}, ensure_ascii=False))


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def resolve_project_path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute() or path.exists():
        return path
    return base / value


def resolve_apply_report(base: Path, selector: str) -> Path:
    if selector == "latest":
        report = latest_file(base / "runs", "apply_edit_*/apply_edit_report.json")
        if report is None:
            raise SystemExit(f"No apply-edit report found under {base / 'runs'}")
        return report
    path = resolve_project_path(base, selector)
    if path is None or not path.exists():
        raise SystemExit(f"Missing apply-edit report: {selector}")
    return path


def resolve_alternative_svg(base: Path, selector: str, apply_report: dict[str, Any]) -> Path:
    if selector == "from-report":
        items = apply_report.get("copied_artifacts", {}).get("svg", [])
        if items:
            path = resolve_project_path(base, items[0])
            if path is not None and path.exists():
                return path
    elif selector == "latest":
        path = latest_file(base / "alternatives", "alternative_*.svg")
        if path is not None:
            return path
    else:
        path = resolve_project_path(base, selector)
        if path is not None and path.exists():
            return path
    raise SystemExit("Missing alternative SVG")


def collect_intent_summary(intent_paths: list[str]) -> list[dict[str, Any]]:
    rows = []
    for raw_path in intent_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        data = read_json(path)
        rows.append(
            {
                "file": path.name,
                "schema": data.get("schema"),
                "source": data.get("source", {}).get("type"),
                "strategy": data.get("strategy"),
                "operations": data.get("operations", []),
            }
        )
    return rows


def summarize_checks(checks: dict[str, Any]) -> tuple[int, int]:
    total = 0
    passed = 0

    def walk(value: Any) -> None:
        nonlocal total, passed
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        else:
            total += 1
            if value is True:
                passed += 1

    walk(checks)
    return passed, total


def render_check_list(checks: dict[str, Any]) -> str:
    lines = []
    for key, value in checks.items():
        if isinstance(value, dict):
            passed, total = summarize_checks(value)
            label = f"{passed}/{total}"
            state = "pass" if passed == total else "review"
        else:
            label = "pass" if value is True else "review"
            state = label
        lines.append(f'<li class="{state}"><span>{html.escape(str(key))}</span><strong>{html.escape(label)}</strong></li>')
    return "\n".join(lines)


def render_intent_list(intents: list[dict[str, Any]]) -> str:
    rows = []
    for intent in intents:
        ops = intent.get("operations") or []
        op_labels = []
        for op in ops:
            target = op.get("target") or op.get("target_hint") or op.get("edit_type") or "operation"
            action = op.get("action") or op.get("method") or op.get("edit_type") or ""
            op_labels.append(f"{target}: {action}".strip(": "))
        rows.append(
            "<li>"
            f"<strong>{html.escape(intent.get('file') or 'intent')}</strong>"
            f"<span>{html.escape(intent.get('schema') or '')}</span>"
            f"<p>{html.escape('; '.join(op_labels) or 'No operations')}</p>"
            "</li>"
        )
    return "\n".join(rows)


def render_evidence_list(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return '<li class="review"><span>evidence_manifest</span><strong>missing</strong></li>'
    rows = [
        f'<li class="pass"><span>status</span><strong>{html.escape(evidence_status(manifest))}</strong></li>',
        f'<li class="pass"><span>evidence_count</span><strong>{count_evidence_items(manifest)}</strong></li>',
    ]
    for item in manifest.get("evidence_items", []):
        label = item.get("pack_id") or item.get("id") or "evidence"
        summary = item.get("summary") or item.get("query") or ""
        rows.append(
            "<li>"
            f"<strong>{html.escape(str(label))}</strong>"
            f"<span>{html.escape(str(item.get('source') or ''))}</span>"
            f"<p>{html.escape(str(summary))}</p>"
            "</li>"
        )
    return "\n".join(rows)


def render_constraint_list(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return '<li class="review"><span>constraint_manifest</span><strong>missing</strong></li>'
    status = constraint_status(manifest)
    status_class = "pass" if status == "active" else "review"
    rows = [
        f'<li class="{status_class}"><span>status</span><strong>{html.escape(status)}</strong></li>',
        f'<li class="{status_class}"><span>constraint_count</span><strong>{count_constraint_items(manifest)}</strong></li>',
        f'<li class="{status_class}"><span>enforced_constraint_count</span><strong>{count_constraint_items(manifest, enforced_only=True)}</strong></li>',
    ]
    for item in manifest.get("constraint_items", []):
        rows.append(
            "<li>"
            f"<strong>{html.escape(str(item.get('role') or 'constraint'))}</strong>"
            f"<span>{html.escape(str(item.get('target_hint') or item.get('mode') or item.get('id') or ''))}</span>"
            f"<p>points: {html.escape(str(item.get('point_count') or 0))}; policy: {html.escape(str(item.get('solver_policy') or ''))}</p>"
            "</li>"
        )
    return "\n".join(rows)


def render_standards_list(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return '<li class="review"><span>standards_manifest</span><strong>missing</strong></li>'
    status = standards_status(manifest)
    status_class = "pass" if status == "active" else "review"
    rows = [
        f'<li class="{status_class}"><span>status</span><strong>{html.escape(status)}</strong></li>',
        f'<li class="{status_class}"><span>standard_count</span><strong>{count_standard_items(manifest)}</strong></li>',
        f'<li class="{status_class}"><span>household_count</span><strong>{html.escape(str(manifest.get("household_count") or ""))}</strong></li>',
    ]
    for item in manifest.get("standard_items", []):
        payload = item.get("payload", {})
        row_count = payload.get("selected_row_count") or payload.get("row_count") or "-"
        rows.append(
            "<li>"
            f"<strong>{html.escape(str(item.get('file_type') or 'standard'))}</strong>"
            f"<span>{html.escape(str(item.get('source_file') or item.get('id') or ''))}</span>"
            f"<p>parser: {html.escape(str(item.get('parser') or ''))}; rows: {html.escape(str(row_count))}</p>"
            "</li>"
        )
    return "\n".join(rows)


def render_recognition_list(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return '<li class="review"><span>recognition_manifest</span><strong>missing</strong></li>'
    status = recognition_status(manifest)
    status_class = "pass" if status == "active" else "review"
    geometry = manifest.get("geometry_summary", {})
    rows = [
        f'<li class="{status_class}"><span>status</span><strong>{html.escape(status)}</strong></li>',
        f'<li class="{status_class}"><span>primitive_count</span><strong>{html.escape(str(manifest.get("primitive_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>label_count</span><strong>{html.escape(str(manifest.get("label_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>program_label_count</span><strong>{html.escape(str(manifest.get("program_label_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>column_candidates</span><strong>{html.escape(str(geometry.get("column_candidate_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>wall_candidates</span><strong>{html.escape(str(geometry.get("wall_candidate_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>room_envelope_candidates</span><strong>{html.escape(str(geometry.get("room_envelope_candidate_count", 0)))}</strong></li>',
    ]
    for item in manifest.get("program_label_candidates", [])[:12]:
        rows.append(
            "<li>"
            f"<strong>{html.escape(str(item.get('role_hint') or 'label'))}</strong>"
            f"<span>{html.escape(str(item.get('text') or ''))}</span>"
            "</li>"
        )
    return "\n".join(rows)


def render_topology_list(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return '<li class="review"><span>topology_manifest</span><strong>missing</strong></li>'
    status = topology_status(manifest)
    status_class = "pass" if status == "active" else "review"
    summary = manifest.get("graph_summary", {})
    roles = ", ".join(summary.get("roles", []))
    rows = [
        f'<li class="{status_class}"><span>status</span><strong>{html.escape(status)}</strong></li>',
        f'<li class="{status_class}"><span>node_count</span><strong>{html.escape(str(manifest.get("node_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>edge_count</span><strong>{html.escape(str(manifest.get("edge_count", 0)))}</strong></li>',
        f'<li class="{status_class}"><span>roles</span><strong>{html.escape(roles or "-")}</strong></li>',
    ]
    for edge in manifest.get("edges", [])[:8]:
        rows.append(
            "<li>"
            f"<strong>{html.escape(str(edge.get('type') or 'edge'))}</strong>"
            f"<span>{html.escape(str(edge.get('source') or ''))} -> {html.escape(str(edge.get('target') or ''))}</span>"
            "</li>"
        )
    return "\n".join(rows)


def build_review_panel_html(context: dict[str, Any]) -> str:
    source_svg = context["source_svg_markup"]
    alternative_svg = context["alternative_svg_markup"]
    apply_checks = render_check_list(context["apply_checks"])
    engine_checks = render_check_list(context["engine_quality_gates"])
    intent_list = render_intent_list(context["intents"])
    evidence_list = render_evidence_list(context["evidence_manifest"])
    constraint_list = render_constraint_list(context["constraint_manifest"])
    standards_list = render_standards_list(context["standards_manifest"])
    recognition_list = render_recognition_list(context["recognition_manifest"])
    topology_list = render_topology_list(context["topology_manifest"])
    source_info = html.escape(json.dumps(context["source_svg_info"], ensure_ascii=False, indent=2))
    alt_info = html.escape(json.dumps(context["alternative_svg_info"], ensure_ascii=False, indent=2))
    title = html.escape(context["title"])
    generated_at = html.escape(context["generated_at"])
    project_id = html.escape(context["project_id"])
    source_path = html.escape(context["source_svg_path"])
    alternative_path = html.escape(context["alternative_svg_path"])
    report_path = html.escape(context["apply_report_path"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="icon" href="data:,">
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f4;
      --panel: #fff;
      --ink: #171717;
      --muted: #687076;
      --line: #d9ded8;
      --accent: #0f766e;
      --warn: #b45309;
      --ok: #15803d;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); }}
    header {{ padding: 18px 22px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    h1 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 8px; color: var(--muted); font-size: 12px; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; padding: 16px; }}
    .comparison {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; min-width: 0; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; min-width: 0; overflow: hidden; }}
    h2 {{ margin: 0; padding: 11px 13px; font-size: 13px; border-bottom: 1px solid var(--line); }}
    .viewport {{ height: 72vh; overflow: auto; background: #fff; }}
    .viewport svg {{ display: block; width: 100%; height: auto; }}
    aside {{ display: grid; gap: 16px; align-content: start; }}
    .box {{ padding: 12px; }}
    ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 7px; }}
    li {{ border: 1px solid var(--line); border-radius: 8px; padding: 9px; }}
    li.pass strong {{ color: var(--ok); }}
    li.review strong {{ color: var(--warn); }}
    li span {{ display: block; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }}
    li p {{ margin: 6px 0 0; font-size: 12px; line-height: 1.4; color: #30343a; overflow-wrap: anywhere; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; line-height: 1.4; margin: 0; color: #30343a; }}
    .paths {{ color: var(--muted); font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }}
    @media (max-width: 1180px) {{
      main {{ grid-template-columns: 1fr; }}
      .comparison {{ grid-template-columns: 1fr; }}
      .viewport {{ height: 56vh; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="meta">
      <span>Project: {project_id}</span>
      <span>Generated: {generated_at}</span>
    </div>
  </header>
  <main>
    <div class="comparison">
      <section>
        <h2>Original</h2>
        <div class="viewport">{source_svg}</div>
      </section>
      <section>
        <h2>Alternative</h2>
        <div class="viewport">{alternative_svg}</div>
      </section>
    </div>
    <aside>
      <section>
        <h2>Apply Checks</h2>
        <div class="box"><ul>{apply_checks}</ul></div>
      </section>
      <section>
        <h2>Engine Gates</h2>
        <div class="box"><ul>{engine_checks}</ul></div>
      </section>
      <section>
        <h2>Intent</h2>
        <div class="box"><ul>{intent_list}</ul></div>
      </section>
      <section>
        <h2>Evidence</h2>
        <div class="box"><ul>{evidence_list}</ul></div>
      </section>
      <section>
        <h2>Recognition</h2>
        <div class="box"><ul>{recognition_list}</ul></div>
      </section>
      <section>
        <h2>Topology</h2>
        <div class="box"><ul>{topology_list}</ul></div>
      </section>
      <section>
        <h2>Constraints</h2>
        <div class="box"><ul>{constraint_list}</ul></div>
      </section>
      <section>
        <h2>Standards</h2>
        <div class="box"><ul>{standards_list}</ul></div>
      </section>
      <section>
        <h2>SVG Info</h2>
        <div class="box"><pre>{source_info}</pre><pre>{alt_info}</pre></div>
      </section>
      <section>
        <h2>Files</h2>
        <div class="box paths">
          <div>Source: {source_path}</div>
          <div>Alternative: {alternative_path}</div>
          <div>Apply report: {report_path}</div>
        </div>
      </section>
    </aside>
  </main>
</body>
</html>
"""


def command_review_panel(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    apply_report_path = resolve_apply_report(base, args.apply_report)
    apply_report = read_json(apply_report_path)
    source_svg = Path(manifest["source_svg"]).expanduser()
    alternative_svg = resolve_alternative_svg(base, args.alternative, apply_report)
    source_info = inspect_svg(source_svg)
    alternative_info = inspect_svg(alternative_svg)

    engine_quality = {}
    for report_path in apply_report.get("copied_artifacts", {}).get("report", []):
        path = resolve_project_path(base, report_path)
        if path is None or path.suffix.lower() != ".json" or not path.exists():
            continue
        try:
            data = read_json(path)
        except json.JSONDecodeError:
            continue
        quality = data.get("quality", {})
        gates = quality.get("gates")
        if isinstance(gates, dict):
            engine_quality = gates
            break

    if not engine_quality:
        engine_quality = {"engine_report_quality_gates_found": False}

    panel_dir = base / "panels"
    panel_seq = next_sequence(panel_dir, "review_panel_*.html")
    panel_path = panel_dir / f"review_panel_{panel_seq:03d}.html"
    context = {
        "title": args.title or f"{args.project_id} Review Panel",
        "generated_at": now(),
        "project_id": args.project_id,
        "source_svg_path": str(source_svg),
        "alternative_svg_path": str(alternative_svg),
        "apply_report_path": str(apply_report_path),
        "source_svg_markup": read_text_file(source_svg),
        "alternative_svg_markup": read_text_file(alternative_svg),
        "source_svg_info": source_info,
        "alternative_svg_info": alternative_info,
        "apply_checks": apply_report.get("checks", {}),
        "engine_quality_gates": engine_quality,
        "recognition_manifest": load_recognition_manifest(args.project_id, root),
        "topology_manifest": load_topology_manifest(args.project_id, root),
        "evidence_manifest": load_evidence_manifest(args.project_id, root),
        "constraint_manifest": load_constraint_manifest(args.project_id, root),
        "standards_manifest": load_standards_manifest(args.project_id, root),
        "intents": collect_intent_summary(apply_report.get("intent_paths", [])),
    }
    html_text = build_review_panel_html(context)
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    panel_path.write_text(html_text, encoding="utf-8")
    print(panel_path)
    print(panel_path.resolve().as_uri())
    if args.open:
        webbrowser.open(panel_path.resolve().as_uri())


def copy_artifact(src: Path, dest_dir: Path, prefix: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{prefix}{src.suffix}"
    shutil.copy2(src, dest)
    return dest


def same_file_or_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def select_candidate_svg(discovered_svgs: list[Path], source_svg: str | None) -> Path | None:
    source = Path(source_svg).expanduser() if source_svg else None
    candidates = [path for path in discovered_svgs if source is None or not same_file_or_path(path, source)]
    if candidates:
        return candidates[0]
    return discovered_svgs[0] if discovered_svgs else None


def summarize_engine_reports(report_paths: list[str]) -> dict[str, Any]:
    statuses = []
    gate_groups: dict[str, dict[str, bool]] = {}
    for raw_path in report_paths:
        path = Path(raw_path)
        if not path.exists() or path.suffix.lower() != ".json":
            continue
        try:
            data = read_json(path)
        except json.JSONDecodeError:
            continue
        gates = data.get("quality", {}).get("gates")
        if not isinstance(gates, dict):
            continue
        schema = str(data.get("schema") or path.stem)
        statuses.append(str(data.get("status") or "missing"))
        gate_groups[schema] = {str(key): bool(value) for key, value in gates.items()}
    gate_values = [value for gates in gate_groups.values() for value in gates.values()]
    return {
        "report_count": len(statuses),
        "statuses": statuses,
        "gates": gate_groups,
        "quality_found": bool(gate_values),
        "all_status_pass": bool(statuses) and all(status == "pass" for status in statuses),
        "all_gates_pass": bool(gate_values) and all(gate_values),
    }


def command_apply_edit(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    intent_paths = resolve_intent_paths(base, args.intent)
    intents = [read_json(path) for path in intent_paths]
    recognition_manifest = load_recognition_manifest(args.project_id, root)
    topology_manifest = load_topology_manifest(args.project_id, root)
    evidence_manifest = load_evidence_manifest(args.project_id, root)
    constraint_manifest = load_constraint_manifest(args.project_id, root)
    standards_manifest = load_standards_manifest(args.project_id, root)

    runs_dir = base / "runs"
    run_seq = next_sequence(runs_dir, "apply_edit_*")
    run_dir = runs_dir / f"apply_edit_{run_seq:03d}"
    alternatives_dir = base / "alternatives"
    run_dir.mkdir(parents=True, exist_ok=True)
    alternatives_dir.mkdir(parents=True, exist_ok=True)

    solver_input = {
        "schema": "crab-archi-design-solver-input-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "manifest": manifest,
        "intent_paths": [str(path) for path in intent_paths],
        "intents": intents,
        "hard_constraints": manifest.get("hard_constraints", []),
        "recognition_manifest": recognition_manifest,
        "topology_manifest": topology_manifest,
        "evidence_manifest": evidence_manifest,
        "constraint_manifest": constraint_manifest,
        "standards_manifest": standards_manifest,
        "opencrab_mcp": manifest.get("opencrab_mcp"),
        "solver_contract": {
            "must_preserve": manifest.get("hard_constraints", []),
            "must_output": "native SVG candidate",
            "must_qa": ["engine_returncode_zero", "candidate_svg_exists", "native_svg_no_images"],
        },
    }
    solver_input_path = run_dir / "solver_input.json"
    write_json(solver_input_path, solver_input)

    adapter = args.engine_adapter or manifest.get("engine_adapter")
    if not adapter:
        raise SystemExit("Missing engine adapter. Pass --engine-adapter or set one during init.")

    extra_args = list(args.engine_arg or [])
    if args.skip_preview and "--skip-preview" not in extra_args:
        extra_args.append("--skip-preview")
    allow_custom_engine = bool(args.allow_custom_engine or manifest.get("allow_custom_engine_adapter"))
    engine_policy = engine_adapter_policy(adapter, allow_custom_engine)
    command, adapter_path = build_engine_command(adapter, extra_args, allow_custom=allow_custom_engine)
    engine_cwd = Path(args.engine_cwd).expanduser() if args.engine_cwd else infer_engine_cwd(adapter_path or Path(adapter), Path.cwd())
    env = os.environ.copy()
    env.update(
        {
            "CRAB_ARCHI_PROJECT_ID": args.project_id,
            "CRAB_ARCHI_PROJECT_ROOT": str(root.resolve()),
            "CRAB_ARCHI_PROJECT_DIR": str(base.resolve()),
            "CRAB_ARCHI_RUN_DIR": str(run_dir.resolve()),
            "CRAB_ARCHI_SOLVER_INPUT": str(solver_input_path.resolve()),
            "CRAB_ARCHI_SOURCE_SVG": str(Path(manifest.get("source_svg", "")).expanduser().resolve()) if manifest.get("source_svg") else "",
            "CRAB_ARCHI_INTENTS": os.pathsep.join(str(path.resolve()) for path in intent_paths),
        }
    )

    started_at = now()
    proc = subprocess.run(
        command,
        cwd=engine_cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )
    engine_result = {
        "command": command,
        "cwd": str(engine_cwd),
        "started_at": started_at,
        "finished_at": now(),
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-8000:],
        "stderr_tail": proc.stderr[-8000:],
    }

    discovered = discover_engine_outputs(
        proc.stdout,
        proc.stderr,
        [args.candidate_svg or "", args.candidate_report or "", args.preview or ""],
    )

    copied: dict[str, list[str]] = {"svg": [], "report": [], "preview": []}
    alternative_svg: Path | None = None
    candidate_svg = select_candidate_svg(discovered["svg"], manifest.get("source_svg"))
    if candidate_svg:
        alternative_svg = copy_artifact(candidate_svg, alternatives_dir, f"alternative_{run_seq:03d}")
        copied["svg"].append(str(alternative_svg))
    for idx, report in enumerate(discovered["report"], start=1):
        copied["report"].append(str(copy_artifact(report, run_dir, f"engine_report_{idx:03d}")))
    for idx, preview in enumerate(discovered["preview"], start=1):
        copied["preview"].append(str(copy_artifact(preview, run_dir, f"preview_{idx:03d}")))

    svg_info = inspect_svg(alternative_svg) if alternative_svg else {"xml_parse": "missing", "image_elements": None}
    engine_report_quality = summarize_engine_reports(copied["report"])
    checks: dict[str, Any] = {
        "engine_returncode_zero": proc.returncode == 0,
        "engine_report_quality_found": engine_report_quality["quality_found"],
        "engine_report_status_pass": engine_report_quality["all_status_pass"],
        "engine_quality_gates_pass": engine_report_quality["all_gates_pass"],
        "candidate_svg_exists": alternative_svg is not None and alternative_svg.exists(),
        "native_svg_no_images": svg_info.get("xml_parse") == "ok" and svg_info.get("image_elements") == 0,
        "recognition_manifest_active": recognition_status(recognition_manifest) == "active",
        "topology_manifest_active": topology_status(topology_manifest) == "active",
        "opencrab_mcp_required": bool(manifest.get("opencrab_mcp", {}).get("required")),
        "opencrab_evidence_verified": evidence_status(evidence_manifest) == "verified",
        "constraint_manifest_active": constraint_status(constraint_manifest) == "active",
        "standards_manifest_active": standards_status(standards_manifest) == "active",
        "intent_count_positive": len(intent_paths) > 0,
    }
    candidate_quality = build_candidate_quality_report(checks, engine_report_quality)
    checks["candidate_hard_gates_pass"] = candidate_quality["hard_status"] == "pass"
    report = {
        "schema": "crab-archi-design-apply-edit-report-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "run_dir": str(run_dir),
        "solver_input": str(solver_input_path),
        "intent_paths": [str(path) for path in intent_paths],
        "engine_policy": engine_policy,
        "engine": engine_result,
        "discovered_outputs": {key: [str(path) for path in paths] for key, paths in discovered.items()},
        "copied_artifacts": copied,
        "svg_inspection": svg_info,
        "engine_report_quality": engine_report_quality,
        "candidate_quality": candidate_quality,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "review_required",
    }
    report_path = run_dir / "apply_edit_report.json"
    write_json(report_path, report)
    print(report_path)
    print(json.dumps({"status": report["status"], "alternative_svg": str(alternative_svg) if alternative_svg else None, "checks": checks}, ensure_ascii=False))


def command_qa(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    checks = {
        "manifest_exists": True,
        "source_svg_exists_or_allowed_missing": Path(manifest["source_svg"]).exists() or args.allow_missing_source,
        "recognition_manifest_active": recognition_status(load_recognition_manifest(args.project_id, root)) == "active",
        "topology_manifest_active": topology_status(load_topology_manifest(args.project_id, root)) == "active",
        "has_hard_constraints": bool(manifest.get("hard_constraints")),
        "opencrab_mcp_required": bool(manifest.get("opencrab_mcp", {}).get("required")),
        "opencrab_mcp_server_configured": bool(manifest.get("opencrab_mcp", {}).get("mcp_server")),
        "opencrab_ontology_pack_attached": bool(manifest.get("opencrab_mcp", {}).get("ontology_pack")),
        "opencrab_evidence_verified": evidence_status(load_evidence_manifest(args.project_id, root)) == "verified",
        "constraint_manifest_active": constraint_status(load_constraint_manifest(args.project_id, root)) == "active",
        "standards_manifest_active": standards_status(load_standards_manifest(args.project_id, root)) == "active",
        "has_edit_intent_dir": Path(manifest["artifacts"]["edit_intents_dir"]).exists(),
    }
    qa = {
        "schema": "crab-archi-design-project-qa-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "review_required",
    }
    out = project_dir(args.project_id, root) / "qa" / "qa_report.json"
    write_json(out, qa)
    print(out)
    print(json.dumps({"status": qa["status"], "checks": checks}, ensure_ascii=False))


def resolve_latest_alternative(base: Path, apply_report: dict[str, Any] | None) -> Path | None:
    if apply_report:
        for item in apply_report.get("copied_artifacts", {}).get("svg", []):
            path = resolve_project_path(base, item)
            if path is not None and path.exists():
                return path
    return latest_file(base / "alternatives", "alternative_*.svg")


def sum_selected_standard_rows(manifest: dict[str, Any] | None) -> int:
    if not manifest:
        return 0
    total = 0
    for item in manifest.get("standard_items", []):
        selected = item.get("payload", {}).get("selected_row_count")
        if isinstance(selected, int):
            total += selected
    return total


def build_project_status(project_id: str, root: Path) -> dict[str, Any]:
    manifest = load_manifest(project_id, root)
    base = project_dir(project_id, root)
    source_svg = Path(manifest["source_svg"]).expanduser()
    source_info = inspect_svg(source_svg)

    recognition_manifest = load_recognition_manifest(project_id, root)
    topology_manifest = load_topology_manifest(project_id, root)
    evidence_manifest = load_evidence_manifest(project_id, root)
    constraint_manifest = load_constraint_manifest(project_id, root)
    standards_manifest = load_standards_manifest(project_id, root)
    scale_manifest = load_scale_manifest(project_id, root)
    edit_intent_paths = sorted_json_files(base / "edit_intents", "*.json")
    latest_brief = latest_file(base / "briefs", "edit_brief_*.json")
    latest_apply = latest_file(base / "runs", "apply_edit_*/apply_edit_report.json")
    apply_report = read_json(latest_apply) if latest_apply else None
    latest_alternative = resolve_latest_alternative(base, apply_report)
    latest_panel = latest_file(base / "panels", "review_panel_*.html")
    latest_recognition_audit = latest_recognition_audit_path(project_id, root)
    recognition_audit = read_json(latest_recognition_audit) if latest_recognition_audit else None
    alternative_info = inspect_svg(latest_alternative) if latest_alternative else {"xml_parse": "missing", "image_elements": None}

    gates = {
        "manifest_exists": True,
        "source_svg_exists": source_svg.exists(),
        "source_svg_parse_ok": source_info.get("xml_parse") == "ok",
        "recognition_manifest_active": recognition_status(recognition_manifest) == "active",
        "topology_manifest_active": topology_status(topology_manifest) == "active",
        "standards_manifest_active": standards_status(standards_manifest) == "active",
        "opencrab_evidence_verified": evidence_status(evidence_manifest) == "verified",
        "constraint_manifest_active": constraint_status(constraint_manifest) == "active",
        "recognition_audit_pass": recognition_audit is not None and recognition_audit.get("status") == "pass",
        "edit_intent_exists": len(edit_intent_paths) > 0,
        "latest_apply_pass": bool(apply_report and apply_report.get("status") == "pass"),
        "latest_alternative_exists": latest_alternative is not None and latest_alternative.exists(),
        "latest_alternative_native_svg": alternative_info.get("xml_parse") == "ok" and alternative_info.get("image_elements") == 0,
    }
    ready_gate_names = [
        "manifest_exists",
        "source_svg_exists",
        "source_svg_parse_ok",
        "recognition_manifest_active",
        "topology_manifest_active",
        "standards_manifest_active",
        "opencrab_evidence_verified",
        "constraint_manifest_active",
        "edit_intent_exists",
    ]
    candidate_gate_names = [*ready_gate_names, "latest_apply_pass", "latest_alternative_exists", "latest_alternative_native_svg"]
    if all(gates[name] for name in candidate_gate_names):
        overall_status = "complete_candidate_ready"
    elif all(gates[name] for name in ready_gate_names) and latest_apply:
        overall_status = "candidate_review_required"
    elif all(gates[name] for name in ready_gate_names):
        overall_status = "ready_for_apply"
    else:
        overall_status = "review_required"

    status = {
        "schema": "crab-archi-design-project-status-v1",
        "created_at": now(),
        "project_id": project_id,
        "overall_status": overall_status,
        "gates": gates,
        "gate_groups": {
            "ready_for_apply": {name: gates[name] for name in ready_gate_names},
            "candidate_ready": {name: gates[name] for name in candidate_gate_names},
        },
        "latest_artifacts": {
            "recognition_manifest": str(recognition_manifest_path(project_id, root)) if recognition_manifest else None,
            "topology_manifest": str(topology_manifest_path(project_id, root)) if topology_manifest else None,
            "standards_manifest": str(standards_manifest_path(project_id, root)) if standards_manifest else None,
            "scale_manifest": str(scale_manifest_path(project_id, root)) if scale_manifest else None,
            "evidence_manifest": str(evidence_manifest_path(project_id, root)) if evidence_manifest else None,
            "constraint_manifest": str(constraint_manifest_path(project_id, root)) if constraint_manifest else None,
            "recognition_audit": str(latest_recognition_audit) if latest_recognition_audit else None,
            "edit_brief": str(latest_brief) if latest_brief else None,
            "apply_report": str(latest_apply) if latest_apply else None,
            "alternative_svg": str(latest_alternative) if latest_alternative else None,
            "review_panel": str(latest_panel) if latest_panel else None,
        },
        "metrics": {
            "primitive_count": (recognition_manifest or {}).get("primitive_count", 0),
            "label_count": (recognition_manifest or {}).get("label_count", 0),
            "program_label_count": (recognition_manifest or {}).get("program_label_count", 0),
            "column_candidate_count": (recognition_manifest or {}).get("geometry_summary", {}).get("column_candidate_count", 0),
            "wall_candidate_count": (recognition_manifest or {}).get("geometry_summary", {}).get("wall_candidate_count", 0),
            "room_envelope_candidate_count": (recognition_manifest or {}).get("geometry_summary", {}).get("room_envelope_candidate_count", 0),
            "topology_node_count": (topology_manifest or {}).get("node_count", 0),
            "topology_edge_count": (topology_manifest or {}).get("edge_count", 0),
            "source_image_elements": source_info.get("image_elements"),
            "standard_count": count_standard_items(standards_manifest),
            "selected_standard_row_count": sum_selected_standard_rows(standards_manifest),
            "scale_status": scale_status(scale_manifest),
            "scale_mm_per_world": (scale_manifest or {}).get("selected_scale", {}).get("mm_per_world"),
            "scale_confidence": (scale_manifest or {}).get("selected_scale", {}).get("confidence"),
            "evidence_count": count_evidence_items(evidence_manifest),
            "constraint_count": count_constraint_items(constraint_manifest),
            "enforced_constraint_count": count_constraint_items(constraint_manifest, enforced_only=True),
            "recognition_audit_status": recognition_audit.get("status") if recognition_audit else None,
            "recognition_audit_blocking_issue_count": len(recognition_audit.get("blocking_issues", [])) if recognition_audit else None,
            "edit_intent_count": len(edit_intent_paths),
            "latest_apply_status": apply_report.get("status") if apply_report else None,
            "latest_alternative_image_elements": alternative_info.get("image_elements"),
        },
        "opencrab_mcp": manifest.get("opencrab_mcp"),
        "source_svg_info": source_info,
        "alternative_svg_info": alternative_info,
    }
    return status


def command_project_status(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    status = build_project_status(args.project_id, root)
    out = project_dir(args.project_id, root) / "status" / "project_status.json"
    write_json(out, status)
    print(out)
    print(
        json.dumps(
            {
                "status": status["overall_status"],
                "gates": status["gates"],
                "latest_artifacts": status["latest_artifacts"],
            },
            ensure_ascii=False,
        )
    )


def selected_standard_rows(manifest: dict[str, Any] | None, max_rows: int = 20) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not manifest:
        return rows
    for item in manifest.get("standard_items", []):
        payload_rows = item.get("payload", {}).get("selected_rows", [])
        if isinstance(payload_rows, list):
            for row in payload_rows:
                if isinstance(row, dict):
                    rows.append({str(key): str(value) for key, value in row.items()})
                    if len(rows) >= max_rows:
                        return rows
    return rows


def summarize_evidence_manifest(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    return [
        {
            "id": item.get("id"),
            "source": item.get("source"),
            "source_tool": item.get("source_tool"),
            "pack_id": item.get("pack_id"),
            "query": item.get("query"),
            "summary": item.get("summary"),
            "source_file": item.get("source_file"),
            "metadata": item.get("metadata", {}),
        }
        for item in manifest.get("evidence_items", [])
    ]


def summarize_constraint_manifest(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    return [
        {
            "id": item.get("id"),
            "role": item.get("role"),
            "mode": item.get("mode"),
            "target_hint": item.get("target_hint"),
            "point_count": item.get("point_count"),
            "solver_policy": item.get("solver_policy"),
        }
        for item in manifest.get("constraint_items", [])
    ]


def summarize_recognition_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest:
        return {"status": "missing", "program_label_candidates": []}
    return {
        "status": recognition_status(manifest),
        "source_svg_info": manifest.get("source_svg_info"),
        "primitive_count": manifest.get("primitive_count", 0),
        "label_count": manifest.get("label_count", 0),
        "program_label_count": manifest.get("program_label_count", 0),
        "geometry_summary": manifest.get("geometry_summary", {}),
        "program_label_candidates": manifest.get("program_label_candidates", [])[:24],
        "column_candidates": manifest.get("geometry_candidates", {}).get("column_candidates", [])[:24],
        "room_envelope_candidates": manifest.get("geometry_candidates", {}).get("room_envelope_candidates", [])[:24],
    }


def summarize_topology_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest:
        return {"status": "missing", "nodes": [], "edges": []}
    return {
        "status": topology_status(manifest),
        "node_count": manifest.get("node_count", 0),
        "edge_count": manifest.get("edge_count", 0),
        "graph_summary": manifest.get("graph_summary", {}),
        "nodes": manifest.get("nodes", [])[:36],
        "edges": manifest.get("edges", [])[:48],
    }


def build_design_handoff_markdown(handoff: dict[str, Any]) -> str:
    lines = [
        f"# {handoff['project_id']} Design Handoff",
        "",
        f"- Status: `{handoff['status']}`",
        f"- Project status: `{handoff['project_status']['overall_status']}`",
        f"- Created: `{handoff['created_at']}`",
        f"- Source SVG: `{handoff['source_svg']}`",
        f"- Households: `{handoff['household_count']}`",
        f"- Ontology pack: `{handoff['ontology_pack']}`",
        "",
        "## Task",
        "",
        handoff["task"],
        "",
        "## Ready Gates",
        "",
    ]
    for key, value in handoff["handoff_checks"].items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")

    lines.extend(["", "## Prompt Blocks", "", "### System Prompt", "", handoff["prompt_blocks"]["system_prompt"], "", "### User Prompt", "", handoff["prompt_blocks"]["user_prompt"], ""])
    lines.extend(["## Operations", ""])
    for op in handoff["operations"]:
        label = op.get("target") or op.get("target_hint") or op.get("action") or "operation"
        action = op.get("action") or op.get("edit_type") or ""
        method = op.get("method") or op.get("snap_policy") or ""
        lines.append(f"- `{label}`: {action} {method}".strip())
    if not handoff["operations"]:
        lines.append("- No operations.")

    lines.extend(["", "## Evidence", ""])
    for item in handoff["knowledge_context"]["evidence_items"]:
        lines.append(f"- `{item.get('pack_id') or item.get('id')}`: {item.get('summary') or item.get('query') or item.get('source')}")
    if not handoff["knowledge_context"]["evidence_items"]:
        lines.append("- No evidence attached.")

    lines.extend(["", "## Constraints", ""])
    for item in handoff["knowledge_context"]["constraint_items"]:
        lines.append(f"- `{item.get('role')}`: {item.get('target_hint') or item.get('mode') or item.get('id')} (policy `{item.get('solver_policy')}`)")
    if not handoff["knowledge_context"]["constraint_items"]:
        lines.append("- No constraints attached.")

    lines.extend(["", "## Topology", ""])
    topology = handoff["knowledge_context"]["topology"]
    if topology["status"] != "missing":
        roles = topology.get("graph_summary", {}).get("roles", [])
        lines.append(f"- Status: `{topology['status']}`")
        lines.append(f"- Nodes: `{topology['node_count']}`")
        lines.append(f"- Edges: `{topology['edge_count']}`")
        lines.append(f"- Roles: `{', '.join(roles) or '-'}`")
    else:
        lines.append("- No topology manifest attached.")

    lines.extend(["", "## Standards Excerpt", ""])
    for row in handoff["knowledge_context"]["standards_excerpt"]:
        lines.append(f"- {json.dumps(row, ensure_ascii=False)}")
    if not handoff["knowledge_context"]["standards_excerpt"]:
        lines.append("- No parsed standards rows attached.")

    lines.extend(["", "## Engine Contract", ""])
    for item in handoff["engine_contract"]["must_preserve"]:
        lines.append(f"- Preserve: `{item}`")
    for item in handoff["engine_contract"]["must_output"]:
        lines.append(f"- Output: `{item}`")
    lines.append("")
    return "\n".join(lines)


def command_design_handoff(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    intent_paths = resolve_intent_paths(base, args.intent)
    intents = [read_json(path) for path in intent_paths]
    operations = flatten_operations(intents)

    recognition_manifest = load_recognition_manifest(args.project_id, root)
    topology_manifest = load_topology_manifest(args.project_id, root)
    evidence_manifest = load_evidence_manifest(args.project_id, root)
    constraint_manifest = load_constraint_manifest(args.project_id, root)
    standards_manifest = load_standards_manifest(args.project_id, root)
    project_status = build_project_status(args.project_id, root)
    latest_brief_path = latest_file(base / "briefs", "edit_brief_*.json")
    latest_brief = read_json(latest_brief_path) if latest_brief_path else None

    ready_checks = project_status["gate_groups"]["ready_for_apply"]
    handoff_checks = {
        **ready_checks,
        "latest_edit_brief_pass": bool(latest_brief and latest_brief.get("status") == "pass"),
    }
    task = args.task or (
        "Create an evidence-backed community layout alternative from the original SVG. "
        "Use the attached OpenCrab ontology evidence, standards, recognition manifest, topology graph, constraints, "
        "natural-language intent, and doodle intent. Return solver-ready native SVG instructions."
    )
    hard_constraints = manifest.get("hard_constraints", [])
    system_prompt = (
        "You are the Crab Archi Design planning agent. Use OpenCrab MCP evidence as the design knowledge base, "
        "treat standards, topology, and constraints as binding inputs, and produce solver-ready instructions rather than a raster overlay. "
        "Do not invent protected geometry changes. Preserve parking count, columns, cores, ramps, stairs, egress, and the community shell."
    )
    operation_lines = [
        f"- {op.get('target') or op.get('target_hint') or 'operation'}: {op.get('action') or op.get('edit_type') or ''} {op.get('method') or op.get('snap_policy') or ''}".strip()
        for op in operations
    ]
    user_prompt = "\n".join(
        [
            f"Project: {args.project_id}",
            f"Task: {task}",
            f"Source SVG: {manifest.get('source_svg')}",
            f"Households: {manifest.get('household_count')}",
            f"Ontology pack: {manifest.get('ontology_pack')}",
            "Operations:",
            *(operation_lines or ["- Review attached intent files."]),
            "Required behavior:",
            "- Keep every edit inside mutable/projectable community zones.",
            "- Preserve topology constraints from the topology manifest before changing room partitions.",
            "- Use OpenCrab evidence and 900-household standards before assigning program area.",
            "- Keep protected no-go and locked geometry unchanged.",
            "- Output native SVG solver instructions and cite the manifest evidence paths.",
        ]
    )

    handoff = {
        "schema": "crab-archi-design-design-handoff-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "status": "pass" if all(handoff_checks.values()) else "review_required",
        "task": task,
        "source_svg": manifest.get("source_svg"),
        "household_count": manifest.get("household_count"),
        "ontology_pack": manifest.get("ontology_pack"),
        "opencrab_mcp": manifest.get("opencrab_mcp"),
        "project_status": project_status,
        "handoff_checks": handoff_checks,
        "latest_edit_brief": str(latest_brief_path) if latest_brief_path else None,
        "intent_paths": [str(path) for path in intent_paths],
        "intents": intents,
        "operations": operations,
        "knowledge_context": {
            "recognition": summarize_recognition_manifest(recognition_manifest),
            "topology": summarize_topology_manifest(topology_manifest),
            "evidence_items": summarize_evidence_manifest(evidence_manifest),
            "constraint_items": summarize_constraint_manifest(constraint_manifest),
            "standards_status": standards_status(standards_manifest),
            "standard_count": count_standard_items(standards_manifest),
            "standards_excerpt": selected_standard_rows(standards_manifest),
            "manifest_paths": {
                "recognition_manifest": str(recognition_manifest_path(args.project_id, root)) if recognition_manifest else None,
                "topology_manifest": str(topology_manifest_path(args.project_id, root)) if topology_manifest else None,
                "evidence_manifest": str(evidence_manifest_path(args.project_id, root)) if evidence_manifest else None,
                "constraint_manifest": str(constraint_manifest_path(args.project_id, root)) if constraint_manifest else None,
                "standards_manifest": str(standards_manifest_path(args.project_id, root)) if standards_manifest else None,
            },
        },
        "prompt_blocks": {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        },
        "engine_contract": {
            "input": "design handoff JSON + original SVG + referenced manifests",
            "must_preserve": hard_constraints,
            "must_output": [
                "native_svg_alternative",
                "engine_report_with_evidence_references",
                "area_delta_summary",
                "qa_gate_results",
            ],
            "must_pass": [
                "source_svg_parse_ok",
                "recognition_manifest_active",
                "topology_manifest_active",
                "opencrab_evidence_verified",
                "constraint_manifest_active",
                "standards_manifest_active",
                "native_svg_no_images",
            ],
        },
    }
    handoff_dir = base / "handoffs"
    seq = next_sequence(handoff_dir, "design_handoff_*.json")
    json_path = handoff_dir / f"design_handoff_{seq:03d}.json"
    md_path = handoff_dir / f"design_handoff_{seq:03d}.md"
    write_json(json_path, handoff)
    md_path.write_text(build_design_handoff_markdown(handoff), encoding="utf-8")
    print(json_path)
    print(md_path)
    print(json.dumps({"status": handoff["status"], "checks": handoff_checks}, ensure_ascii=False))


def capture_command_step(name: str, func: Any, args: argparse.Namespace) -> dict[str, Any]:
    buffer = io.StringIO()
    started_at = now()
    try:
        with contextlib.redirect_stdout(buffer):
            func(args)
    except SystemExit as exc:
        return {
            "name": name,
            "status": "error",
            "started_at": started_at,
            "finished_at": now(),
            "stdout": buffer.getvalue().splitlines(),
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive workflow report path
        return {
            "name": name,
            "status": "error",
            "started_at": started_at,
            "finished_at": now(),
            "stdout": buffer.getvalue().splitlines(),
            "error": f"{exc.__class__.__name__}: {exc}",
        }
    return {
        "name": name,
        "status": "pass",
        "started_at": started_at,
        "finished_at": now(),
        "stdout": buffer.getvalue().splitlines(),
    }


def skipped_workflow_step(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "status": "skipped", "created_at": now(), "reason": reason}


def workflow_manifest_exists(project_id: str, root: Path) -> bool:
    return manifest_path(project_id, root).exists()


def workflow_has_edit_intents(project_id: str, root: Path) -> bool:
    return bool(sorted_json_files(project_dir(project_id, root) / "edit_intents", "*.json"))


def write_workflow_report(project_id: str, root: Path, report: dict[str, Any]) -> Path:
    out_dir = project_dir(project_id, root) / "workflow"
    seq = next_sequence(out_dir, "workflow_run_*.json")
    out = out_dir / f"workflow_run_{seq:03d}.json"
    write_json(out, report)
    return out


def write_revision_report(project_id: str, root: Path, report: dict[str, Any]) -> Path:
    out_dir = project_dir(project_id, root) / "revisions"
    seq = next_sequence(out_dir, "revision_run_*.json")
    out = out_dir / f"revision_run_{seq:03d}.json"
    write_json(out, report)
    return out


def command_workflow_run(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    steps: list[dict[str, Any]] = []
    project_id = args.project_id

    def add_step(name: str, func: Any, namespace: argparse.Namespace, required: bool = True) -> bool:
        step = capture_command_step(name, func, namespace)
        step["required"] = required
        steps.append(step)
        return step["status"] == "pass"

    def add_skipped(name: str, reason: str) -> None:
        step = skipped_workflow_step(name, reason)
        step["required"] = False
        steps.append(step)

    manifest_exists = workflow_manifest_exists(project_id, root)
    if args.reinit or not manifest_exists:
        if not args.source_svg:
            report = {
                "schema": "crab-archi-design-workflow-run-v1",
                "created_at": now(),
                "project_id": project_id,
                "status": "error",
                "steps": steps,
                "error": "Missing --source-svg for new workflow project.",
            }
            report_path = write_workflow_report(project_id, root, report)
            print(report_path)
            print(json.dumps({"status": "error", "error": report["error"]}, ensure_ascii=False))
            raise SystemExit(report["error"])
        init_ok = add_step(
            "init",
            command_init,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                source_svg=args.source_svg,
                households=args.households,
                standards=args.standards or [],
                ontology_pack=args.ontology_pack,
                engine_adapter=args.engine_adapter or "layout-svg-engine",
                allow_custom_engine=args.allow_custom_engine,
                opencrab_mcp_server=args.opencrab_mcp_server,
                opencrab_homepage=args.opencrab_homepage,
                allow_missing_source=False,
            ),
        )
        if not init_ok:
            report = {
                "schema": "crab-archi-design-workflow-run-v1",
                "created_at": now(),
                "project_id": project_id,
                "status": "error",
                "steps": steps,
            }
            report_path = write_workflow_report(project_id, root, report)
            print(report_path)
            print(json.dumps({"status": "error", "failed_step": "init"}, ensure_ascii=False))
            raise SystemExit(f"workflow-run failed at init; report: {report_path}")
    else:
        add_skipped("init", "Project manifest already exists. Pass --reinit to recreate it.")

    manifest = load_manifest(project_id, root)
    engine_adapter = args.engine_adapter or manifest.get("engine_adapter") or "layout-svg-engine"
    households = args.households if args.households is not None else manifest.get("household_count")

    required_sequence = [
        (
            "recognize-svg",
            command_recognize_svg,
            argparse.Namespace(project_root=str(root), project_id=project_id, source_svg=None, max_labels=args.max_labels),
        )
    ]
    for name, func, namespace in required_sequence:
        if not add_step(name, func, namespace):
            report = {
                "schema": "crab-archi-design-workflow-run-v1",
                "created_at": now(),
                "project_id": project_id,
                "status": "error",
                "steps": steps,
            }
            report_path = write_workflow_report(project_id, root, report)
            print(report_path)
            print(json.dumps({"status": "error", "failed_step": name}, ensure_ascii=False))
            raise SystemExit(f"workflow-run failed at {name}; report: {report_path}")

    standards_files = args.standards or manifest.get("standards_files", [])
    if standards_files:
        add_step(
            "standards-attach",
            command_standards_attach,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                file=args.standards or [],
                households=households,
                source="local_file",
                standard_id=None,
                summary=args.standards_summary,
                metadata=args.standards_metadata or [],
                replace=args.replace_standards,
            ),
        )
    else:
        add_skipped("standards-attach", "No standards files supplied or stored in manifest.")

    if args.opencrab_result_file or args.opencrab_result_json:
        add_step(
            "opencrab-sync",
            command_opencrab_sync,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                result_file=args.opencrab_result_file or [],
                result_json=args.opencrab_result_json or [],
                source_tool=args.opencrab_source_tool,
                workspace_id=args.workspace_id,
                pack_id=args.ontology_pack or manifest.get("ontology_pack"),
                query=args.opencrab_query,
                summary=args.evidence_summary,
                evidence_id=None,
                metadata=args.evidence_metadata or [],
                replace=args.replace_evidence,
            ),
        )
    elif args.evidence_source_file or args.evidence_summary:
        add_step(
            "evidence-attach",
            command_evidence_attach,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                source=args.evidence_source,
                pack_id=args.ontology_pack or manifest.get("ontology_pack"),
                query=args.opencrab_query,
                summary=args.evidence_summary,
                source_file=args.evidence_source_file,
                evidence_id=None,
                metadata=args.evidence_metadata or [],
            ),
        )
    elif load_evidence_manifest(project_id, root):
        add_skipped("evidence", "Existing evidence manifest found.")
    else:
        add_skipped("evidence", "No evidence source supplied.")

    if args.constraint_sketch:
        add_step(
            "constraint-attach",
            command_constraint_attach,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                sketch=args.constraint_sketch,
                source="doodle",
                role=args.constraint_role,
                replace=args.replace_constraints,
            ),
        )
    elif load_constraint_manifest(project_id, root):
        add_skipped("constraint-attach", "Existing constraint manifest found.")
    else:
        add_skipped("constraint-attach", "No constraint sketch supplied.")

    add_step("topology-build", command_topology_build, argparse.Namespace(project_root=str(root), project_id=project_id))

    if args.prompt:
        add_step("prompt-edit", command_prompt_edit, argparse.Namespace(project_root=str(root), project_id=project_id, text=args.prompt))
    elif workflow_has_edit_intents(project_id, root):
        add_skipped("prompt-edit", "Existing edit intents found.")
    else:
        add_step(
            "prompt-edit",
            command_prompt_edit,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                text="Create an evidence-backed community layout alternative while preserving protected geometry.",
            ),
        )

    if args.sketch:
        add_step("sketch-intent", command_sketch_intent, argparse.Namespace(project_root=str(root), project_id=project_id, sketch=args.sketch))
    else:
        add_skipped("sketch-intent", "No edit sketch supplied.")

    post_intent_steps = [
        ("edit-brief", command_edit_brief, argparse.Namespace(project_root=str(root), project_id=project_id, intent="all")),
        (
            "project-status-before-apply",
            command_project_status,
            argparse.Namespace(project_root=str(root), project_id=project_id),
        ),
        (
            "design-handoff",
            command_design_handoff,
            argparse.Namespace(project_root=str(root), project_id=project_id, intent="all", task=args.task),
        ),
    ]
    for name, func, namespace in post_intent_steps:
        add_step(name, func, namespace)

    if not args.skip_apply:
        add_step(
            "apply-edit",
            command_apply_edit,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                intent="all",
                engine_adapter=engine_adapter,
                engine_cwd=None,
                engine_arg=args.engine_arg or [],
                allow_custom_engine=args.allow_custom_engine,
                candidate_svg=None,
                candidate_report=None,
                preview=None,
                skip_preview=args.skip_preview,
                timeout=args.timeout,
            ),
        )
        if not args.skip_review_panel:
            add_step(
                "review-panel",
                command_review_panel,
                argparse.Namespace(project_root=str(root), project_id=project_id, apply_report="latest", alternative="from-report", title=None, open=False),
            )
        else:
            add_skipped("review-panel", "Skipped by --skip-review-panel.")
    else:
        add_skipped("apply-edit", "Skipped by --skip-apply.")

    add_step("project-status-final", command_project_status, argparse.Namespace(project_root=str(root), project_id=project_id))
    final_status = build_project_status(project_id, root)
    step_errors = [step for step in steps if step["status"] == "error"]
    expected_status = "ready_for_apply" if args.skip_apply else "complete_candidate_ready"
    workflow_status = "pass" if not step_errors and final_status["overall_status"] == expected_status else "review_required"
    report = {
        "schema": "crab-archi-design-workflow-run-v1",
        "created_at": now(),
        "project_id": project_id,
        "status": workflow_status,
        "expected_final_status": expected_status,
        "engine_adapter": engine_adapter,
        "steps": steps,
        "final_project_status": final_status,
        "latest_artifacts": final_status.get("latest_artifacts", {}),
    }
    report_path = write_workflow_report(project_id, root, report)
    print(report_path)
    print(
        json.dumps(
            {
                "status": workflow_status,
                "final_project_status": final_status["overall_status"],
                "latest_artifacts": final_status.get("latest_artifacts", {}),
            },
            ensure_ascii=False,
        )
    )


def command_revision_run(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    steps: list[dict[str, Any]] = []
    project_id = args.project_id

    def add_step(name: str, func: Any, namespace: argparse.Namespace, required: bool = True) -> bool:
        step = capture_command_step(name, func, namespace)
        step["required"] = required
        steps.append(step)
        return step["status"] == "pass"

    def add_skipped(name: str, reason: str) -> None:
        step = skipped_workflow_step(name, reason)
        step["required"] = False
        steps.append(step)

    if not workflow_manifest_exists(project_id, root):
        report = {
            "schema": "crab-archi-design-revision-run-v1",
            "created_at": now(),
            "project_id": project_id,
            "status": "error",
            "steps": steps,
            "error": "Missing project manifest. Run workflow-run or init first.",
        }
        report_path = write_revision_report(project_id, root, report)
        print(report_path)
        print(json.dumps({"status": "error", "error": report["error"]}, ensure_ascii=False))
        raise SystemExit(report["error"])

    manifest = load_manifest(project_id, root)
    engine_adapter = args.engine_adapter or manifest.get("engine_adapter") or "layout-svg-engine"
    intent_selector = args.intent

    if args.refresh_recognition or recognition_status(load_recognition_manifest(project_id, root)) != "active":
        add_step(
            "recognize-svg",
            command_recognize_svg,
            argparse.Namespace(project_root=str(root), project_id=project_id, source_svg=None, max_labels=args.max_labels),
        )
    else:
        add_skipped("recognize-svg", "Existing active recognition manifest found.")

    if args.constraint_sketch:
        add_step(
            "constraint-attach",
            command_constraint_attach,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                sketch=args.constraint_sketch,
                source="doodle",
                role=args.constraint_role,
                replace=args.replace_constraints,
            ),
        )
    elif load_constraint_manifest(project_id, root):
        add_skipped("constraint-attach", "Existing constraint manifest found.")
    else:
        add_skipped("constraint-attach", "No constraint sketch supplied.")

    add_step("topology-build", command_topology_build, argparse.Namespace(project_root=str(root), project_id=project_id))

    created_intent = False
    if args.text:
        add_step("prompt-edit", command_prompt_edit, argparse.Namespace(project_root=str(root), project_id=project_id, text=args.text))
        created_intent = True
    else:
        add_skipped("prompt-edit", "No natural-language revision supplied.")

    if args.sketch:
        add_step("sketch-intent", command_sketch_intent, argparse.Namespace(project_root=str(root), project_id=project_id, sketch=args.sketch))
        created_intent = True
    else:
        add_skipped("sketch-intent", "No edit sketch supplied.")

    if not created_intent and not workflow_has_edit_intents(project_id, root):
        final_status = build_project_status(project_id, root)
        report = {
            "schema": "crab-archi-design-revision-run-v1",
            "created_at": now(),
            "project_id": project_id,
            "status": "error",
            "expected_final_status": "ready_for_apply" if args.skip_apply else "complete_candidate_ready",
            "engine_adapter": engine_adapter,
            "steps": steps,
            "final_project_status": final_status,
            "latest_artifacts": final_status.get("latest_artifacts", {}),
            "error": "No revision intent found. Pass --text, --sketch, or create an edit intent first.",
        }
        report_path = write_revision_report(project_id, root, report)
        print(report_path)
        print(json.dumps({"status": "error", "error": report["error"]}, ensure_ascii=False))
        raise SystemExit(report["error"])

    post_intent_steps = [
        ("edit-brief", command_edit_brief, argparse.Namespace(project_root=str(root), project_id=project_id, intent=intent_selector)),
        (
            "project-status-before-apply",
            command_project_status,
            argparse.Namespace(project_root=str(root), project_id=project_id),
        ),
        (
            "design-handoff",
            command_design_handoff,
            argparse.Namespace(project_root=str(root), project_id=project_id, intent=intent_selector, task=args.task),
        ),
    ]
    for name, func, namespace in post_intent_steps:
        add_step(name, func, namespace)

    if not args.skip_apply:
        add_step(
            "apply-edit",
            command_apply_edit,
            argparse.Namespace(
                project_root=str(root),
                project_id=project_id,
                intent=intent_selector,
                engine_adapter=engine_adapter,
                engine_cwd=None,
                engine_arg=args.engine_arg or [],
                allow_custom_engine=args.allow_custom_engine,
                candidate_svg=None,
                candidate_report=None,
                preview=None,
                skip_preview=args.skip_preview,
                timeout=args.timeout,
            ),
        )
        if not args.skip_review_panel:
            add_step(
                "review-panel",
                command_review_panel,
                argparse.Namespace(project_root=str(root), project_id=project_id, apply_report="latest", alternative="from-report", title=None, open=False),
            )
        else:
            add_skipped("review-panel", "Skipped by --skip-review-panel.")
    else:
        add_skipped("apply-edit", "Skipped by --skip-apply.")

    add_step("project-status-final", command_project_status, argparse.Namespace(project_root=str(root), project_id=project_id))
    final_status = build_project_status(project_id, root)
    step_errors = [step for step in steps if step["status"] == "error"]
    expected_status = "ready_for_apply" if args.skip_apply else "complete_candidate_ready"
    revision_status = "pass" if not step_errors and final_status["overall_status"] == expected_status else "review_required"
    report = {
        "schema": "crab-archi-design-revision-run-v1",
        "created_at": now(),
        "project_id": project_id,
        "status": revision_status,
        "expected_final_status": expected_status,
        "engine_adapter": engine_adapter,
        "intent_selector": intent_selector,
        "steps": steps,
        "final_project_status": final_status,
        "latest_artifacts": final_status.get("latest_artifacts", {}),
    }
    report_path = write_revision_report(project_id, root, report)
    print(report_path)
    print(
        json.dumps(
            {
                "status": revision_status,
                "final_project_status": final_status["overall_status"],
                "latest_artifacts": final_status.get("latest_artifacts", {}),
            },
            ensure_ascii=False,
        )
    )


def job_value_list(job: dict[str, Any], key: str) -> list[str]:
    value = job.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def job_bool(job: dict[str, Any], key: str, default: bool = False) -> bool:
    value = job.get(key, default)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def job_path_value(job: dict[str, Any], key: str, path_base: Path) -> str | None:
    value = job.get(key)
    if value is None:
        return None
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return str(path)
    return str(path_base / path)


def job_path_list(job: dict[str, Any], key: str, path_base: Path) -> list[str]:
    paths = []
    for value in job_value_list(job, key):
        path = Path(value).expanduser()
        paths.append(str(path if path.is_absolute() else path_base / path))
    return paths


def workflow_namespace_from_job(job: dict[str, Any], project_root: Path, path_base: Path) -> argparse.Namespace:
    return argparse.Namespace(
        project_root=str(project_root),
        project_id=str(job["project_id"]),
        source_svg=job_path_value(job, "source_svg", path_base),
        households=job.get("households"),
        standards=job_path_list(job, "standards", path_base),
        standards_summary=job.get("standards_summary"),
        standards_metadata=job_value_list(job, "standards_metadata"),
        ontology_pack=job.get("ontology_pack"),
        engine_adapter=job.get("engine_adapter") or "layout-svg-engine",
        engine_arg=job_value_list(job, "engine_arg"),
        allow_custom_engine=job_bool(job, "allow_custom_engine"),
        opencrab_mcp_server=job.get("opencrab_mcp_server") or "opencrab",
        opencrab_homepage=job.get("opencrab_homepage") or OPENCRAB_HOMEPAGE,
        opencrab_result_file=job_path_list(job, "opencrab_result_file", path_base),
        opencrab_result_json=job_value_list(job, "opencrab_result_json"),
        opencrab_source_tool=job.get("opencrab_source_tool") or "opencrab_mcp",
        opencrab_query=job.get("opencrab_query"),
        workspace_id=job.get("workspace_id"),
        evidence_source=job.get("evidence_source") or "localcrab",
        evidence_source_file=job_path_value(job, "evidence_source_file", path_base),
        evidence_summary=job.get("evidence_summary"),
        evidence_metadata=job_value_list(job, "evidence_metadata"),
        constraint_sketch=job_path_value(job, "constraint_sketch", path_base),
        constraint_role=job.get("constraint_role"),
        prompt=job.get("prompt"),
        sketch=job_path_value(job, "sketch", path_base),
        task=job.get("task"),
        max_labels=int(job.get("max_labels", 500)),
        timeout=int(job.get("timeout", 300)),
        skip_preview=job_bool(job, "skip_preview"),
        skip_apply=job_bool(job, "skip_apply"),
        skip_review_panel=job_bool(job, "skip_review_panel"),
        replace_standards=job_bool(job, "replace_standards"),
        replace_evidence=job_bool(job, "replace_evidence"),
        replace_constraints=job_bool(job, "replace_constraints"),
        reinit=job_bool(job, "reinit"),
    )


JOB_SPEC_ALLOWED_KEYS = {
    "schema",
    "project_root",
    "path_base",
    "project_id",
    "source_svg",
    "households",
    "standards",
    "standards_summary",
    "standards_metadata",
    "ontology_pack",
    "engine_adapter",
    "engine_arg",
    "allow_custom_engine",
    "opencrab_mcp_server",
    "opencrab_homepage",
    "opencrab_result_file",
    "opencrab_result_json",
    "opencrab_source_tool",
    "opencrab_query",
    "workspace_id",
    "evidence_source",
    "evidence_source_file",
    "evidence_summary",
    "evidence_metadata",
    "constraint_sketch",
    "constraint_role",
    "prompt",
    "sketch",
    "task",
    "max_labels",
    "timeout",
    "skip_preview",
    "skip_apply",
    "skip_review_panel",
    "replace_standards",
    "replace_evidence",
    "replace_constraints",
    "reinit",
    "export_package",
    "include_source_svg",
    "only_latest",
    "skip_opencrab_sync",
    "verify_package",
    "check_local_files",
    "doctor",
    "strict",
    "zip",
}


def resolve_job_context(job: dict[str, Any], default_project_root: str, cwd: Path | None = None) -> tuple[Path, Path]:
    cwd = cwd or Path.cwd()
    path_base = Path(job.get("path_base") or ".").expanduser()
    if not path_base.is_absolute():
        path_base = cwd / path_base
    project_root = Path(job.get("project_root") or default_project_root).expanduser()
    if not project_root.is_absolute():
        project_root = cwd / project_root
    return project_root, path_base


def existing_project_artifact(project_id: str | None, project_root: Path, *parts: str) -> bool:
    if not project_id:
        return False
    return (project_dir(str(project_id), project_root).joinpath(*parts)).exists()


def job_file_entries(job: dict[str, Any], path_base: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add_entry(key: str, raw_value: str | None, required: bool = True) -> None:
        if not raw_value:
            return
        path = Path(raw_value).expanduser()
        resolved = path if path.is_absolute() else path_base / path
        entries.append({"key": key, "path": str(resolved), "exists": resolved.exists(), "required": required})

    add_entry("source_svg", job_path_value(job, "source_svg", path_base))
    for path in job_path_list(job, "standards", path_base):
        add_entry("standards", path)
    for path in job_path_list(job, "opencrab_result_file", path_base):
        add_entry("opencrab_result_file", path)
    add_entry("evidence_source_file", job_path_value(job, "evidence_source_file", path_base))
    add_entry("constraint_sketch", job_path_value(job, "constraint_sketch", path_base))
    add_entry("sketch", job_path_value(job, "sketch", path_base), required=False)
    add_entry("zip", job_path_value(job, "zip", path_base), required=False)
    return entries


def build_job_validation_report(job_path: Path, job: dict[str, Any], default_project_root: str, check_files: bool = True) -> dict[str, Any]:
    project_root, path_base = resolve_job_context(job, default_project_root)
    project_id = str(job.get("project_id") or "")
    existing_manifest = existing_project_artifact(project_id, project_root, "project_manifest.json")
    existing_standards = existing_project_artifact(project_id, project_root, "standards", "standards_manifest.json")
    existing_evidence = existing_project_artifact(project_id, project_root, "evidence", "evidence_manifest.json")
    existing_constraints = existing_project_artifact(project_id, project_root, "constraints", "constraint_manifest.json")
    unknown_keys = sorted(set(job) - JOB_SPEC_ALLOWED_KEYS)
    files = job_file_entries(job, path_base)
    missing_files = [item for item in files if item["required"] and not item["exists"]] if check_files else []
    engine_policy = engine_adapter_policy(str(job.get("engine_adapter") or ""), job_bool(job, "allow_custom_engine"))
    checks = {
        "job_file_exists": job_path.exists(),
        "schema_supported": job.get("schema") in {None, "crab-archi-design-job-spec-v1"},
        "project_id_present": bool(project_id),
        "source_svg_present_or_existing_project": bool(job.get("source_svg") or existing_manifest),
        "standards_present_or_existing_manifest": bool(job_value_list(job, "standards") or existing_standards),
        "opencrab_or_evidence_input_present": bool(job_value_list(job, "opencrab_result_file") or job_value_list(job, "opencrab_result_json") or job.get("evidence_source_file") or job.get("evidence_summary") or existing_evidence),
        "constraint_sketch_present_or_existing_manifest": bool(job.get("constraint_sketch") or existing_constraints),
        "ontology_pack_present": bool(job.get("ontology_pack") or existing_manifest),
        "engine_adapter_present": bool(job.get("engine_adapter") or existing_manifest),
        "engine_adapter_allowed": bool(existing_manifest or engine_policy["allowed"]),
        "required_files_exist": not missing_files,
    }
    warnings = []
    if unknown_keys:
        warnings.append({"type": "unknown_keys", "keys": unknown_keys})
    if not job.get("prompt"):
        warnings.append({"type": "missing_prompt", "message": "workflow-run will create a default prompt if none exists."})
    if job.get("engine_adapter") and not engine_policy["allowed"]:
        warnings.append(
            {
                "type": "engine_adapter_not_allowlisted",
                "adapter": job.get("engine_adapter"),
                "message": "Use a built-in engine adapter or set allow_custom_engine=true only for a trusted local worker.",
            }
        )
    status = "pass" if all(checks.values()) else "review_required"
    return {
        "schema": "crab-archi-design-job-validation-v1",
        "created_at": now(),
        "status": status,
        "job_spec": str(job_path),
        "project_id": project_id or None,
        "project_root": str(project_root),
        "path_base": str(path_base),
        "check_files": check_files,
        "checks": checks,
        "warnings": warnings,
        "engine_policy": engine_policy,
        "files": files,
        "missing_files": missing_files,
        "unknown_keys": unknown_keys,
    }


def maybe_set_job_value(job: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, list) and not value:
        return
    job[key] = value


def create_job_output_path(project_id: str, output: str | None, output_dir: str | None) -> Path:
    if output:
        path = Path(output).expanduser()
    else:
        base = Path(output_dir).expanduser() if output_dir else Path("job_specs")
        path = base / f"{slugify(project_id)}_job.json"
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def create_job_brief_path(job_path: Path, brief_output: str | None) -> Path:
    path = Path(brief_output).expanduser() if brief_output else job_path.with_suffix(".md")
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def write_job_validation_report_file(report: dict[str, Any], output_dir: str | None) -> Path:
    out_dir = Path(output_dir).expanduser() if output_dir else Path("diagnostics")
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    seq = next_sequence(out_dir, "job_validation_*.json")
    out = out_dir / f"job_validation_{seq:03d}.json"
    write_json(out, report)
    return out


def write_validation_report(job_path: Path, job: dict[str, Any], default_project_root: str, output_dir: str | None, check_files: bool) -> tuple[Path, dict[str, Any]]:
    report = build_job_validation_report(job_path, job, default_project_root, check_files=check_files)
    out = write_job_validation_report_file(report, output_dir)
    return out, report


def md_cell(value: Any) -> str:
    return str(value if value is not None else "n/a").replace("|", "\\|").replace("\n", " ").strip()


def bool_label(value: Any) -> str:
    return "yes" if bool(value) else "no"


def build_job_brief_markdown(job_path: Path, job: dict[str, Any], validation_path: Path | None, validation_report: dict[str, Any] | None) -> str:
    validation_status = validation_report.get("status") if validation_report else "not_run"
    files = validation_report.get("files", []) if validation_report else []
    checks = validation_report.get("checks", {}) if validation_report else {}
    lines = [
        f"# Crab Archi Design Job Brief: {job.get('project_id')}",
        "",
        "## Job",
        "",
        f"- Job spec: `{job_path}`",
        f"- Validation report: `{validation_path}`" if validation_path else "- Validation report: not generated",
        f"- Validation status: `{validation_status}`",
        f"- Project root: `{job.get('project_root')}`",
        f"- Path base: `{job.get('path_base')}`",
        f"- Source SVG: `{job.get('source_svg')}`",
        f"- Households: `{job.get('households') if job.get('households') is not None else 'n/a'}`",
        f"- Engine adapter: `{job.get('engine_adapter')}`",
        "",
        "## Knowledge And Constraints",
        "",
        f"- OpenCrab MCP server: `{job.get('opencrab_mcp_server')}`",
        f"- OpenCrab homepage: `{job.get('opencrab_homepage')}`",
        f"- OpenCrab source tool: `{job.get('opencrab_source_tool')}`",
        f"- Ontology pack: `{job.get('ontology_pack') or 'n/a'}`",
        f"- Standards files: `{len(job_value_list(job, 'standards'))}`",
        f"- OpenCrab result files: `{len(job_value_list(job, 'opencrab_result_file'))}`",
        f"- Constraint sketch: `{job.get('constraint_sketch') or 'n/a'}`",
        f"- Edit sketch: `{job.get('sketch') or 'n/a'}`",
        "",
        "## Execution Policy",
        "",
        f"- Skip preview: `{bool_label(job.get('skip_preview'))}`",
        f"- Skip apply: `{bool_label(job.get('skip_apply'))}`",
        f"- Skip review panel: `{bool_label(job.get('skip_review_panel'))}`",
        f"- Export package: `{bool_label(job.get('export_package', True))}`",
        f"- Verify package: `{bool_label(job.get('verify_package', True))}`",
        f"- Doctor: `{bool_label(job.get('doctor', True))}`",
        f"- Include source SVG in export: `{bool_label(job.get('include_source_svg'))}`",
        f"- Strict run: `{bool_label(job.get('strict'))}`",
        "",
        "## Validation Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    if checks:
        for key in sorted(checks):
            lines.append(f"| {md_cell(key)} | {md_cell(bool_label(checks[key]))} |")
    else:
        lines.append("| validation | not run |")

    lines.extend(["", "## Referenced Files", "", "| Role | Exists | Path |", "| --- | --- | --- |"])
    if files:
        for item in files:
            lines.append(f"| {md_cell(item.get('key'))} | {md_cell(bool_label(item.get('exists')))} | `{md_cell(item.get('path'))}` |")
    else:
        lines.append("| n/a | n/a | n/a |")

    prompt = str(job.get("prompt") or "").strip()
    lines.extend(["", "## Prompt", "", prompt or "No prompt supplied.", "", "## Next Commands", "", "```bash"])
    lines.append(f"crab-archi-design validate-job --job {shlex.quote(str(job_path))} --strict")
    lines.append(f"crab-archi-design run-job --job {shlex.quote(str(job_path))} --strict")
    lines.extend(["```", ""])
    return "\n".join(lines)


def write_job_brief(path: Path, job_path: Path, job: dict[str, Any], validation_path: Path | None, validation_report: dict[str, Any] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_job_brief_markdown(job_path, job, validation_path, validation_report), encoding="utf-8")


def command_create_job(args: argparse.Namespace) -> None:
    out = create_job_output_path(args.project_id, args.output, args.output_dir)
    if out.exists() and not args.force:
        raise SystemExit(f"Job spec already exists: {out}. Use --force to overwrite.")
    brief_path = create_job_brief_path(out, args.brief_output) if args.brief else None
    if brief_path and brief_path.exists() and not args.force:
        raise SystemExit(f"Job brief already exists: {brief_path}. Use --force to overwrite.")

    job: dict[str, Any] = {
        "schema": "crab-archi-design-job-spec-v1",
        "project_root": str(args.project_root),
        "path_base": args.path_base,
        "project_id": args.project_id,
        "source_svg": args.source_svg,
        "engine_adapter": args.engine_adapter,
        "allow_custom_engine": args.allow_custom_engine,
        "opencrab_mcp_server": args.opencrab_mcp_server,
        "opencrab_homepage": args.opencrab_homepage,
        "opencrab_source_tool": args.opencrab_source_tool,
        "evidence_source": args.evidence_source,
        "max_labels": args.max_labels,
        "timeout": args.timeout,
        "skip_preview": args.skip_preview,
        "skip_apply": args.skip_apply,
        "skip_review_panel": args.skip_review_panel,
        "replace_standards": args.replace_standards,
        "replace_evidence": args.replace_evidence,
        "replace_constraints": args.replace_constraints,
        "reinit": args.reinit,
        "export_package": not args.no_export_package,
        "verify_package": not args.no_verify_package,
        "doctor": not args.no_doctor,
        "include_source_svg": args.include_source_svg,
        "only_latest": args.only_latest,
        "skip_opencrab_sync": args.skip_opencrab_sync,
        "check_local_files": args.check_local_files,
        "strict": args.strict,
    }
    maybe_set_job_value(job, "households", args.households)
    maybe_set_job_value(job, "standards", args.standards)
    maybe_set_job_value(job, "standards_summary", args.standards_summary)
    maybe_set_job_value(job, "standards_metadata", args.standards_metadata)
    maybe_set_job_value(job, "ontology_pack", args.ontology_pack)
    maybe_set_job_value(job, "engine_arg", args.engine_arg)
    maybe_set_job_value(job, "opencrab_result_file", args.opencrab_result_file)
    maybe_set_job_value(job, "opencrab_result_json", args.opencrab_result_json)
    maybe_set_job_value(job, "opencrab_query", args.opencrab_query)
    maybe_set_job_value(job, "workspace_id", args.workspace_id)
    maybe_set_job_value(job, "evidence_source_file", args.evidence_source_file)
    maybe_set_job_value(job, "evidence_summary", args.evidence_summary)
    maybe_set_job_value(job, "evidence_metadata", args.evidence_metadata)
    maybe_set_job_value(job, "constraint_sketch", args.constraint_sketch)
    maybe_set_job_value(job, "constraint_role", args.constraint_role)
    maybe_set_job_value(job, "prompt", args.prompt)
    maybe_set_job_value(job, "sketch", args.sketch)
    maybe_set_job_value(job, "task", args.task)

    write_json(out, job)
    print(out)

    validation_path: Path | None = None
    validation_report: dict[str, Any] | None = None
    if args.validate:
        validation_path, validation_report = write_validation_report(
            out,
            job,
            args.project_root,
            args.validation_output_dir,
            check_files=not args.no_validate_file_checks,
        )
        print(validation_path)

    if brief_path:
        if validation_report is None:
            validation_report = build_job_validation_report(out, job, args.project_root, check_files=not args.no_validate_file_checks)
        write_job_brief(brief_path, out, job, validation_path, validation_report)
        print(brief_path)

    summary = {
        "status": "created",
        "job_spec": str(out),
        "project_id": args.project_id,
        "validation_report": str(validation_path) if validation_path else None,
        "validation_status": validation_report.get("status") if validation_report else None,
        "job_brief": str(brief_path) if brief_path else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.strict_validation and validation_report and validation_report.get("status") != "pass":
        raise SystemExit(f"create-job validation failed; report: {validation_path}")


def command_validate_job(args: argparse.Namespace) -> None:
    job_path = Path(args.job).expanduser()
    if not job_path.exists():
        job = {}
        report = {
            "schema": "crab-archi-design-job-validation-v1",
            "created_at": now(),
            "status": "review_required",
            "job_spec": str(job_path),
            "project_id": None,
            "project_root": str(Path(args.project_root).expanduser()),
            "path_base": str(Path.cwd()),
            "check_files": not args.no_check_files,
            "checks": {"job_file_exists": False},
            "warnings": [],
            "files": [],
            "missing_files": [{"key": "job", "path": str(job_path), "exists": False, "required": True}],
            "unknown_keys": [],
        }
    else:
        job = read_json(job_path)
        report = build_job_validation_report(job_path, job, args.project_root, check_files=not args.no_check_files)

    out = write_job_validation_report_file(report, args.output_dir)
    print(out)
    print(json.dumps({"status": report["status"], "checks": report["checks"], "missing_file_count": len(report["missing_files"])}, ensure_ascii=False))
    if args.strict and report["status"] != "pass":
        raise SystemExit(f"validate-job failed; report: {out}")


def step_primary_stdout(step: dict[str, Any], line_index: int = 0) -> str | None:
    stdout = step.get("stdout") or []
    if len(stdout) > line_index:
        return str(stdout[line_index])
    return None


def path_exists_string(value: str | None) -> bool:
    return bool(value and Path(value).expanduser().exists())


def require_step_report_value(step: dict[str, Any], artifact_path: str | None, key: str, expected: str = "pass") -> None:
    if step.get("status") != "pass":
        return
    if not artifact_path:
        step["status"] = "error"
        step["error"] = f"Missing expected artifact path for {key}."
        return
    path = Path(artifact_path).expanduser()
    if not path.exists():
        step["status"] = "error"
        step["error"] = f"Missing expected artifact file: {artifact_path}"
        return
    try:
        report = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        step["status"] = "error"
        step["error"] = f"Could not read artifact report {artifact_path}: {exc}"
        return
    actual = report.get(key)
    if actual != expected:
        step["status"] = "error"
        step["error"] = f"Report {key} was {actual!r}; expected {expected!r}."


def command_run_job(args: argparse.Namespace) -> None:
    job_path = Path(args.job).expanduser()
    if not job_path.exists():
        raise SystemExit(f"Missing job spec: {job_path}")
    job = read_json(job_path)
    project_id = job.get("project_id")
    if not project_id:
        raise SystemExit("Job spec requires project_id.")
    if job.get("schema") not in {None, "crab-archi-design-job-spec-v1"}:
        raise SystemExit(f"Unsupported job schema: {job.get('schema')}")
    engine_policy = engine_adapter_policy(str(job.get("engine_adapter") or "layout-svg-engine"), job_bool(job, "allow_custom_engine"))
    if not engine_policy["allowed"]:
        raise SystemExit(
            f"run-job refused non-allowlisted engine adapter {job.get('engine_adapter')!r}. "
            "Use a built-in adapter or set allow_custom_engine=true only for a trusted local worker."
        )

    path_base = Path(job.get("path_base") or ".").expanduser()
    if not path_base.is_absolute():
        path_base = Path.cwd() / path_base
    project_root = Path(job.get("project_root") or args.project_root).expanduser()
    if not project_root.is_absolute():
        project_root = Path.cwd() / project_root
    strict = bool(args.strict or job_bool(job, "strict"))
    steps: list[dict[str, Any]] = []
    artifacts: dict[str, str | None] = {
        "workflow_report": None,
        "export_manifest": None,
        "export_zip": None,
        "verify_report": None,
        "doctor_report": None,
    }

    def add_step(name: str, func: Any, namespace: argparse.Namespace) -> dict[str, Any]:
        step = capture_command_step(name, func, namespace)
        steps.append(step)
        return step

    workflow_step = add_step("workflow-run", command_workflow_run, workflow_namespace_from_job(job, project_root, path_base))
    artifacts["workflow_report"] = step_primary_stdout(workflow_step)
    require_step_report_value(workflow_step, artifacts["workflow_report"], "status", "pass")

    export_requested = job_bool(job, "export_package", True)
    verify_requested = job_bool(job, "verify_package", True)
    doctor_requested = job_bool(job, "doctor", True)
    check_local_files = job_bool(job, "check_local_files")
    workflow_passed = workflow_step["status"] == "pass"

    if export_requested and workflow_passed:
        export_step = add_step(
            "export-package",
            command_export_package,
            argparse.Namespace(
                project_root=str(project_root),
                project_id=str(project_id),
                include_source_svg=job_bool(job, "include_source_svg"),
                only_latest=job_bool(job, "only_latest"),
                skip_opencrab_sync=job_bool(job, "skip_opencrab_sync"),
            ),
        )
        artifacts["export_manifest"] = step_primary_stdout(export_step)
        artifacts["export_zip"] = step_primary_stdout(export_step, 1)
        require_step_report_value(export_step, artifacts["export_manifest"], "package_status", "pass")
    elif export_requested:
        steps.append(skipped_workflow_step("export-package", "Skipped because workflow-run did not pass."))
    else:
        steps.append(skipped_workflow_step("export-package", "Skipped by job export_package=false."))

    zip_path = artifacts["export_zip"] or job_path_value(job, "zip", path_base)
    if verify_requested and zip_path and path_exists_string(zip_path):
        verify_step = add_step(
            "verify-package",
            command_verify_package,
            argparse.Namespace(zip=zip_path, manifest=None, output_dir=None, check_local_files=check_local_files, strict=strict),
        )
        artifacts["verify_report"] = step_primary_stdout(verify_step)
        require_step_report_value(verify_step, artifacts["verify_report"], "status", "pass")
    elif verify_requested:
        steps.append(skipped_workflow_step("verify-package", "Skipped because no export ZIP is available."))
    else:
        steps.append(skipped_workflow_step("verify-package", "Skipped by job verify_package=false."))

    if doctor_requested:
        doctor_step = add_step(
            "doctor",
            command_doctor,
            argparse.Namespace(
                project_root=str(project_root),
                project_id=str(project_id),
                zip=zip_path if zip_path and path_exists_string(zip_path) else None,
                manifest=None,
                output_dir=None,
                check_local_files=check_local_files,
                strict=strict,
            ),
        )
        artifacts["doctor_report"] = step_primary_stdout(doctor_step)
        require_step_report_value(doctor_step, artifacts["doctor_report"], "status", "pass")
    else:
        steps.append(skipped_workflow_step("doctor", "Skipped by job doctor=false."))

    failed_steps = [step for step in steps if step.get("status") == "error"]
    skipped_required = [
        step
        for step in steps
        if step.get("name") in {"workflow-run", "export-package", "verify-package", "doctor"}
        and step.get("status") == "skipped"
        and not str(step.get("reason", "")).endswith("=false.")
    ]
    status = "pass" if not failed_steps and not skipped_required else "review_required"
    report = {
        "schema": "crab-archi-design-job-run-report-v1",
        "created_at": now(),
        "status": status,
        "project_id": str(project_id),
        "job_spec": str(job_path),
        "project_root": str(project_root),
        "path_base": str(path_base),
        "strict": strict,
        "steps": steps,
        "artifacts": artifacts,
        "failed_steps": [step.get("name") for step in failed_steps],
        "skipped_required_steps": [step.get("name") for step in skipped_required],
    }
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else project_dir(str(project_id), project_root) / "jobs"
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    seq = next_sequence(out_dir, "job_run_*.json")
    out = out_dir / f"job_run_{seq:03d}.json"
    write_json(out, report)
    print(out)
    print(json.dumps({"status": status, "project_id": str(project_id), "artifacts": artifacts, "failed_steps": report["failed_steps"]}, ensure_ascii=False))
    if strict and status != "pass":
        raise SystemExit(f"run-job failed; report: {out}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_arcname(project_id: str, base: Path, path: Path, role: str) -> str:
    try:
        relative = path.resolve().relative_to(base.resolve())
        return f"project/{slugify(project_id)}/{relative.as_posix()}"
    except ValueError:
        return f"external/{role}/{path.name}"


def append_package_file(files: list[dict[str, Any]], project_id: str, base: Path, role: str, path: Path | None, required: bool = False) -> None:
    if path is None:
        files.append({"role": role, "path": None, "archive_path": None, "exists": False, "required": required})
        return
    expanded = path.expanduser()
    exists = expanded.exists()
    item = {
        "role": role,
        "path": str(expanded),
        "archive_path": package_arcname(project_id, base, expanded, role) if exists else None,
        "exists": exists,
        "required": required,
    }
    if exists and expanded.is_file():
        item["size_bytes"] = expanded.stat().st_size
        item["sha256"] = sha256_file(expanded)
    files.append(item)


def append_many_package_files(files: list[dict[str, Any]], project_id: str, base: Path, role: str, paths: list[Path]) -> None:
    for index, path in enumerate(paths, start=1):
        append_package_file(files, project_id, base, f"{role}_{index:03d}", path)


def latest_handoff_files(base: Path) -> list[Path]:
    json_path = latest_file(base / "handoffs", "design_handoff_*.json")
    if not json_path:
        return []
    md_path = json_path.with_suffix(".md")
    return [path for path in [json_path, md_path] if path.exists()]


def collect_export_files(project_id: str, root: Path, include_source_svg: bool, include_all_intents: bool, include_opencrab_sync: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = load_manifest(project_id, root)
    base = project_dir(project_id, root)
    status = build_project_status(project_id, root)
    status_path = base / "status" / "project_status.json"
    write_json(status_path, status)
    files: list[dict[str, Any]] = []

    append_package_file(files, project_id, base, "project_manifest", manifest_path(project_id, root), required=True)
    append_package_file(files, project_id, base, "project_status", status_path, required=True)
    if include_source_svg:
        append_package_file(files, project_id, base, "source_svg", Path(manifest["source_svg"]), required=True)

    latest_artifacts = status.get("latest_artifacts", {})
    for role, raw_path in latest_artifacts.items():
        append_package_file(
            files,
            project_id,
            base,
            role,
            Path(raw_path) if raw_path else None,
            required=role in {"recognition_manifest", "topology_manifest", "evidence_manifest", "constraint_manifest", "standards_manifest"},
        )

    for handoff_path in latest_handoff_files(base):
        append_package_file(files, project_id, base, f"design_handoff_{handoff_path.suffix.lstrip('.')}", handoff_path)

    latest_workflow = latest_file(base / "workflow", "workflow_run_*.json")
    append_package_file(files, project_id, base, "workflow_report", latest_workflow)

    apply_report_path = latest_artifacts.get("apply_report")
    if apply_report_path and Path(apply_report_path).exists():
        apply_report = read_json(Path(apply_report_path))
        solver_input = apply_report.get("solver_input")
        append_package_file(files, project_id, base, "solver_input", Path(solver_input) if solver_input else None)
        for raw_report in apply_report.get("copied_artifacts", {}).get("report", []):
            append_package_file(files, project_id, base, "engine_report", Path(raw_report))

    if include_all_intents:
        append_many_package_files(files, project_id, base, "edit_intent", sorted_json_files(base / "edit_intents", "*.json"))
    if include_opencrab_sync:
        append_many_package_files(files, project_id, base, "opencrab_sync", sorted_json_files(base / "opencrab", "opencrab_sync_*.json"))

    return status, files


def command_export_package(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    base = project_dir(args.project_id, root)
    status, files = collect_export_files(
        args.project_id,
        root,
        include_source_svg=args.include_source_svg,
        include_all_intents=not args.only_latest,
        include_opencrab_sync=not args.skip_opencrab_sync,
    )
    export_dir = base / "exports"
    seq = next_sequence(export_dir, "export_manifest_*.json")
    zip_path = export_dir / f"{slugify(args.project_id)}_export_{seq:03d}.zip"
    manifest_path_out = export_dir / f"export_manifest_{seq:03d}.json"
    export_manifest = {
        "schema": "crab-archi-design-export-package-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "package_status": "pass" if all(not item.get("required") or item.get("exists") for item in files) else "review_required",
        "project_status": status,
        "zip_path": str(zip_path),
        "include_source_svg": args.include_source_svg,
        "file_count": sum(1 for item in files if item.get("exists")),
        "missing_required": [item for item in files if item.get("required") and not item.get("exists")],
        "files": files,
    }
    write_json(manifest_path_out, export_manifest)

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path_out, "export_manifest.json")
        for item in files:
            if not item.get("exists") or not item.get("archive_path"):
                continue
            path = Path(item["path"])
            if path.is_file():
                archive.write(path, item["archive_path"])

    export_manifest["zip_size_bytes"] = zip_path.stat().st_size
    export_manifest["zip_sha256"] = sha256_file(zip_path)
    write_json(manifest_path_out, export_manifest)
    print(manifest_path_out)
    print(zip_path)
    print(
        json.dumps(
            {
                "status": export_manifest["package_status"],
                "zip_path": str(zip_path),
                "file_count": export_manifest["file_count"],
                "missing_required_count": len(export_manifest["missing_required"]),
            },
            ensure_ascii=False,
        )
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_export_manifest_from_zip(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as archive:
        try:
            data = archive.read("export_manifest.json")
        except KeyError as exc:
            raise SystemExit(f"Missing export_manifest.json in ZIP: {zip_path}") from exc
    return json.loads(data.decode("utf-8"))


def verify_export_package(zip_path: Path, manifest_path_arg: Path | None = None, check_local_files: bool = False) -> dict[str, Any]:
    if not zip_path.exists():
        raise SystemExit(f"Missing export ZIP: {zip_path}")
    manifest = read_json(manifest_path_arg) if manifest_path_arg else load_export_manifest_from_zip(zip_path)

    checks: dict[str, Any] = {
        "zip_exists": zip_path.exists(),
        "zip_readable": False,
        "zip_has_export_manifest": False,
        "zip_integrity_ok": False,
        "manifest_schema_ok": manifest.get("schema") == "crab-archi-design-export-package-v1",
        "manifest_package_status_pass": manifest.get("package_status") == "pass",
        "project_status_candidate_ready": manifest.get("project_status", {}).get("overall_status") in {"complete_candidate_ready", "ready_for_apply"},
        "no_missing_required_files": not manifest.get("missing_required"),
        "archive_file_hashes_ok": False,
        "local_file_hashes_ok": True,
    }
    archive_entries: list[dict[str, Any]] = []
    missing_archive_paths: list[str] = []
    hash_mismatches: list[dict[str, Any]] = []
    local_mismatches: list[dict[str, Any]] = []

    with zipfile.ZipFile(zip_path) as archive:
        checks["zip_readable"] = True
        checks["zip_has_export_manifest"] = "export_manifest.json" in archive.namelist()
        checks["zip_integrity_ok"] = archive.testzip() is None
        names = set(archive.namelist())
        for item in manifest.get("files", []):
            if not item.get("exists") or not item.get("archive_path"):
                continue
            archive_path = str(item["archive_path"])
            entry = {
                "role": item.get("role"),
                "archive_path": archive_path,
                "exists_in_zip": archive_path in names,
                "sha256_ok": None,
                "size_ok": None,
            }
            if archive_path not in names:
                missing_archive_paths.append(archive_path)
                archive_entries.append(entry)
                continue
            data = archive.read(archive_path)
            expected_hash = item.get("sha256")
            expected_size = item.get("size_bytes")
            entry["sha256_ok"] = not expected_hash or sha256_bytes(data) == expected_hash
            entry["size_ok"] = expected_size is None or len(data) == expected_size
            if entry["sha256_ok"] is False or entry["size_ok"] is False:
                hash_mismatches.append(
                    {
                        "role": item.get("role"),
                        "archive_path": archive_path,
                        "expected_sha256": expected_hash,
                        "actual_sha256": sha256_bytes(data),
                        "expected_size_bytes": expected_size,
                        "actual_size_bytes": len(data),
                    }
                )
            archive_entries.append(entry)

    checks["archive_file_hashes_ok"] = not missing_archive_paths and not hash_mismatches

    if check_local_files:
        for item in manifest.get("files", []):
            expected_hash = item.get("sha256")
            raw_path = item.get("path")
            if not raw_path or not expected_hash:
                continue
            path = Path(raw_path).expanduser()
            if not path.exists():
                local_mismatches.append({"role": item.get("role"), "path": raw_path, "error": "missing"})
                continue
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                local_mismatches.append({"role": item.get("role"), "path": raw_path, "expected_sha256": expected_hash, "actual_sha256": actual_hash})
        checks["local_file_hashes_ok"] = not local_mismatches

    status = "pass" if all(checks.values()) else "review_required"
    return {
        "schema": "crab-archi-design-export-verification-v1",
        "created_at": now(),
        "status": status,
        "zip_path": str(zip_path),
        "manifest_path": str(manifest_path_arg) if manifest_path_arg else "zip:export_manifest.json",
        "project_id": manifest.get("project_id"),
        "checks": checks,
        "file_count": manifest.get("file_count"),
        "archive_entry_count": len(archive_entries),
        "missing_archive_paths": missing_archive_paths,
        "hash_mismatches": hash_mismatches,
        "local_mismatches": local_mismatches,
        "archive_entries": archive_entries,
        "export_manifest": manifest,
    }


def command_verify_package(args: argparse.Namespace) -> None:
    zip_path = Path(args.zip).expanduser()
    manifest_path_arg = Path(args.manifest).expanduser() if args.manifest else None
    report = verify_export_package(zip_path, manifest_path_arg=manifest_path_arg, check_local_files=args.check_local_files)
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else zip_path.parent
    seq = next_sequence(out_dir, "verify_report_*.json")
    out = out_dir / f"verify_report_{seq:03d}.json"
    write_json(out, report)
    print(out)
    print(json.dumps({"status": report["status"], "checks": report["checks"], "missing_archive_paths": report["missing_archive_paths"]}, ensure_ascii=False))
    if args.strict and report["status"] != "pass":
        raise SystemExit(f"verify-package failed; report: {out}")


def build_doctor_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.project_root)
    repo = Path(__file__).resolve().parents[2]
    local_checks = {
        "python_version_ok": sys.version_info >= (3, 9),
        "cli_file_exists": Path(__file__).exists(),
        "reference_svg_engine_exists": (Path(__file__).resolve().parent / "reference_svg_engine.py").exists(),
        "layout_svg_engine_exists": (Path(__file__).resolve().parent / "layout_svg_engine.py").exists(),
        "doodle_editor_exists": (repo / "tools" / "doodle_editor.html").exists(),
        "readme_exists": (repo / "README.md").exists(),
        "opencrab_workflow_doc_exists": (repo / "docs" / "opencrab_mcp_workflow.md").exists(),
        "opencrab_homepage_configured": OPENCRAB_HOMEPAGE.startswith("https://"),
    }
    required_checks = {f"local.{name}": value for name, value in local_checks.items()}

    project_status: dict[str, Any] | None = None
    project_checks: dict[str, Any] = {}
    project_error: str | None = None
    manifest: dict[str, Any] | None = None

    if args.project_id:
        project_manifest_path = manifest_path(args.project_id, root)
        project_checks["project_manifest_exists"] = project_manifest_path.exists()
        if project_manifest_path.exists():
            try:
                manifest = read_json(project_manifest_path)
                project_checks["project_manifest_readable"] = True
            except (OSError, json.JSONDecodeError) as exc:
                project_checks["project_manifest_readable"] = False
                project_error = str(exc)
        else:
            project_checks["project_manifest_readable"] = False

        if manifest:
            opencrab_mcp = manifest.get("opencrab_mcp", {})
            project_checks.update(
                {
                    "opencrab_mcp_required": opencrab_mcp.get("required") is True,
                    "opencrab_homepage_present": bool(opencrab_mcp.get("homepage")),
                    "opencrab_server_present": bool(opencrab_mcp.get("mcp_server")),
                    "ontology_pack_configured": bool(opencrab_mcp.get("ontology_pack") or manifest.get("ontology_pack")),
                }
            )
            try:
                project_status = build_project_status(args.project_id, root)
                project_checks["project_status_buildable"] = True
                gates = project_status.get("gates", {})
                ready_gate_names = [
                    "manifest_exists",
                    "source_svg_exists",
                    "source_svg_parse_ok",
                    "recognition_manifest_active",
                    "topology_manifest_active",
                    "standards_manifest_active",
                    "opencrab_evidence_verified",
                    "constraint_manifest_active",
                    "edit_intent_exists",
                ]
                for name in ready_gate_names:
                    project_checks[name] = bool(gates.get(name))

                has_candidate = bool(args.zip) or any(gates.get(name) for name in ["latest_apply_pass", "latest_alternative_exists", "latest_alternative_native_svg"])
                if has_candidate:
                    for name in ["latest_apply_pass", "latest_alternative_exists", "latest_alternative_native_svg"]:
                        project_checks[name] = bool(gates.get(name))
                project_checks["project_ready_for_solver"] = project_status.get("overall_status") in {
                    "ready_for_apply",
                    "candidate_review_required",
                    "complete_candidate_ready",
                }
                project_checks["project_candidate_ready"] = project_status.get("overall_status") == "complete_candidate_ready"
            except SystemExit as exc:
                project_checks["project_status_buildable"] = False
                project_error = str(exc)

        for name, value in project_checks.items():
            if name == "project_candidate_ready" and not args.zip:
                continue
            if name == "project_ready_for_solver" and project_checks.get("project_candidate_ready"):
                continue
            required_checks[f"project.{name}"] = bool(value)

    package_report: dict[str, Any] | None = None
    package_checks: dict[str, Any] = {}
    package_error: str | None = None
    if args.zip:
        zip_path = Path(args.zip).expanduser()
        manifest_path_arg = Path(args.manifest).expanduser() if args.manifest else None
        package_checks["package_zip_exists"] = zip_path.exists()
        if zip_path.exists():
            try:
                package_report = verify_export_package(zip_path, manifest_path_arg=manifest_path_arg, check_local_files=args.check_local_files)
                package_checks["package_verify_pass"] = package_report.get("status") == "pass"
            except (OSError, json.JSONDecodeError, zipfile.BadZipFile, SystemExit) as exc:
                package_checks["package_verify_pass"] = False
                package_error = str(exc)
        else:
            package_checks["package_verify_pass"] = False
        required_checks["package.package_zip_exists"] = package_checks["package_zip_exists"]
        required_checks["package.package_verify_pass"] = package_checks["package_verify_pass"]

    status = "pass" if all(required_checks.values()) else "review_required"
    report = {
        "schema": "crab-archi-design-doctor-report-v1",
        "created_at": now(),
        "status": status,
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "minimum_supported": "3.9",
        },
        "paths": {
            "repo_root": str(repo),
            "project_root": str(root),
            "project_dir": str(project_dir(args.project_id, root)) if args.project_id else None,
            "zip": str(Path(args.zip).expanduser()) if args.zip else None,
        },
        "local_checks": local_checks,
        "project_checks": project_checks,
        "package_checks": package_checks,
        "required_checks": required_checks,
        "project_error": project_error,
        "package_error": package_error,
        "project_status": project_status,
        "package_verification": package_report,
    }
    return report


def command_doctor(args: argparse.Namespace) -> None:
    report = build_doctor_report(args)
    root = Path(args.project_root)
    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser()
    elif args.project_id:
        out_dir = project_dir(args.project_id, root) / "diagnostics"
    elif args.zip:
        out_dir = Path(args.zip).expanduser().parent
    else:
        out_dir = Path("diagnostics")
    seq = next_sequence(out_dir, "doctor_report_*.json")
    out = out_dir / f"doctor_report_{seq:03d}.json"
    write_json(out, report)
    print(out)
    print(json.dumps({"status": report["status"], "required_checks": report["required_checks"]}, ensure_ascii=False))
    if args.strict and report["status"] != "pass":
        raise SystemExit(f"doctor failed; report: {out}")


def latest_export_zip(project_id: str, root: Path) -> Path | None:
    return latest_file(project_dir(project_id, root) / "exports", "*_export_*.zip")


def command_release_audit(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    zip_path = Path(args.zip).expanduser() if args.zip else latest_export_zip(args.project_id, root)
    manifest_path_arg = Path(args.manifest).expanduser() if args.manifest else None
    project_status = build_project_status(args.project_id, root)
    package_report: dict[str, Any] | None = None
    package_error: str | None = None
    if zip_path and zip_path.exists():
        try:
            package_report = verify_export_package(zip_path, manifest_path_arg=manifest_path_arg, check_local_files=args.check_local_files)
        except (OSError, json.JSONDecodeError, zipfile.BadZipFile, SystemExit) as exc:
            package_error = str(exc)

    doctor_report = build_doctor_report(
        argparse.Namespace(
            project_root=str(root),
            project_id=args.project_id,
            zip=str(zip_path) if zip_path else None,
            manifest=str(manifest_path_arg) if manifest_path_arg else None,
            output_dir=None,
            check_local_files=args.check_local_files,
            strict=False,
        )
    )
    gates = project_status.get("gates", {})
    checks = {
        "project_candidate_ready": project_status.get("overall_status") == "complete_candidate_ready",
        "recognition_manifest_active": bool(gates.get("recognition_manifest_active")),
        "topology_manifest_active": bool(gates.get("topology_manifest_active")),
        "standards_manifest_active": bool(gates.get("standards_manifest_active")),
        "opencrab_evidence_verified": bool(gates.get("opencrab_evidence_verified")),
        "constraint_manifest_active": bool(gates.get("constraint_manifest_active")),
        "latest_apply_pass": bool(gates.get("latest_apply_pass")),
        "latest_alternative_native_svg": bool(gates.get("latest_alternative_native_svg")),
        "package_zip_found": bool(zip_path and zip_path.exists()),
        "package_verify_pass": bool(package_report and package_report.get("status") == "pass"),
        "doctor_pass": doctor_report.get("status") == "pass",
    }
    status = "pass" if all(checks.values()) else "review_required"
    report = {
        "schema": "crab-archi-design-release-audit-v1",
        "created_at": now(),
        "status": status,
        "project_id": args.project_id,
        "project_root": str(root),
        "zip_path": str(zip_path) if zip_path else None,
        "manifest_path": str(manifest_path_arg) if manifest_path_arg else None,
        "checks": checks,
        "package_error": package_error,
        "project_status": project_status,
        "package_verification": package_report,
        "doctor_report": doctor_report,
    }
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else project_dir(args.project_id, root) / "audits"
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    seq = next_sequence(out_dir, "release_audit_*.json")
    out = out_dir / f"release_audit_{seq:03d}.json"
    write_json(out, report)
    print(out)
    print(json.dumps({"status": status, "checks": checks, "zip_path": str(zip_path) if zip_path else None}, ensure_ascii=False))
    if args.strict and status != "pass":
        raise SystemExit(f"release-audit failed; report: {out}")


def command_doodle_editor(args: argparse.Namespace) -> None:
    candidates = [
        Path(__file__).resolve().parents[2] / "tools" / "doodle_editor.html",
        Path.cwd() / "tools" / "doodle_editor.html",
    ]
    editor = next((path for path in candidates if path.exists()), None)
    if editor is None:
        raise SystemExit("Missing tools/doodle_editor.html")
    print(editor)
    print(editor.as_uri())
    if args.open:
        webbrowser.open(editor.as_uri())


def command_workflow_contract(args: argparse.Namespace) -> None:
    contract = build_engine_workflow_contract(opencrab_homepage=args.opencrab_homepage, production_engine=args.production_engine)
    if args.state:
        state = read_json(Path(args.state).expanduser())
        contract["state_audit"] = audit_workflow_state(state, contract)
    if args.output:
        out = Path(args.output).expanduser()
        write_json(out, contract)
        print(out)
        print(json.dumps({"status": "pass", "stage_count": len(contract["stages"]), "has_state_audit": "state_audit" in contract}, ensure_ascii=False))
        return
    print(json.dumps(contract, ensure_ascii=False, indent=2))


def build_mcp_tool_manifest() -> dict[str, Any]:
    tool_defaults = {
        "project_root_arg": "--project-root",
        "project_id_arg": "--project-id",
        "command": "crab-archi-design",
        "execution": "local_exec",
        "output_contract": "first stdout line is the primary artifact path unless the command prints JSON only",
    }
    tools = [
        {
            "id": "create_job",
            "cli_subcommand": "create-job",
            "description": "Create a validated crab-archi-design job spec from uploaded SVG, standards, OpenCrab evidence, constraints, and prompt inputs.",
            "required_args": ["--project-id", "--source-svg"],
            "optional_args": [
                "--output",
                "--output-dir",
                "--path-base",
                "--households",
                "--standards",
                "--standards-summary",
                "--standards-metadata",
                "--ontology-pack",
                "--engine-adapter",
                "--engine-arg",
                "--opencrab-mcp-server",
                "--opencrab-homepage",
                "--opencrab-result-file",
                "--opencrab-result-json",
                "--opencrab-source-tool",
                "--opencrab-query",
                "--workspace-id",
                "--evidence-source",
                "--evidence-source-file",
                "--evidence-summary",
                "--evidence-metadata",
                "--constraint-sketch",
                "--constraint-role",
                "--prompt",
                "--sketch",
                "--task",
                "--max-labels",
                "--timeout",
                "--skip-preview",
                "--skip-apply",
                "--skip-review-panel",
                "--replace-standards",
                "--replace-evidence",
                "--replace-constraints",
                "--reinit",
                "--no-export-package",
                "--no-verify-package",
                "--no-doctor",
                "--include-source-svg",
                "--only-latest",
                "--skip-opencrab-sync",
                "--check-local-files",
                "--strict",
                "--force",
                "--validate",
                "--validation-output-dir",
                "--no-validate-file-checks",
                "--strict-validation",
                "--brief",
                "--brief-output",
            ],
            "outputs": ["job_specs/<project>_job.json", "diagnostics/job_validation_###.json when --validate is used", "job_specs/<project>_job.md when --brief is used"],
            "gates": ["job_spec_created", "job_spec_valid"],
        },
        {
            "id": "run_job",
            "cli_subcommand": "run-job",
            "description": "Run workflow/export/verify/doctor from a single JSON job spec for SaaS, OAuth, or MCP workers.",
            "required_args": ["--job"],
            "optional_args": ["--output-dir", "--strict"],
            "outputs": ["jobs/job_run_###.json", "workflow/workflow_run_###.json", "exports/<project>_export_###.zip", "diagnostics/doctor_report_###.json"],
            "gates": ["workflow_run_pass", "package_verify_pass", "doctor_pass"],
        },
        {
            "id": "validate_job",
            "cli_subcommand": "validate-job",
            "description": "Validate a job spec before run-job by checking schema, required design inputs, and referenced local files.",
            "required_args": ["--job"],
            "optional_args": ["--output-dir", "--no-check-files", "--strict"],
            "outputs": ["diagnostics/job_validation_###.json"],
            "gates": ["job_spec_valid", "required_files_exist"],
        },
        {
            "id": "workflow_run",
            "cli_subcommand": "workflow-run",
            "description": "Run the full source SVG to evidence-backed candidate workflow.",
            "required_args": ["--project-id"],
            "typical_required_args_for_new_project": ["--source-svg", "--standards", "--ontology-pack", "--opencrab-result-file", "--constraint-sketch", "--prompt"],
            "optional_args": ["--households", "--engine-adapter", "--sketch", "--task", "--skip-preview", "--reinit"],
            "outputs": ["workflow/workflow_run_###.json", "alternatives/alternative_###.svg", "panels/review_panel_###.html"],
            "gates": ["recognition", "standards", "opencrab_evidence", "constraints", "topology", "edit_brief", "apply_edit", "review_panel"],
        },
        {
            "id": "revision_run",
            "cli_subcommand": "revision-run",
            "description": "Run an existing project's natural-language or doodle revision loop through topology, handoff, apply, review, and status.",
            "required_args": ["--project-id"],
            "optional_args": [
                "--text",
                "--sketch",
                "--constraint-sketch",
                "--constraint-role",
                "--replace-constraints",
                "--intent",
                "--task",
                "--engine-adapter",
                "--engine-arg",
                "--refresh-recognition",
                "--max-labels",
                "--timeout",
                "--skip-preview",
                "--skip-apply",
                "--skip-review-panel",
            ],
            "outputs": ["revisions/revision_run_###.json", "alternatives/alternative_###.svg", "panels/review_panel_###.html"],
            "gates": ["topology_manifest_active", "edit_brief", "apply_edit", "review_panel", "candidate_ready"],
        },
        {
            "id": "topology_build",
            "cli_subcommand": "topology-build",
            "description": "Build a target topology graph from SVG recognition, standards, OpenCrab evidence, and drawing constraints.",
            "required_args": ["--project-id"],
            "outputs": ["topology/topology_manifest.json"],
            "gates": ["topology_manifest_active"],
        },
        {
            "id": "recognize_svg_v2",
            "cli_subcommand": "recognize-svg-v2",
            "description": "Create a safe-parser, world-coordinate Recognition IR v2 for parser/solver development.",
            "required_args": ["--project-id"],
            "optional_args": ["--source-svg"],
            "outputs": ["recognition/recognition_ir_v2.json"],
            "gates": ["safe_svg_parse", "world_coordinate_ir"],
        },
        {
            "id": "recognition_audit",
            "cli_subcommand": "recognition-audit",
            "description": "Audit whether the target SVG is understood well enough for ontology projection and native SVG mutation.",
            "required_args": ["--project-id"],
            "outputs": ["audits/recognition_audit_###.json"],
            "gates": ["program_labels_positioned", "columns_detected", "community_shell_confirmed", "mutable_zone_confirmed", "protected_zone_confirmed", "label_topology_edges_present"],
        },
        {
            "id": "svg_patch_plan",
            "cli_subcommand": "svg-patch-plan",
            "description": "Plan topology-aware same-layer SVG element mutations and OpenCrab-adjacency opening candidates instead of generating an overlay redraw layer.",
            "required_args": ["--project-id"],
            "optional_args": ["--max-candidates"],
            "outputs": ["patch_plans/svg_patch_plan_###.json"],
            "gates": ["same_layer_mutable_candidates_found", "program_anchors_found", "overlay_generation_disallowed", "recognition_audit_pass"],
        },
        {
            "id": "opencrab_request",
            "cli_subcommand": "opencrab-request",
            "description": "Create an OpenCrab MCP tool-call request package from the current project context before evidence sync.",
            "required_args": ["--project-id"],
            "optional_args": ["--query", "--intent", "--pack-id", "--workspace-id", "--source-tool", "--opencrab-mcp-server", "--opencrab-homepage", "--households", "--max-results", "--expected-result-file", "--output-dir"],
            "outputs": ["opencrab/opencrab_request_###.json", "opencrab/opencrab_request_###.md"],
            "gates": ["opencrab_mcp_request_ready"],
        },
        {
            "id": "opencrab_sync",
            "cli_subcommand": "opencrab-sync",
            "description": "Normalize OpenCrab MCP JSON results and attach them as project evidence.",
            "required_args": ["--project-id"],
            "optional_args": ["--result-file", "--result-json", "--source-tool", "--workspace-id", "--pack-id", "--query", "--summary", "--metadata", "--replace"],
            "outputs": ["opencrab/opencrab_sync_###.json", "evidence/evidence_manifest.json"],
            "gates": ["opencrab_evidence_verified"],
        },
        {
            "id": "prompt_edit",
            "cli_subcommand": "prompt-edit",
            "description": "Convert natural-language revision instructions into structured edit intent JSON.",
            "required_args": ["--project-id", "--text"],
            "outputs": ["edit_intents/prompt_edit_###.json"],
            "gates": ["edit_intent_exists"],
        },
        {
            "id": "sketch_intent",
            "cli_subcommand": "sketch-intent",
            "description": "Convert vector doodle sketch JSON into structured edit intent JSON.",
            "required_args": ["--project-id", "--sketch"],
            "outputs": ["edit_intents/sketch_edit_###.json"],
            "gates": ["edit_intent_exists", "sketch_points_inside_viewbox"],
        },
        {
            "id": "constraint_attach",
            "cli_subcommand": "constraint-attach",
            "description": "Attach doodle-derived community shell, no-go, lock, mutable, and projectable constraints.",
            "required_args": ["--project-id", "--sketch"],
            "optional_args": ["--role", "--replace"],
            "outputs": ["constraints/constraint_manifest.json"],
            "gates": ["constraint_manifest_active"],
        },
        {
            "id": "scale_attach",
            "cli_subcommand": "scale-attach",
            "description": "Attach explicit architectural scale calibration from manual input, OCR, or a known dimension measurement.",
            "required_args": ["--project-id"],
            "optional_args": ["--mm-per-world", "--known-mm", "--known-world-length", "--confidence", "--source", "--method", "--scale-id", "--evidence", "--note", "--metadata", "--replace"],
            "outputs": ["scale/scale_manifest.json"],
            "gates": ["scale_manifest_active_or_review_required"],
        },
        {
            "id": "edit_brief",
            "cli_subcommand": "edit-brief",
            "description": "Summarize natural-language and doodle edit intents before SVG mutation.",
            "required_args": ["--project-id"],
            "optional_args": ["--intent"],
            "outputs": ["briefs/edit_brief_###.json", "briefs/edit_brief_###.md"],
            "gates": ["recognition_manifest_active", "topology_manifest_active", "opencrab_evidence_verified", "standards_manifest_active", "constraint_manifest_active"],
        },
        {
            "id": "design_handoff",
            "cli_subcommand": "design-handoff",
            "description": "Build a Codex/LLM/MCP/engine handoff package from current project gates.",
            "required_args": ["--project-id"],
            "optional_args": ["--intent", "--task"],
            "outputs": ["handoffs/design_handoff_###.json", "handoffs/design_handoff_###.md"],
            "gates": ["ready_for_apply"],
        },
        {
            "id": "apply_edit",
            "cli_subcommand": "apply-edit",
            "description": "Run the configured deterministic engine adapter and collect a native SVG alternative.",
            "required_args": ["--project-id"],
            "optional_args": ["--intent", "--engine-adapter", "--engine-cwd", "--engine-arg", "--candidate-svg", "--candidate-report", "--preview", "--skip-preview", "--timeout"],
            "outputs": ["runs/apply_edit_###/apply_edit_report.json", "alternatives/alternative_###.svg"],
            "gates": ["native_svg_no_images", "candidate_hard_gates_pass", "latest_apply_pass", "latest_alternative_native_svg", "locked_geometry_unchanged", "locked_targets_not_selected"],
        },
        {
            "id": "review_panel",
            "cli_subcommand": "review-panel",
            "description": "Generate a local before/after HTML review panel.",
            "required_args": ["--project-id"],
            "optional_args": ["--apply-report", "--alternative", "--title", "--open"],
            "outputs": ["panels/review_panel_###.html"],
            "gates": ["latest_alternative_exists"],
        },
        {
            "id": "project_status",
            "cli_subcommand": "project-status",
            "description": "Write the command-center project readiness report.",
            "required_args": ["--project-id"],
            "outputs": ["status/project_status.json"],
            "gates": ["ready_for_apply", "candidate_ready"],
        },
        {
            "id": "export_package",
            "cli_subcommand": "export-package",
            "description": "Create a portable ZIP package of latest evidence, handoff, SVG, and review artifacts.",
            "required_args": ["--project-id"],
            "optional_args": ["--include-source-svg", "--only-latest", "--skip-opencrab-sync"],
            "outputs": ["exports/export_manifest_###.json", "exports/<project>_export_###.zip"],
            "gates": ["no_missing_required_files"],
        },
        {
            "id": "verify_package",
            "cli_subcommand": "verify-package",
            "description": "Verify exported ZIP integrity, manifest, required files, and hashes.",
            "required_args": ["--zip"],
            "optional_args": ["--manifest", "--output-dir", "--check-local-files", "--strict"],
            "outputs": ["exports/verify_report_###.json"],
            "gates": ["zip_integrity_ok", "archive_file_hashes_ok"],
        },
        {
            "id": "doctor",
            "cli_subcommand": "doctor",
            "description": "Diagnose local install, OpenCrab configuration, project gates, candidate readiness, and optional package verification.",
            "required_args": [],
            "optional_args": ["--project-id", "--zip", "--manifest", "--output-dir", "--check-local-files", "--strict"],
            "outputs": ["diagnostics/doctor_report_###.json"],
            "gates": ["local_ready", "project_ready", "package_ready"],
        },
        {
            "id": "release_audit",
            "cli_subcommand": "release-audit",
            "description": "Run the final project/package handoff audit across project status, OpenCrab gates, native SVG checks, package verification, and doctor diagnostics.",
            "required_args": ["--project-id"],
            "optional_args": ["--zip", "--manifest", "--output-dir", "--check-local-files", "--strict"],
            "outputs": ["audits/release_audit_###.json"],
            "gates": ["candidate_ready", "native_svg_no_images", "package_verify_pass", "doctor_pass"],
        },
        {
            "id": "workflow_contract",
            "cli_subcommand": "workflow-contract",
            "description": "Print the authoritative recognition/OpenCrab/intent/solver/native-SVG/QA stage contract and optionally audit a workflow state JSON.",
            "required_args": [],
            "optional_args": ["--output", "--state", "--production-engine", "--opencrab-homepage"],
            "outputs": ["workflow contract JSON on stdout or output path"],
            "gates": ["engine_workflow_contract_available"],
        },
        {
            "id": "mcp_manifest",
            "cli_subcommand": "mcp-manifest",
            "description": "Print or write the MCP/OAuth exec tool manifest.",
            "required_args": [],
            "optional_args": ["--output"],
            "outputs": ["manifest JSON on stdout or output path"],
            "gates": ["tool_catalog_available"],
        },
        {
            "id": "mcp_config",
            "cli_subcommand": "mcp-config",
            "description": "Print or write MCP client and OAuth worker runtime configuration.",
            "required_args": [],
            "optional_args": ["--output", "--server-name", "--project-root", "--cwd"],
            "outputs": ["runtime configuration JSON on stdout or output path"],
            "gates": ["mcp_server_config_available"],
        },
        {
            "id": "mcp_smoke",
            "cli_subcommand": "mcp-smoke",
            "description": "Start the configured stdio MCP server and verify initialize/tools/list JSON-RPC roundtrip.",
            "required_args": [],
            "optional_args": ["--config", "--server-name", "--project-root", "--cwd", "--output-dir", "--strict", "--timeout"],
            "outputs": ["diagnostics/mcp_smoke_report_###.json"],
            "gates": ["mcp_initialize_ok", "mcp_tools_list_ok"],
        },
        {
            "id": "doodle_editor",
            "cli_subcommand": "doodle-editor",
            "description": "Print or open the browser-based SVG doodle editor for sketch JSON capture.",
            "required_args": [],
            "optional_args": ["--open"],
            "outputs": ["file:// URL printed to stdout"],
            "gates": ["doodle_editor_exists"],
        },
    ]
    return {
        "schema": "crab-archi-design-mcp-tool-manifest-v1",
        "created_at": now(),
        "name": PROJECT_NAME,
        "version": PROJECT_VERSION,
        "description": "Exec-callable architectural community SVG design workflow for Codex, MCP wrappers, OAuth upload flows, and SaaS ingestion.",
        "homepage": "https://github.com/AlexAI-MCP/Crab-Archi-Design",
        "opencrab": {
            "required": True,
            "homepage": OPENCRAB_HOMEPAGE,
            "expected_mcp_server": "opencrab",
            "evidence_gate": "opencrab_evidence_verified",
            "decision_rule": "final layout alternatives must cite OpenCrab ontology evidence before native SVG mutation is accepted",
        },
        "transport": {
            "primary": "exec",
            "command": "crab-archi-design",
            "oauth_boundary": "OAuth or SaaS services should call the CLI in a sandboxed worker and exchange only generated package ZIPs or validated JSON artifacts.",
            "mcp_wrapper": "Expose each tool id as an MCP tool that maps arguments to the listed CLI subcommand.",
        },
        "defaults": tool_defaults,
        "tools": tools,
        "recommended_sequences": {
            "saas_job_runner": ["create_job", "validate_job", "run_job"],
            "new_project_to_candidate": ["workflow_run", "export_package", "verify_package", "doctor", "release_audit"],
            "revision_loop": ["revision_run", "export_package", "verify_package", "doctor", "release_audit"],
            "manual_revision_loop": ["topology_build", "recognition_audit", "scale_attach", "svg_patch_plan", "prompt_edit", "sketch_intent", "edit_brief", "design_handoff", "apply_edit", "review_panel", "project_status", "export_package", "verify_package", "doctor", "release_audit"],
            "opencrab_first_manual_loop": ["opencrab_request", "opencrab_sync", "constraint_attach", "topology_build", "recognition_audit", "scale_attach", "svg_patch_plan", "prompt_edit", "sketch_intent", "edit_brief", "design_handoff", "apply_edit"],
            "mcp_server_bootstrap": ["mcp_manifest", "mcp_config", "mcp_smoke", "doctor"],
        },
        "security": {
            "source_svg_in_package": "opt-in via export-package --include-source-svg",
            "native_svg_only_gate": "latest_alternative_native_svg",
            "no_raster_overlay_gate": "native_svg_no_images",
            "engine_adapter_allowlist": sorted(BUILTIN_ENGINE_ADAPTERS),
            "custom_engine_policy": "Custom engine adapters are refused by default. Direct local CLI users may pass --allow-custom-engine for trusted adapters; MCP raw_args cannot use this escape hatch.",
            "secrets_policy": "do not put OAuth tokens, API keys, or private credentials in project manifests, evidence payloads, or export packages",
        },
    }


def command_mcp_manifest(args: argparse.Namespace) -> None:
    manifest = build_mcp_tool_manifest()
    if args.output:
        out = Path(args.output).expanduser()
        write_json(out, manifest)
        print(out)
        print(json.dumps({"status": "pass", "tool_count": len(manifest["tools"])}, ensure_ascii=False))
        return
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def build_mcp_runtime_config(server_name: str, project_root_value: str, cwd_value: str | None = None) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    cwd = str(Path(cwd_value).expanduser()) if cwd_value else str(repo_root)
    env = {
        "CRAB_ARCHI_PROJECT_ROOT": project_root_value,
        "PYTHONPATH": str(repo_root / "src"),
    }
    server = {
        "command": "crab-archi-design-mcp",
        "args": ["--stdio"],
        "cwd": cwd,
        "env": env,
    }
    return {
        "schema": "crab-archi-design-mcp-runtime-config-v1",
        "created_at": now(),
        "name": PROJECT_NAME,
        "version": PROJECT_VERSION,
        "server_name": server_name,
        "description": "Runtime configuration for connecting Crab Archi Design to MCP clients, Codex exec runners, and OAuth/SaaS workers.",
        "opencrab": {
            "required": True,
            "homepage": OPENCRAB_HOMEPAGE,
            "evidence_gate": "opencrab_evidence_verified",
        },
        "mcp_server": server,
        "codex": {
            "mcpServers": {
                server_name: server,
            }
        },
        "generic_mcp_client": {
            "servers": {
                server_name: server,
            }
        },
        "oauth_worker": {
            "recommended_environment": env,
            "preflight_commands": [
                ["crab-archi-design-mcp", "--help"],
                ["crab-archi-design", "mcp-manifest"],
                ["crab-archi-design", "--project-root", project_root_value, "doctor", "--strict"],
            ],
            "handoff_sequence": [
                "create-job --project-id <project-id> --source-svg <source.svg> --standards <standards.csv> --ontology-pack <pack-id> --opencrab-result-file <opencrab.json> --constraint-sketch <constraints.json> --output <job.json> --validate --strict-validation --brief",
                "validate-job --job <job.json> --strict",
                "run-job --job <job.json> --strict",
            ],
            "manual_handoff_sequence": ["workflow-run", "export-package", "verify-package --strict", "doctor --strict"],
            "revision_sequence": ["revision-run", "export-package", "verify-package --strict", "doctor --strict"],
            "package_policy": "Do not include source SVG unless the receiving system is authorized; export-package requires --include-source-svg for that.",
        },
        "smoke_test_messages": [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    }


def command_mcp_config(args: argparse.Namespace) -> None:
    config = build_mcp_runtime_config(args.server_name, args.config_project_root, args.cwd)
    if args.output:
        out = Path(args.output).expanduser()
        write_json(out, config)
        print(out)
        print(json.dumps({"status": "pass", "server_name": args.server_name}, ensure_ascii=False))
        return
    print(json.dumps(config, ensure_ascii=False, indent=2))


def select_mcp_server_config(config: dict[str, Any], server_name: str | None) -> tuple[str, dict[str, Any]]:
    if server_name:
        for section, key in [("codex", "mcpServers"), ("generic_mcp_client", "servers")]:
            servers = config.get(section, {}).get(key, {})
            if server_name in servers:
                return server_name, servers[server_name]
        if config.get("server_name") == server_name and isinstance(config.get("mcp_server"), dict):
            return server_name, config["mcp_server"]
        raise SystemExit(f"Missing MCP server config for server name: {server_name}")
    if isinstance(config.get("mcp_server"), dict):
        return str(config.get("server_name") or "crab-archi-design"), config["mcp_server"]
    servers = config.get("codex", {}).get("mcpServers", {})
    if servers:
        name = next(iter(servers))
        return str(name), servers[name]
    raise SystemExit("Missing MCP server configuration.")


def write_mcp_message(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise RuntimeError("MCP process stdin is closed.")
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def read_mcp_message(process: subprocess.Popen[str]) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("MCP process stdout is closed.")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("MCP process closed stdout before responding.")
    return json.loads(line)


def run_mcp_smoke(config: dict[str, Any], server_name: str | None, timeout: int) -> dict[str, Any]:
    selected_name, server = select_mcp_server_config(config, server_name)
    command = [str(server.get("command") or "crab-archi-design-mcp"), *[str(item) for item in server.get("args", [])]]
    if command[0] == "crab-archi-design-mcp" and shutil.which(command[0]) is None:
        command = [sys.executable, "-m", "crab_archi_design.mcp_server", *command[1:]]
    cwd = str(Path(server["cwd"]).expanduser()) if server.get("cwd") else None
    env = os.environ.copy()
    for key, value in (server.get("env") or {}).items():
        env[str(key)] = str(value)

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    messages = config.get("smoke_test_messages") or [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    responses: list[dict[str, Any]] = []
    try:
        for message in messages:
            write_mcp_message(process, message)
            if "id" in message:
                responses.append(read_mcp_message(process))
        if process.stdin:
            process.stdin.close()
        stderr = process.stderr.read() if process.stderr else ""
        process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    initialize = next((item for item in responses if item.get("id") == 1), {})
    tools_list = next((item for item in responses if item.get("id") == 2), {})
    tools = tools_list.get("result", {}).get("tools", []) if isinstance(tools_list.get("result"), dict) else []
    tool_names = [item.get("name") for item in tools if isinstance(item, dict)]
    checks = {
        "process_exit_ok": process.returncode == 0,
        "initialize_ok": initialize.get("result", {}).get("serverInfo", {}).get("name") == "crab-archi-design-mcp",
        "tools_list_ok": bool(tools),
        "create_job_tool_available": "create_job" in tool_names,
        "run_job_tool_available": "run_job" in tool_names,
        "validate_job_tool_available": "validate_job" in tool_names,
        "opencrab_request_tool_available": "opencrab_request" in tool_names,
        "doctor_tool_available": "doctor" in tool_names,
        "release_audit_tool_available": "release_audit" in tool_names,
        "workflow_run_tool_available": "workflow_run" in tool_names,
        "revision_run_tool_available": "revision_run" in tool_names,
        "topology_build_tool_available": "topology_build" in tool_names,
    }
    return {
        "schema": "crab-archi-design-mcp-smoke-report-v1",
        "created_at": now(),
        "status": "pass" if all(checks.values()) else "review_required",
        "server_name": selected_name,
        "command": command,
        "cwd": cwd,
        "checks": checks,
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "responses": responses,
        "stderr": stderr,
        "returncode": process.returncode,
    }


def command_mcp_smoke(args: argparse.Namespace) -> None:
    if args.config:
        config = read_json(Path(args.config).expanduser())
    else:
        config = build_mcp_runtime_config(args.server_name, args.config_project_root, args.cwd)
    report = run_mcp_smoke(config, args.server_name, args.timeout)
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else Path("diagnostics")
    seq = next_sequence(out_dir, "mcp_smoke_report_*.json")
    out = out_dir / f"mcp_smoke_report_{seq:03d}.json"
    write_json(out, report)
    print(out)
    print(json.dumps({"status": report["status"], "checks": report["checks"], "tool_count": report["tool_count"]}, ensure_ascii=False))
    if args.strict and report["status"] != "pass":
        raise SystemExit(f"mcp-smoke failed; report: {out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crab-archi-design")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a project manifest from an original SVG.")
    p_init.add_argument("--project-id", required=True)
    p_init.add_argument("--source-svg", required=True)
    p_init.add_argument("--households", type=int)
    p_init.add_argument("--standards", action="append", default=[])
    p_init.add_argument("--ontology-pack")
    p_init.add_argument("--engine-adapter")
    p_init.add_argument("--allow-custom-engine", action="store_true", help="Allow a trusted non-built-in engine adapter for this local project.")
    p_init.add_argument("--opencrab-mcp-server", default="opencrab")
    p_init.add_argument("--opencrab-homepage", default=OPENCRAB_HOMEPAGE)
    p_init.add_argument("--allow-missing-source", action="store_true")
    p_init.set_defaults(func=command_init)

    p_prompt = sub.add_parser("prompt-edit", help="Convert natural language into structured edit intent JSON.")
    p_prompt.add_argument("--project-id", required=True)
    p_prompt.add_argument("--text", required=True)
    p_prompt.set_defaults(func=command_prompt_edit)

    p_sketch = sub.add_parser("sketch-intent", help="Convert vector doodle sketch JSON into edit intent JSON.")
    p_sketch.add_argument("--project-id", required=True)
    p_sketch.add_argument("--sketch", required=True)
    p_sketch.set_defaults(func=command_sketch_intent)

    p_recognize = sub.add_parser("recognize-svg", help="Create a lightweight recognition manifest from the source SVG.")
    p_recognize.add_argument("--project-id", required=True)
    p_recognize.add_argument("--source-svg", help="Override the project source SVG.")
    p_recognize.add_argument("--max-labels", type=int, default=500)
    p_recognize.set_defaults(func=command_recognize_svg)

    p_recognize_v2 = sub.add_parser("recognize-svg-v2", help="Create a world-coordinate Recognition IR v2 from the source SVG.")
    p_recognize_v2.add_argument("--project-id", required=True)
    p_recognize_v2.add_argument("--source-svg", help="Override the project source SVG.")
    p_recognize_v2.set_defaults(func=command_recognize_svg_v2)

    p_topology = sub.add_parser("topology-build", help="Build a target topology graph from recognition, standards, evidence, and constraints.")
    p_topology.add_argument("--project-id", required=True)
    p_topology.set_defaults(func=command_topology_build)

    p_recognition_audit = sub.add_parser("recognition-audit", help="Gate SVG mutation on target drawing recognition quality.")
    p_recognition_audit.add_argument("--project-id", required=True)
    p_recognition_audit.set_defaults(func=command_recognition_audit)

    p_svg_patch_plan = sub.add_parser("svg-patch-plan", help="Plan same-layer SVG element mutations inside confirmed mutable zones.")
    p_svg_patch_plan.add_argument("--project-id", required=True)
    p_svg_patch_plan.add_argument("--max-candidates", type=int, default=240)
    p_svg_patch_plan.set_defaults(func=command_svg_patch_plan)

    p_standards = sub.add_parser("standards-attach", help="Attach standards files such as CSV, Excel, PDF, or JSON to a project.")
    p_standards.add_argument("--project-id", required=True)
    p_standards.add_argument("--file", action="append", default=[], help="Standards file. Repeatable. Defaults to manifest standards_files.")
    p_standards.add_argument("--households", type=int, help="Household count to select rows from tabular standards.")
    p_standards.add_argument("--source", default="local_file")
    p_standards.add_argument("--standard-id")
    p_standards.add_argument("--summary")
    p_standards.add_argument("--metadata", action="append", default=[])
    p_standards.add_argument("--replace", action="store_true")
    p_standards.set_defaults(func=command_standards_attach)

    p_scale = sub.add_parser("scale-attach", help="Attach an explicit architectural scale calibration to a project.")
    p_scale.add_argument("--project-id", required=True)
    p_scale.add_argument("--mm-per-world", type=float, help="Direct architectural scale: millimeters per SVG world unit.")
    p_scale.add_argument("--known-mm", type=float, help="Known real-world length in millimeters.")
    p_scale.add_argument("--known-world-length", type=float, help="Measured source SVG world-coordinate length for --known-mm.")
    p_scale.add_argument("--confidence", type=float, default=0.95)
    p_scale.add_argument("--source", default="manual")
    p_scale.add_argument("--method", default="manual_known_dimension")
    p_scale.add_argument("--scale-id")
    p_scale.add_argument("--evidence")
    p_scale.add_argument("--note")
    p_scale.add_argument("--metadata", action="append", default=[])
    p_scale.add_argument("--replace", action="store_true")
    p_scale.set_defaults(func=command_scale_attach)

    p_evidence = sub.add_parser("evidence-attach", help="Attach OpenCrab/LocalCrab evidence to a project.")
    p_evidence.add_argument("--project-id", required=True)
    p_evidence.add_argument("--source", default="localcrab")
    p_evidence.add_argument("--pack-id")
    p_evidence.add_argument("--query")
    p_evidence.add_argument("--summary")
    p_evidence.add_argument("--source-file")
    p_evidence.add_argument("--evidence-id")
    p_evidence.add_argument("--metadata", action="append", default=[])
    p_evidence.set_defaults(func=command_evidence_attach)

    p_opencrab_sync = sub.add_parser("opencrab-sync", help="Normalize OpenCrab MCP result JSON and attach it as project evidence.")
    p_opencrab_sync.add_argument("--project-id", required=True)
    p_opencrab_sync.add_argument("--result-file", action="append", default=[], help="OpenCrab MCP JSON result file. Repeatable.")
    p_opencrab_sync.add_argument("--result-json", action="append", default=[], help="Inline OpenCrab MCP JSON result. Repeatable.")
    p_opencrab_sync.add_argument("--source-tool", default="opencrab_mcp")
    p_opencrab_sync.add_argument("--workspace-id")
    p_opencrab_sync.add_argument("--pack-id")
    p_opencrab_sync.add_argument("--query")
    p_opencrab_sync.add_argument("--summary")
    p_opencrab_sync.add_argument("--evidence-id")
    p_opencrab_sync.add_argument("--metadata", action="append", default=[])
    p_opencrab_sync.add_argument("--replace", action="store_true")
    p_opencrab_sync.set_defaults(func=command_opencrab_sync)

    p_opencrab_request = sub.add_parser("opencrab-request", help="Create an OpenCrab MCP request package from project context.")
    p_opencrab_request.add_argument("--project-id", required=True)
    p_opencrab_request.add_argument("--query", help="Explicit OpenCrab query. Defaults to a project-context query.")
    p_opencrab_request.add_argument("--intent", help="Design intent to include in the generated query.")
    p_opencrab_request.add_argument("--pack-id", help="Ontology pack id. Defaults to the project ontology pack.")
    p_opencrab_request.add_argument("--workspace-id")
    p_opencrab_request.add_argument("--source-tool", default="opencrab_search_documents")
    p_opencrab_request.add_argument("--opencrab-mcp-server", default="opencrab")
    p_opencrab_request.add_argument("--opencrab-homepage", default=OPENCRAB_HOMEPAGE)
    p_opencrab_request.add_argument("--households", type=int)
    p_opencrab_request.add_argument("--max-results", type=int, default=12)
    p_opencrab_request.add_argument("--expected-result-file", help="Expected path where the OpenCrab MCP JSON response will be saved.")
    p_opencrab_request.add_argument("--output-dir", help="Directory for opencrab_request_###.json/.md. Defaults to project opencrab directory.")
    p_opencrab_request.set_defaults(func=command_opencrab_request)

    p_constraint = sub.add_parser("constraint-attach", help="Attach doodle-based lock/no-go/mutable constraints to a project.")
    p_constraint.add_argument("--project-id", required=True)
    p_constraint.add_argument("--sketch", required=True)
    p_constraint.add_argument("--source", default="doodle")
    p_constraint.add_argument("--role", help="Override inferred role for every stroke, e.g. community_shell, lock, no_go, mutable.")
    p_constraint.add_argument("--replace", action="store_true", help="Replace the existing constraint manifest instead of appending.")
    p_constraint.set_defaults(func=command_constraint_attach)

    p_brief = sub.add_parser("edit-brief", help="Summarize natural-language and doodle intents before SVG mutation.")
    p_brief.add_argument("--project-id", required=True)
    p_brief.add_argument("--intent", default="all", help="latest, all, or a project-relative/absolute intent JSON path.")
    p_brief.set_defaults(func=command_edit_brief)

    p_apply = sub.add_parser("apply-edit", help="Run an engine adapter from structured edit intent and collect a native SVG alternative.")
    p_apply.add_argument("--project-id", required=True)
    p_apply.add_argument("--intent", default="latest", help="latest, all, or a project-relative/absolute intent JSON path.")
    p_apply.add_argument("--engine-adapter", help="Override the manifest engine adapter.")
    p_apply.add_argument("--engine-cwd", help="Working directory for the engine adapter.")
    p_apply.add_argument("--engine-arg", action="append", default=[], help="Additional argument passed to the engine adapter. Repeatable.")
    p_apply.add_argument("--allow-custom-engine", action="store_true", help="Allow a trusted non-built-in engine adapter for this local run.")
    p_apply.add_argument("--candidate-svg", help="Explicit SVG candidate path if the engine does not print/report it.")
    p_apply.add_argument("--candidate-report", help="Explicit report path to copy into the run directory.")
    p_apply.add_argument("--preview", help="Explicit preview image path to copy into the run directory.")
    p_apply.add_argument("--skip-preview", action="store_true", help="Pass --skip-preview to adapters that support it.")
    p_apply.add_argument("--timeout", type=int, default=300)
    p_apply.set_defaults(func=command_apply_edit)

    p_editor = sub.add_parser("doodle-editor", help="Print or open the local SVG doodle editor.")
    p_editor.add_argument("--open", action="store_true")
    p_editor.set_defaults(func=command_doodle_editor)

    p_review = sub.add_parser("review-panel", help="Generate a local before/after SVG review panel.")
    p_review.add_argument("--project-id", required=True)
    p_review.add_argument("--apply-report", default="latest", help="latest or a project-relative/absolute apply_edit_report.json path.")
    p_review.add_argument("--alternative", default="from-report", help="from-report, latest, or a project-relative/absolute SVG path.")
    p_review.add_argument("--title")
    p_review.add_argument("--open", action="store_true")
    p_review.set_defaults(func=command_review_panel)

    p_status = sub.add_parser("project-status", help="Summarize project readiness gates and latest artifacts.")
    p_status.add_argument("--project-id", required=True)
    p_status.set_defaults(func=command_project_status)

    p_handoff = sub.add_parser("design-handoff", help="Build a Codex/LLM/engine design handoff package from current project gates.")
    p_handoff.add_argument("--project-id", required=True)
    p_handoff.add_argument("--intent", default="all", help="latest, all, or a project-relative/absolute intent JSON path.")
    p_handoff.add_argument("--task", help="Optional design task override for the handoff prompt.")
    p_handoff.set_defaults(func=command_design_handoff)

    p_workflow = sub.add_parser("workflow-run", help="Run the full recognition/evidence/constraint/intent/apply/review workflow.")
    p_workflow.add_argument("--project-id", required=True)
    p_workflow.add_argument("--source-svg", help="Required when creating a new project.")
    p_workflow.add_argument("--households", type=int)
    p_workflow.add_argument("--standards", action="append", default=[])
    p_workflow.add_argument("--standards-summary")
    p_workflow.add_argument("--standards-metadata", action="append", default=[])
    p_workflow.add_argument("--ontology-pack")
    p_workflow.add_argument("--engine-adapter")
    p_workflow.add_argument("--engine-arg", action="append", default=[])
    p_workflow.add_argument("--allow-custom-engine", action="store_true", help="Allow a trusted non-built-in engine adapter for this local workflow.")
    p_workflow.add_argument("--opencrab-mcp-server", default="opencrab")
    p_workflow.add_argument("--opencrab-homepage", default=OPENCRAB_HOMEPAGE)
    p_workflow.add_argument("--opencrab-result-file", action="append", default=[])
    p_workflow.add_argument("--opencrab-result-json", action="append", default=[])
    p_workflow.add_argument("--opencrab-source-tool", default="opencrab_mcp")
    p_workflow.add_argument("--opencrab-query")
    p_workflow.add_argument("--workspace-id")
    p_workflow.add_argument("--evidence-source", default="localcrab")
    p_workflow.add_argument("--evidence-source-file")
    p_workflow.add_argument("--evidence-summary")
    p_workflow.add_argument("--evidence-metadata", action="append", default=[])
    p_workflow.add_argument("--constraint-sketch")
    p_workflow.add_argument("--constraint-role")
    p_workflow.add_argument("--prompt")
    p_workflow.add_argument("--sketch", help="Optional edit sketch JSON for sketch-intent.")
    p_workflow.add_argument("--task", help="Optional design handoff task.")
    p_workflow.add_argument("--max-labels", type=int, default=500)
    p_workflow.add_argument("--timeout", type=int, default=300)
    p_workflow.add_argument("--skip-preview", action="store_true")
    p_workflow.add_argument("--skip-apply", action="store_true")
    p_workflow.add_argument("--skip-review-panel", action="store_true")
    p_workflow.add_argument("--replace-standards", action="store_true")
    p_workflow.add_argument("--replace-evidence", action="store_true")
    p_workflow.add_argument("--replace-constraints", action="store_true")
    p_workflow.add_argument("--reinit", action="store_true")
    p_workflow.set_defaults(func=command_workflow_run)

    p_revision = sub.add_parser("revision-run", help="Run an existing project's natural-language or doodle revision loop.")
    p_revision.add_argument("--project-id", required=True)
    p_revision.add_argument("--text", help="Natural-language revision instruction to convert into prompt-edit intent.")
    p_revision.add_argument("--sketch", help="Optional edit sketch JSON for sketch-intent.")
    p_revision.add_argument("--constraint-sketch", help="Optional updated constraint sketch JSON to attach before rebuilding topology.")
    p_revision.add_argument("--constraint-role")
    p_revision.add_argument("--replace-constraints", action="store_true")
    p_revision.add_argument("--intent", default="all", help="latest, all, or a project-relative/absolute intent JSON path.")
    p_revision.add_argument("--task", help="Optional design handoff task.")
    p_revision.add_argument("--engine-adapter")
    p_revision.add_argument("--engine-arg", action="append", default=[])
    p_revision.add_argument("--allow-custom-engine", action="store_true", help="Allow a trusted non-built-in engine adapter for this local revision.")
    p_revision.add_argument("--refresh-recognition", action="store_true")
    p_revision.add_argument("--max-labels", type=int, default=500)
    p_revision.add_argument("--timeout", type=int, default=300)
    p_revision.add_argument("--skip-preview", action="store_true")
    p_revision.add_argument("--skip-apply", action="store_true")
    p_revision.add_argument("--skip-review-panel", action="store_true")
    p_revision.set_defaults(func=command_revision_run)

    p_create_job = sub.add_parser("create-job", help="Create a JSON job spec for validate-job and run-job.")
    p_create_job.add_argument("--project-id", required=True)
    p_create_job.add_argument("--source-svg", required=True)
    p_create_job.add_argument("--output", help="Explicit job spec path. Defaults to job_specs/<project>_job.json.")
    p_create_job.add_argument("--output-dir", help="Directory for the generated job spec when --output is omitted.")
    p_create_job.add_argument("--path-base", default=".", help="Base directory for relative file paths stored in the job spec.")
    p_create_job.add_argument("--households", type=int)
    p_create_job.add_argument("--standards", action="append", default=[])
    p_create_job.add_argument("--standards-summary")
    p_create_job.add_argument("--standards-metadata", action="append", default=[])
    p_create_job.add_argument("--ontology-pack")
    p_create_job.add_argument("--engine-adapter", default="layout-svg-engine")
    p_create_job.add_argument("--engine-arg", action="append", default=[])
    p_create_job.add_argument("--allow-custom-engine", action="store_true", help="Store allow_custom_engine=true for a trusted local worker. Do not use for untrusted OAuth/MCP uploads.")
    p_create_job.add_argument("--opencrab-mcp-server", default="opencrab")
    p_create_job.add_argument("--opencrab-homepage", default=OPENCRAB_HOMEPAGE)
    p_create_job.add_argument("--opencrab-result-file", action="append", default=[])
    p_create_job.add_argument("--opencrab-result-json", action="append", default=[])
    p_create_job.add_argument("--opencrab-source-tool", default="opencrab_mcp")
    p_create_job.add_argument("--opencrab-query")
    p_create_job.add_argument("--workspace-id")
    p_create_job.add_argument("--evidence-source", default="localcrab")
    p_create_job.add_argument("--evidence-source-file")
    p_create_job.add_argument("--evidence-summary")
    p_create_job.add_argument("--evidence-metadata", action="append", default=[])
    p_create_job.add_argument("--constraint-sketch")
    p_create_job.add_argument("--constraint-role")
    p_create_job.add_argument("--prompt")
    p_create_job.add_argument("--sketch", help="Optional edit sketch JSON for sketch-intent.")
    p_create_job.add_argument("--task", help="Optional design handoff task.")
    p_create_job.add_argument("--max-labels", type=int, default=500)
    p_create_job.add_argument("--timeout", type=int, default=300)
    p_create_job.add_argument("--skip-preview", action="store_true")
    p_create_job.add_argument("--skip-apply", action="store_true")
    p_create_job.add_argument("--skip-review-panel", action="store_true")
    p_create_job.add_argument("--replace-standards", action="store_true")
    p_create_job.add_argument("--replace-evidence", action="store_true")
    p_create_job.add_argument("--replace-constraints", action="store_true")
    p_create_job.add_argument("--reinit", action="store_true")
    p_create_job.add_argument("--no-export-package", action="store_true")
    p_create_job.add_argument("--no-verify-package", action="store_true")
    p_create_job.add_argument("--no-doctor", action="store_true")
    p_create_job.add_argument("--include-source-svg", action="store_true")
    p_create_job.add_argument("--only-latest", action="store_true")
    p_create_job.add_argument("--skip-opencrab-sync", action="store_true")
    p_create_job.add_argument("--check-local-files", action="store_true")
    p_create_job.add_argument("--strict", action="store_true", help="Store strict=true for run-job.")
    p_create_job.add_argument("--force", action="store_true", help="Overwrite an existing job spec path.")
    p_create_job.add_argument("--validate", action="store_true", help="Write a validation report immediately after creating the job spec.")
    p_create_job.add_argument("--validation-output-dir", help="Directory for job_validation_###.json when --validate is used.")
    p_create_job.add_argument("--no-validate-file-checks", action="store_true", help="Skip local file existence checks during --validate.")
    p_create_job.add_argument("--strict-validation", action="store_true", help="Exit non-zero if --validate does not pass.")
    p_create_job.add_argument("--brief", action="store_true", help="Write a Markdown job brief next to the generated job spec.")
    p_create_job.add_argument("--brief-output", help="Explicit Markdown brief path. Defaults to the job spec path with .md suffix.")
    p_create_job.set_defaults(func=command_create_job)

    p_job = sub.add_parser("run-job", help="Run workflow/export/verify/doctor from a JSON job spec.")
    p_job.add_argument("--job", required=True, help="Path to a crab-archi-design-job-spec-v1 JSON file.")
    p_job.add_argument("--output-dir", help="Directory for job_run_###.json. Defaults to the project jobs directory.")
    p_job.add_argument("--strict", action="store_true", help="Exit non-zero unless the job report status is pass.")
    p_job.set_defaults(func=command_run_job)

    p_validate_job = sub.add_parser("validate-job", help="Validate a job spec before run-job.")
    p_validate_job.add_argument("--job", required=True, help="Path to a crab-archi-design-job-spec-v1 JSON file.")
    p_validate_job.add_argument("--output-dir", help="Directory for job_validation_###.json. Defaults to diagnostics.")
    p_validate_job.add_argument("--no-check-files", action="store_true", help="Validate structure without checking referenced local files.")
    p_validate_job.add_argument("--strict", action="store_true", help="Exit non-zero if validation is not pass.")
    p_validate_job.set_defaults(func=command_validate_job)

    p_export = sub.add_parser("export-package", help="Create a ZIP package of latest project artifacts for handoff or SaaS upload.")
    p_export.add_argument("--project-id", required=True)
    p_export.add_argument("--include-source-svg", action="store_true", help="Include the original source SVG in the ZIP.")
    p_export.add_argument("--only-latest", action="store_true", help="Skip all edit intents and include only latest summary artifacts.")
    p_export.add_argument("--skip-opencrab-sync", action="store_true", help="Do not include OpenCrab sync artifacts.")
    p_export.set_defaults(func=command_export_package)

    p_verify = sub.add_parser("verify-package", help="Verify an exported Crab Archi Design ZIP package.")
    p_verify.add_argument("--zip", required=True, help="Export ZIP path.")
    p_verify.add_argument("--manifest", help="Optional external export_manifest_###.json path. Defaults to export_manifest.json inside the ZIP.")
    p_verify.add_argument("--output-dir", help="Directory for verify_report_###.json. Defaults to ZIP directory.")
    p_verify.add_argument("--check-local-files", action="store_true", help="Also verify local source paths listed in the export manifest.")
    p_verify.add_argument("--strict", action="store_true", help="Exit non-zero if verification is not pass.")
    p_verify.set_defaults(func=command_verify_package)

    p_doctor = sub.add_parser("doctor", help="Diagnose local install, optional project gates, and optional export ZIP.")
    p_doctor.add_argument("--project-id", help="Optional project id to check.")
    p_doctor.add_argument("--zip", help="Optional export ZIP path to verify.")
    p_doctor.add_argument("--manifest", help="Optional external export manifest path for ZIP verification.")
    p_doctor.add_argument("--output-dir", help="Directory for doctor_report_###.json.")
    p_doctor.add_argument("--check-local-files", action="store_true", help="Also verify local source paths when checking an export ZIP.")
    p_doctor.add_argument("--strict", action="store_true", help="Exit non-zero if any required diagnostic check fails.")
    p_doctor.set_defaults(func=command_doctor)

    p_release = sub.add_parser("release-audit", help="Run final project/package handoff audit before downstream release.")
    p_release.add_argument("--project-id", required=True)
    p_release.add_argument("--zip", help="Export ZIP path. Defaults to the latest project export ZIP.")
    p_release.add_argument("--manifest", help="Optional external export manifest path for ZIP verification.")
    p_release.add_argument("--output-dir", help="Directory for release_audit_###.json. Defaults to project audits.")
    p_release.add_argument("--check-local-files", action="store_true", help="Also verify local source paths when checking the export ZIP.")
    p_release.add_argument("--strict", action="store_true", help="Exit non-zero unless the release audit passes.")
    p_release.set_defaults(func=command_release_audit)

    p_workflow_contract = sub.add_parser("workflow-contract", help="Print the engine workflow contract and optional state audit.")
    p_workflow_contract.add_argument("--output", help="Optional path for workflow contract JSON. Defaults to stdout.")
    p_workflow_contract.add_argument("--state", help="Optional workflow state JSON containing artifacts/gates to audit.")
    p_workflow_contract.add_argument("--production-engine", default="same-layer-svg-engine")
    p_workflow_contract.add_argument("--opencrab-homepage", default=OPENCRAB_HOMEPAGE)
    p_workflow_contract.set_defaults(func=command_workflow_contract)

    p_mcp_manifest = sub.add_parser("mcp-manifest", help="Print or write the MCP/OAuth exec tool manifest.")
    p_mcp_manifest.add_argument("--output", help="Optional path for the tool manifest JSON. Defaults to stdout.")
    p_mcp_manifest.set_defaults(func=command_mcp_manifest)

    p_mcp_config = sub.add_parser("mcp-config", help="Print or write MCP client and OAuth worker runtime configuration.")
    p_mcp_config.add_argument("--output", help="Optional path for runtime config JSON. Defaults to stdout.")
    p_mcp_config.add_argument("--server-name", default="crab-archi-design")
    p_mcp_config.add_argument("--project-root", dest="config_project_root", default=str(DEFAULT_PROJECT_ROOT), help="Default project root for MCP tool calls.")
    p_mcp_config.add_argument("--cwd", help="Working directory for the MCP server process. Defaults to repository root.")
    p_mcp_config.set_defaults(func=command_mcp_config)

    p_mcp_smoke = sub.add_parser("mcp-smoke", help="Verify a stdio MCP server config with initialize and tools/list.")
    p_mcp_smoke.add_argument("--config", help="Runtime config JSON from mcp-config. Defaults to an in-memory config.")
    p_mcp_smoke.add_argument("--server-name", default="crab-archi-design")
    p_mcp_smoke.add_argument("--project-root", dest="config_project_root", default=str(DEFAULT_PROJECT_ROOT), help="Default project root when building an in-memory config.")
    p_mcp_smoke.add_argument("--cwd", help="Working directory when building an in-memory config. Defaults to repository root.")
    p_mcp_smoke.add_argument("--output-dir", help="Directory for mcp_smoke_report_###.json. Defaults to diagnostics.")
    p_mcp_smoke.add_argument("--timeout", type=int, default=30)
    p_mcp_smoke.add_argument("--strict", action="store_true")
    p_mcp_smoke.set_defaults(func=command_mcp_smoke)

    p_qa = sub.add_parser("qa", help="Run lightweight framework QA.")
    p_qa.add_argument("--project-id", required=True)
    p_qa.add_argument("--allow-missing-source", action="store_true")
    p_qa.set_defaults(func=command_qa)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
