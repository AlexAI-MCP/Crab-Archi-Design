from __future__ import annotations

import argparse
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
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
            "evidence_dir": str(out_dir / "evidence"),
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


def build_review_panel_html(context: dict[str, Any]) -> str:
    source_svg = context["source_svg_markup"]
    alternative_svg = context["alternative_svg_markup"]
    apply_checks = render_check_list(context["apply_checks"])
    engine_checks = render_check_list(context["engine_quality_gates"])
    intent_list = render_intent_list(context["intents"])
    evidence_list = render_evidence_list(context["evidence_manifest"])
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
        "evidence_manifest": load_evidence_manifest(args.project_id, root),
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


def command_apply_edit(args: argparse.Namespace) -> None:
    root = Path(args.project_root)
    manifest = load_manifest(args.project_id, root)
    base = project_dir(args.project_id, root)
    intent_paths = resolve_intent_paths(base, args.intent)
    intents = [read_json(path) for path in intent_paths]
    evidence_manifest = load_evidence_manifest(args.project_id, root)

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
        "evidence_manifest": evidence_manifest,
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
        "opencrab_evidence_verified": evidence_status(evidence_manifest) == "verified",
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
        "opencrab_evidence_verified": evidence_status(load_evidence_manifest(args.project_id, root)) == "verified",
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
