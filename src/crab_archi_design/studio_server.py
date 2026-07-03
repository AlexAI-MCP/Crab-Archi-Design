"""Localhost design studio for Crab Archi Design.

Serves a browser UI where a designer or an agent operator specifies, on top of
the original drawing linework: protected geometry (community shell, no-go,
lock), space adjustments (mutable/projectable zones, program expansion),
wall adjustments (openings, partition removals), and the natural-language
request. The server converts those annotations into the framework's constraint
and edit sketch JSON, then executes the tested CLI workflow
(`workflow-run` / `revision-run`) with the same-layer production engine.

The server binds to 127.0.0.1 only and is dependency-free (stdlib http.server).
Artifact reads are restricted to the configured project root plus the source
SVG paths registered during this session.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

STUDIO_SCHEMA = "crab-archi-design-studio-run-v1"
SKETCH_SCHEMA = "crab-archi-design-vector-sketch-layer-v1"

CONSTRAINT_MODES = {
    "community_shell",
    "no_go_zone",
    "lock_boundary",
    "protect_zone",
    "mutable_zone",
    "projectable_zone",
}
EDIT_MODES = {
    "open_connection",
    "remove_partition",
    "move_wall",
    "expand_program_feel",
    "mark_up",
}


def studio_html_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[2] / "tools" / "studio.html",
        Path.cwd() / "tools" / "studio.html",
    ]
    return next((path for path in candidates if path.exists()), None)


def cli_command() -> list[str]:
    cli = Path(__file__).resolve().parent / "cli.py"
    return [sys.executable, str(cli)]


class StudioState:
    def __init__(self, project_root: Path, repo_root: Path) -> None:
        self.project_root = project_root
        self.repo_root = repo_root
        self.registered_sources: set[Path] = set()
        self.lock = threading.Lock()

    def register_source(self, path: Path) -> None:
        with self.lock:
            self.registered_sources.add(path.resolve())

    def path_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        with self.lock:
            if resolved in self.registered_sources:
                return True
        for base in (self.project_root.resolve(), self.repo_root.resolve()):
            try:
                resolved.relative_to(base)
                return True
            except ValueError:
                continue
        return False


def split_strokes(strokes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    constraints: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, stroke in enumerate(strokes or [], start=1):
        mode = str(stroke.get("mode") or "").strip()
        points = stroke.get("points") or []
        if len(points) < 2:
            warnings.append(f"stroke {index} ({mode or 'unknown'}) skipped: fewer than 2 points")
            continue
        record = {
            "stroke_id": stroke.get("stroke_id") or f"studio_{index:03d}",
            "mode": mode,
            "target_hint": stroke.get("target_hint") or stroke.get("label") or mode,
            "points": [[float(p[0]), float(p[1])] for p in points],
        }
        if mode in CONSTRAINT_MODES:
            constraints.append(record)
        elif mode in EDIT_MODES:
            edits.append(record)
        else:
            warnings.append(f"stroke {index} skipped: unsupported mode {mode!r}")
    return constraints, edits, warnings


def scale_polygon_about_centroid(points: list[list[float]], factor: float) -> list[list[float]]:
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return [[round(cx + (p[0] - cx) * factor, 2), round(cy + (p[1] - cy) * factor, 2)] for p in points]


def synthesize_default_constraints(
    constraints: list[dict[str, Any]], viewbox: list[float]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Shell-first defaults: the shell interior is editable, everything else is protected.

    The operator draws (at most) the community shell. The mutable zone is derived
    as the shell interior (slightly inset), and the ring between the drawing frame
    and the shell becomes no-go — so topology and assets inside the shell are the
    only mutation targets. With no shell at all, the whole drawing frame is used.
    """
    modes = {item["mode"] for item in constraints}
    have_shell = "community_shell" in modes
    have_mutable = bool(modes & {"mutable_zone", "projectable_zone"})
    have_protected = bool(modes & {"no_go_zone", "lock_boundary", "protect_zone"})
    if have_shell and have_mutable and have_protected:
        return [], []

    x, y, w, h = (float(v) for v in viewbox)
    x0, y0, x1, y1 = x, y, x + w, y + h
    added: list[dict[str, Any]] = []
    notes: list[str] = []

    def rect(px0: float, py0: float, px1: float, py1: float) -> list[list[float]]:
        return [[px0, py0], [px1, py0], [px1, py1], [px0, py1], [px0, py0]]

    shell_points: list[list[float]] | None = None
    for item in constraints:
        if item["mode"] == "community_shell" and len(item.get("points") or []) >= 4:
            shell_points = [[float(p[0]), float(p[1])] for p in item["points"]]
            break

    if not have_shell:
        shell_points = rect(x0, y0, x1, y1)
        added.append({"stroke_id": "auto_shell", "mode": "community_shell",
                      "target_hint": "studio_auto_default_shell", "points": shell_points})
        notes.append("외곽 쉘 미지정 → 도면 전체 범위를 쉘로 사용했습니다 (쉘을 직접 그리면 편집 범위가 정확해집니다).")

    if not have_mutable and shell_points:
        interior = scale_polygon_about_centroid(shell_points, 0.96)
        added.append({"stroke_id": "auto_mutable", "mode": "mutable_zone",
                      "target_hint": "studio_interior_mutable", "points": interior})
        notes.append("쉘 내부가 편집 가능 영역으로 설정되었습니다 — 이 안의 토폴로지와 요소만 변형 대상입니다.")

    if not have_protected and shell_points:
        xs = [p[0] for p in shell_points]
        ys = [p[1] for p in shell_points]
        bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
        strips = [
            ("top", rect(x0, y0, x1, by0)) if by0 > y0 else None,
            ("bottom", rect(x0, by1, x1, y1)) if by1 < y1 else None,
            ("left", rect(x0, by0, bx0, by1)) if bx0 > x0 else None,
            ("right", rect(bx1, by0, x1, by1)) if bx1 < x1 else None,
        ]
        strips = [item for item in strips if item]
        if not strips:
            edge = 0.04 * min(w, h)
            strips = [
                ("top", rect(x0, y0, x1, y0 + edge)),
                ("bottom", rect(x0, y1 - edge, x1, y1)),
                ("left", rect(x0, y0 + edge, x0 + edge, y1 - edge)),
                ("right", rect(x1 - edge, y0 + edge, x1, y1 - edge)),
            ]
        for side, pts in strips:
            added.append({"stroke_id": f"auto_nogo_{side}", "mode": "no_go_zone",
                          "target_hint": "studio_border_no_go", "points": pts})
        notes.append("쉘 바깥 영역은 자동으로 보호(금지) 구역으로 잠갔습니다.")
    return added, notes


def write_sketch(path: Path, strokes: list[dict[str, Any]], viewbox: list[float] | None) -> None:
    payload: dict[str, Any] = {
        "schema": SKETCH_SCHEMA,
        "coordinate_space": "source_svg_viewbox",
        "strokes": strokes,
    }
    if viewbox:
        payload["viewBox"] = viewbox
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def next_studio_sequence(studio_dir: Path) -> int:
    studio_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(studio_dir.glob("studio_run_*"))
    return len(existing) + 1


def parse_cli_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed([line for line in stdout.splitlines() if line.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def run_pipeline(state: StudioState, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id or any(ch in project_id for ch in "/\\.."):
        return {"status": "error", "error": "project_id is required and must be a plain name."}
    prompt = str(payload.get("prompt") or "").strip()
    strokes = payload.get("strokes") or []
    constraints, edits, warnings = split_strokes(strokes)

    root = state.project_root
    project_dir = root / project_id
    manifest_exists = (project_dir / "project_manifest.json").exists()

    source_svg = str(payload.get("source_svg") or "").strip()
    if not manifest_exists and not source_svg:
        return {"status": "error", "error": "source_svg is required for a new project."}
    if source_svg:
        source_path = Path(source_svg).expanduser()
        if not source_path.exists():
            return {"status": "error", "error": f"source_svg not found: {source_svg}"}
        state.register_source(source_path)

    studio_dir = project_dir / "studio"
    seq = next_studio_sequence(studio_dir)
    run_dir = studio_dir / f"studio_run_{seq:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    viewbox = payload.get("viewBox") if isinstance(payload.get("viewBox"), list) else None
    constraint_manifest_exists = (project_dir / "constraints" / "constraint_manifest.json").exists()
    if viewbox and len(viewbox) == 4 and (constraints or not constraint_manifest_exists):
        defaults, default_notes = synthesize_default_constraints(constraints, viewbox)
        constraints.extend(defaults)
        warnings.extend(default_notes)
    constraint_path: Path | None = None
    edit_path: Path | None = None
    if constraints:
        constraint_path = run_dir / "constraint_sketch.json"
        write_sketch(constraint_path, constraints, viewbox)
    if edits:
        edit_path = run_dir / "edit_sketch.json"
        write_sketch(edit_path, edits, viewbox)

    engine = str(payload.get("engine_adapter") or "same-layer-svg-engine")
    engine_args = payload.get("engine_args")
    if not isinstance(engine_args, list):
        engine_args = ["--apply-program-relabels", "--apply-openings", "--apply-endpoint-moves"]

    use_revision = manifest_exists and not payload.get("reinit")
    cmd = [*cli_command(), "--project-root", str(root)]
    if use_revision:
        cmd += ["revision-run", "--project-id", project_id]
        if prompt:
            cmd += ["--text", prompt]
        if edit_path:
            cmd += ["--sketch", str(edit_path)]
        if constraint_path:
            cmd += ["--constraint-sketch", str(constraint_path), "--replace-constraints"]
    else:
        cmd += ["workflow-run", "--project-id", project_id]
        if source_svg:
            cmd += ["--source-svg", source_svg]
        if payload.get("households"):
            cmd += ["--households", str(int(payload["households"]))]
        for standards in payload.get("standards") or []:
            cmd += ["--standards", str(standards)]
        if payload.get("ontology_pack"):
            cmd += ["--ontology-pack", str(payload["ontology_pack"])]
        for result_file in payload.get("opencrab_result_files") or []:
            cmd += ["--opencrab-result-file", str(result_file)]
        if payload.get("evidence_summary"):
            cmd += ["--evidence-summary", str(payload["evidence_summary"])]
        if constraint_path:
            cmd += ["--constraint-sketch", str(constraint_path)]
        if edit_path:
            cmd += ["--sketch", str(edit_path)]
        if prompt:
            cmd += ["--prompt", prompt]
        scale = payload.get("scale") or {}
        if scale.get("mm_per_world"):
            cmd += ["--scale-mm-per-world", str(float(scale["mm_per_world"]))]
        elif scale.get("known_mm") and scale.get("known_world_length"):
            cmd += [
                "--scale-known-mm",
                str(float(scale["known_mm"])),
                "--scale-known-world-length",
                str(float(scale["known_world_length"])),
            ]
        if scale.get("evidence"):
            cmd += ["--scale-evidence", str(scale["evidence"])]
    cmd += ["--engine-adapter", engine]
    for arg in engine_args:
        cmd.append(f"--engine-arg={arg}")
    cmd += ["--skip-preview"]

    completed = subprocess.run(cmd, capture_output=True, text=True, cwd=str(state.repo_root), timeout=1800)
    result = parse_cli_json(completed.stdout) or {}
    blocking = collect_blocking_issues(project_dir, result)
    report = {
        "schema": STUDIO_SCHEMA,
        "project_id": project_id,
        "command_kind": "revision-run" if use_revision else "workflow-run",
        "returncode": completed.returncode,
        "status": result.get("status") or ("error" if completed.returncode != 0 else "unknown"),
        "result": result,
        "blocking": blocking,
        "warnings": warnings,
        "constraint_sketch": str(constraint_path) if constraint_path else None,
        "edit_sketch": str(edit_path) if edit_path else None,
        "stderr_tail": completed.stderr.strip().splitlines()[-5:] if completed.stderr.strip() else [],
    }
    (run_dir / "studio_run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


GATE_HINTS = {
    "community_shell_confirmed": "외곽 쉘 폴리곤이 없습니다 — 2단계에서 '외곽 쉘'로 커뮤니티 경계를 그려주세요.",
    "protected_zone_confirmed": "보호 구역이 없습니다 — 2단계에서 '금지 구역'(주차·코어·기둥·램프) 폴리곤을 그려주세요.",
    "mutable_zone_confirmed": "가변 구역이 없습니다 — 3단계에서 '가변 구역' 폴리곤을 그려야 그 안의 벽이 변형 후보가 됩니다.",
    "mutable_zone_found": "가변 구역이 없습니다 — 3단계에서 '가변 구역' 폴리곤을 그려야 그 안의 벽이 변형 후보가 됩니다.",
    "protected_zone_found": "보호 구역이 없습니다 — 2단계에서 '금지 구역'(주차·코어·기둥·램프) 폴리곤을 그려주세요.",
    "same_layer_mutable_candidates_found": "가변 구역 안에서 변형 가능한 벽을 찾지 못했습니다 — 가변 구역이 내부 칸막이를 포함하도록 조정하세요.",
    "program_anchors_found": "가변 구역 안에 프로그램 라벨이 없습니다 — 라벨이 있는 실을 포함하도록 가변 구역을 넓히세요.",
    "recognition_audit_pass": "인식 감사가 통과되지 않았습니다 — 위 항목을 먼저 해결하세요.",
    "standards_manifest_active": "면적 기준 파일이 없습니다 — 1단계에서 CSV 경로를 지정하세요.",
    "constraint_manifest_active": "제약 매니페스트가 없습니다 — 2·3단계 폴리곤을 최소 1개 이상 그려주세요.",
    "column_candidates_detected": "기둥 후보가 인식되지 않았습니다 — 도면 스케일과 기둥 표기를 확인하세요.",
}


def collect_blocking_issues(project_dir: Path, result: dict[str, Any]) -> list[str]:
    """Read the latest audit/patch-plan/apply artifacts and explain what blocked a pass."""
    issues: list[str] = []
    seen: set[str] = set()

    def add_gate(key: str, source: str) -> None:
        message = GATE_HINTS.get(key, f"{source}: {key}")
        if message not in seen:
            seen.add(message)
            issues.append(message)

    def load_latest(pattern: str) -> dict[str, Any] | None:
        candidates = sorted(project_dir.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name))
        if not candidates:
            return None
        try:
            return json.loads(candidates[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    if result.get("status") in (None, "pass"):
        return issues
    audit = load_latest("audits/recognition_audit_*.json")
    if audit and audit.get("status") != "pass":
        gates = audit.get("gates", {})
        for key in audit.get("hard_gate_keys", gates.keys()):
            if not gates.get(key):
                add_gate(key, "recognition-audit")
    plan = load_latest("patch_plans/svg_patch_plan_*.json")
    if plan and plan.get("status") != "pass":
        for key, value in plan.get("gates", {}).items():
            if not value:
                add_gate(key, "svg-patch-plan")
    apply_report = load_latest("runs/apply_edit_*/apply_edit_report.json")
    if apply_report and apply_report.get("status") != "pass":
        for key, value in apply_report.get("checks", {}).items():
            if not value and key in GATE_HINTS:
                add_gate(key, "apply-edit")
    return issues


def make_handler(state: StudioState) -> type[BaseHTTPRequestHandler]:
    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "CrabArchiStudio/0.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            sys.stderr.write("[studio] " + (format % args) + "\n")

        def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return value if isinstance(value, dict) else {}

        def query(self) -> dict[str, str]:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(self.path)
            return {key: values[0] for key, values in parse_qs(parsed.query).items()}

        def route(self) -> str:
            from urllib.parse import urlparse

            return urlparse(self.path).path

        def do_GET(self) -> None:  # noqa: N802
            route = self.route()
            if route in {"/", "/index.html"}:
                page = studio_html_path()
                if page is None:
                    self.send_json({"status": "error", "error": "tools/studio.html not found"}, 404)
                    return
                self.send_bytes(page.read_bytes(), "text/html; charset=utf-8")
                return
            if route == "/api/health":
                self.send_json(
                    {
                        "status": "ok",
                        "schema": "crab-archi-design-studio-health-v1",
                        "project_root": str(state.project_root),
                        "constraint_modes": sorted(CONSTRAINT_MODES),
                        "edit_modes": sorted(EDIT_MODES),
                    }
                )
                return
            if route == "/api/artifact":
                raw = self.query().get("path", "")
                if not raw:
                    self.send_json({"status": "error", "error": "path query is required"}, 400)
                    return
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = state.repo_root / path
                if not state.path_allowed(path) or not path.is_file():
                    self.send_json({"status": "error", "error": "path is outside the allowed roots"}, 403)
                    return
                suffix = path.suffix.lower()
                content_type = {
                    ".svg": "image/svg+xml; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                    ".html": "text/html; charset=utf-8",
                    ".md": "text/plain; charset=utf-8",
                }.get(suffix, "application/octet-stream")
                self.send_bytes(path.read_bytes(), content_type)
                return
            if route == "/api/status":
                project_id = self.query().get("project_id", "")
                status_path = state.project_root / project_id / "status" / "project_status.json"
                if not project_id or not status_path.exists():
                    self.send_json({"status": "error", "error": "project status not found"}, 404)
                    return
                self.send_json(json.loads(status_path.read_text(encoding="utf-8")))
                return
            self.send_json({"status": "error", "error": f"unknown route {route}"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            route = self.route()
            if route == "/api/load-svg":
                payload = self.read_body()
                raw = str(payload.get("path") or "").strip()
                path = Path(raw).expanduser()
                if not raw or not path.is_file() or path.suffix.lower() != ".svg":
                    self.send_json({"status": "error", "error": f"SVG file not found: {raw}"}, 404)
                    return
                state.register_source(path)
                self.send_json(
                    {
                        "status": "ok",
                        "path": str(path.resolve()),
                        "content": path.read_text(encoding="utf-8", errors="replace"),
                    }
                )
                return
            if route == "/api/run":
                payload = self.read_body()
                try:
                    report = run_pipeline(state, payload)
                except subprocess.TimeoutExpired:
                    self.send_json({"status": "error", "error": "pipeline timed out"}, 500)
                    return
                self.send_json(report, 200 if report.get("status") != "error" else 400)
                return
            self.send_json({"status": "error", "error": f"unknown route {route}"}, 404)

    return StudioHandler


def serve(project_root: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> ThreadingHTTPServer:
    repo_root = Path(__file__).resolve().parents[2]
    state = StudioState(project_root=project_root.resolve(), repo_root=repo_root)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    url = f"http://{host}:{server.server_address[1]}/"
    print(url)
    if open_browser:
        webbrowser.open(url)
    return server


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Crab Archi Design localhost studio")
    parser.add_argument("--project-root", default="projects")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    server = serve(Path(args.project_root), args.host, args.port, args.open)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
