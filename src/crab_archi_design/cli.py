from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
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
            "runs_dir": str(out_dir / "runs"),
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


def sorted_json_files(path: Path, pattern: str) -> list[Path]:
    return sorted(path.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name))


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


def build_engine_command(adapter: str, extra_args: list[str]) -> tuple[list[str], Path | None]:
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
    except ET.ParseError as exc:
        return {"xml_parse": "error", "error": str(exc), "image_elements": None}
    return {
        "xml_parse": "ok",
        "viewBox": root.attrib.get("viewBox"),
        "image_elements": sum(1 for el in root.iter() if local_tag(el) == "image"),
        "text_elements": sum(1 for el in root.iter() if local_tag(el) == "text"),
    }


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


def command_apply_edit(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    intent_paths = resolve_intent_paths(base, args.intent)
    intents = [read_json(path) for path in intent_paths]

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
    command, adapter_path = build_engine_command(adapter, extra_args)
    engine_cwd = Path(args.engine_cwd).expanduser() if args.engine_cwd else infer_engine_cwd(adapter_path or Path(adapter), Path.cwd())
    env = os.environ.copy()
    env.update(
        {
            "CRAB_ARCHI_PROJECT_ID": args.project_id,
            "CRAB_ARCHI_PROJECT_ROOT": str(root),
            "CRAB_ARCHI_PROJECT_DIR": str(base),
            "CRAB_ARCHI_RUN_DIR": str(run_dir),
            "CRAB_ARCHI_SOLVER_INPUT": str(solver_input_path),
            "CRAB_ARCHI_SOURCE_SVG": manifest.get("source_svg", ""),
            "CRAB_ARCHI_INTENTS": os.pathsep.join(str(path) for path in intent_paths),
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
    checks = {
        "engine_returncode_zero": proc.returncode == 0,
        "candidate_svg_exists": alternative_svg is not None and alternative_svg.exists(),
        "native_svg_no_images": svg_info.get("xml_parse") == "ok" and svg_info.get("image_elements") == 0,
        "opencrab_mcp_required": bool(manifest.get("opencrab_mcp", {}).get("required")),
        "intent_count_positive": len(intent_paths) > 0,
    }
    report = {
        "schema": "crab-archi-design-apply-edit-report-v1",
        "created_at": now(),
        "project_id": args.project_id,
        "run_dir": str(run_dir),
        "solver_input": str(solver_input_path),
        "intent_paths": [str(path) for path in intent_paths],
        "engine": engine_result,
        "discovered_outputs": {key: [str(path) for path in paths] for key, paths in discovered.items()},
        "copied_artifacts": copied,
        "svg_inspection": svg_info,
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

    p_apply = sub.add_parser("apply-edit", help="Run an engine adapter from structured edit intent and collect a native SVG alternative.")
    p_apply.add_argument("--project-id", required=True)
    p_apply.add_argument("--intent", default="latest", help="latest, all, or a project-relative/absolute intent JSON path.")
    p_apply.add_argument("--engine-adapter", help="Override the manifest engine adapter.")
    p_apply.add_argument("--engine-cwd", help="Working directory for the engine adapter.")
    p_apply.add_argument("--engine-arg", action="append", default=[], help="Additional argument passed to the engine adapter. Repeatable.")
    p_apply.add_argument("--candidate-svg", help="Explicit SVG candidate path if the engine does not print/report it.")
    p_apply.add_argument("--candidate-report", help="Explicit report path to copy into the run directory.")
    p_apply.add_argument("--preview", help="Explicit preview image path to copy into the run directory.")
    p_apply.add_argument("--skip-preview", action="store_true", help="Pass --skip-preview to adapters that support it.")
    p_apply.add_argument("--timeout", type=int, default=300)
    p_apply.set_defaults(func=command_apply_edit)

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
