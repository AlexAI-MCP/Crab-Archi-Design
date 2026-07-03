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
    report = {
        "schema": STUDIO_SCHEMA,
        "project_id": project_id,
        "command_kind": "revision-run" if use_revision else "workflow-run",
        "returncode": completed.returncode,
        "status": result.get("status") or ("error" if completed.returncode != 0 else "unknown"),
        "result": result,
        "warnings": warnings,
        "constraint_sketch": str(constraint_path) if constraint_path else None,
        "edit_sketch": str(edit_path) if edit_path else None,
        "stderr_tail": completed.stderr.strip().splitlines()[-5:] if completed.stderr.strip() else [],
    }
    (run_dir / "studio_run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


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
