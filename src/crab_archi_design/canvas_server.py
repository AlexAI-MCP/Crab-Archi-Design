"""Localhost live-canvas server for the Crab Archi Design SVG CAD studio.

Single shared :class:`CanvasDocument` is the source of truth. The browser UI
(``tools/canvas.html``) and the MCP bridge (``canvas_mcp``) both talk to this
server, so a human sketching in the browser and an agent editing over MCP see
the same drawing in real time (the browser polls the revision counter).

Binds to 127.0.0.1 only. Stdlib only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from crab_archi_design.canvas_document import (
    CanvasDocument,
    CanvasError,
    ZONE_MODES,
)

DEFAULT_PORT = 8770

# Ops an HTTP client may invoke on the document (op name -> mutating).
ALLOWED_OPS = {
    "draw_line", "draw_polyline", "draw_path", "draw_curve", "draw_rect",
    "draw_ellipse", "add_text", "delete_elements", "move_element",
    "copy_element", "copy_style", "set_style", "set_attrs",
    "set_zone", "clear_zones",
}


def clean_path(raw: str) -> str:
    """Tolerate shell-escaped paths pasted from a terminal (\\~, \\space, quotes)."""
    text = raw.strip().strip("'\"")
    if "\\" in text:
        text = re.sub(r"\\(.)", r"\1", text)
    return text


def pick_file_dialog() -> str | None:
    """Open a native file picker (macOS Finder) and return the chosen path."""
    if sys.platform != "darwin":
        raise CanvasError("native file picker is only supported on macOS")
    script = 'POSIX path of (choose file with prompt "SVG 도면 선택" of type {"public.svg-image", "svg"})'
    try:
        completed = subprocess.run(["osascript", "-e", script],
                                   capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None
    if completed.returncode != 0:
        return None  # user cancelled
    path = completed.stdout.strip()
    return path or None


def canvas_html_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[2] / "tools" / "canvas.html",
        Path.cwd() / "tools" / "canvas.html",
    ]
    return next((path for path in candidates if path.exists()), None)


class CanvasState:
    def __init__(self, project_root: Path) -> None:
        self.document = CanvasDocument()
        self.project_root = project_root
        self.repo_root = Path(__file__).resolve().parents[2]
        self.regen_lock = threading.Lock()

    def regenerate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Save the canvas, run the deterministic pipeline, load the alternative back."""
        from crab_archi_design.studio_server import StudioState, run_pipeline

        doc = self.document
        status = doc.status()
        if not status.get("loaded"):
            raise CanvasError("no SVG loaded")
        project_id = str(payload.get("project_id") or "canvas-session").strip()
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise CanvasError("prompt is required for regeneration")

        with self.regen_lock:
            work_dir = self.project_root / project_id / "canvas"
            work_dir.mkdir(parents=True, exist_ok=True)
            source_path = work_dir / "canvas_source.svg"
            doc.save(str(source_path))

            studio_payload: dict[str, Any] = {
                "project_id": project_id,
                "prompt": prompt,
                "source_svg": str(source_path),
                "strokes": doc.zones_as_strokes(),
                "viewBox": doc.viewbox(),
                "engine_adapter": payload.get("engine_adapter") or "layout-svg-engine",
                "reinit": bool(payload.get("reinit")),
            }
            for key in ("households", "standards", "ontology_pack", "opencrab_result_files",
                        "evidence_summary", "scale", "engine_args"):
                if payload.get(key) is not None:
                    studio_payload[key] = payload[key]

            studio_state = StudioState(project_root=self.project_root.resolve(), repo_root=self.repo_root)
            report = run_pipeline(studio_state, studio_payload)

            alternative = (report.get("result", {}).get("latest_artifacts") or {}).get("alternative_svg")
            if alternative and Path(alternative).is_file() and payload.get("load_result", True):
                doc.load(alternative)
                report["loaded_into_canvas"] = True
            return report


def make_handler(state: CanvasState) -> type[BaseHTTPRequestHandler]:
    class CanvasHandler(BaseHTTPRequestHandler):
        server_version = "CrabArchiCanvas/0.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            sys.stderr.write("[canvas] " + (format % args) + "\n")

        def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
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
            parsed = urlparse(self.path)
            return {key: values[0] for key, values in parse_qs(parsed.query).items()}

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            doc = state.document
            try:
                if route in {"/", "/index.html"}:
                    page = canvas_html_path()
                    if page is None:
                        self.send_json({"status": "error", "error": "tools/canvas.html not found"}, 404)
                        return
                    body = page.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif route == "/api/health":
                    self.send_json({"status": "ok", "schema": "crab-archi-design-canvas-health-v1",
                                    "zone_modes": sorted(ZONE_MODES), "ops": sorted(ALLOWED_OPS),
                                    **doc.status()})
                elif route == "/api/doc":
                    since = int(self.query().get("rev", -1))
                    status = doc.status()
                    if not status.get("loaded"):
                        self.send_json({"status": "empty", **status})
                    elif status["revision"] == since:
                        self.send_json({"status": "unchanged", "revision": since})
                    else:
                        with doc.lock:
                            self.send_json({"status": "ok", **doc.status(), "svg": doc.to_string()})
                elif route == "/api/elements":
                    params = self.query()
                    self.send_json({"status": "ok",
                                    "elements": doc.list_elements(
                                        tag=params.get("tag") or None,
                                        max_results=int(params.get("max_results", 200)))})
                elif route == "/api/zones":
                    self.send_json({"status": "ok", "zones": doc.list_zones()})
                else:
                    self.send_json({"status": "error", "error": f"unknown route {route}"}, 404)
            except CanvasError as exc:
                self.send_json({"status": "error", "error": str(exc)}, 400)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            payload = self.read_body()
            doc = state.document
            try:
                if route == "/api/open":
                    self.send_json({"status": "ok", **doc.load(clean_path(str(payload.get("path") or "")))})
                elif route == "/api/pick-file":
                    picked = pick_file_dialog()
                    if picked is None:
                        self.send_json({"status": "cancelled"})
                    else:
                        self.send_json({"status": "ok", **doc.load(picked), "path": picked})
                elif route == "/api/open-text":
                    self.send_json({"status": "ok", **doc.load_text(str(payload.get("svg") or ""))})
                elif route == "/api/op":
                    op = str(payload.get("op") or "")
                    if op not in ALLOWED_OPS:
                        raise CanvasError(f"op not allowed: {op}")
                    params = payload.get("params") or {}
                    if not isinstance(params, dict):
                        raise CanvasError("params must be an object")
                    self.send_json({"status": "ok", **doc.apply(op, params)})
                elif route == "/api/undo":
                    self.send_json({"status": "ok", **doc.undo()})
                elif route == "/api/save":
                    raw_target = payload.get("path")
                    saved = doc.save(clean_path(str(raw_target)) if raw_target else None)
                    self.send_json({"status": "ok", "saved": saved})
                elif route == "/api/regenerate":
                    report = state.regenerate(payload)
                    self.send_json({"status": report.get("status", "unknown"), "report": report},
                                   200 if report.get("status") != "error" else 400)
                else:
                    self.send_json({"status": "error", "error": f"unknown route {route}"}, 404)
            except CanvasError as exc:
                self.send_json({"status": "error", "error": str(exc)}, 400)

    return CanvasHandler


def serve(project_root: Path, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
          open_browser: bool = False, svg: str | None = None) -> ThreadingHTTPServer:
    state = CanvasState(project_root=project_root.resolve())
    if svg:
        state.document.load(svg)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    url = f"http://{host}:{server.server_address[1]}/"
    print(url)
    if open_browser:
        webbrowser.open(url)
    return server


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Crab Archi Design live SVG canvas")
    parser.add_argument("--project-root", default="projects")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--svg", help="SVG file to load on startup")
    args = parser.parse_args()
    server = serve(Path(args.project_root), args.host, args.port, args.open, svg=args.svg)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
