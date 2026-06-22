from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = Path("projects")
OPENCRAB_HOMEPAGE = "https://opencrab.sh"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "project"


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
            "constraints_dir": str(out_dir / "constraints"),
            "alternatives_dir": str(out_dir / "alternatives"),
            "edit_intents_dir": str(out_dir / "edit_intents"),
            "qa_dir": str(out_dir / "qa"),
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
        "source": {"type": "vector_sketch_layer", "path": str(sketch_path)},
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


def command_qa(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    checks = {
        "manifest_exists": True,
        "source_svg_exists_or_allowed_missing": Path(manifest["source_svg"]).exists() or args.allow_missing_source,
        "has_hard_constraints": bool(manifest.get("hard_constraints")),
        "opencrab_mcp_required": bool(manifest.get("opencrab_mcp", {}).get("required")),
        "opencrab_mcp_server_configured": bool(manifest.get("opencrab_mcp", {}).get("mcp_server")),
        "opencrab_ontology_pack_attached": bool(manifest.get("opencrab_mcp", {}).get("ontology_pack")),
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
