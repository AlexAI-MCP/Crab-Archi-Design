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
    "set_zone", "clear_zones", "stretch", "set_scale", "place_symbol",
}


def render_png(svg_text: str, bbox: list[float] | None, px: int = 1024) -> bytes:
    """Rasterize (a crop of) the document. Backends: rsvg-convert > cairosvg > qlmanage."""
    import shutil
    import tempfile

    if bbox:
        svg_text = re.sub(r'viewBox="[^"]*"',
                          f'viewBox="{bbox[0]} {bbox[1]} {bbox[2] - bbox[0]} {bbox[3] - bbox[1]}"',
                          svg_text, count=1)
        svg_text = re.sub(r'\s(width|height)="[^"]*"', "", svg_text, count=2)
    with tempfile.TemporaryDirectory() as tmp:
        svg_path = Path(tmp) / "view.svg"
        svg_path.write_text("<?xml version='1.0' encoding='utf-8'?>\n" + svg_text, encoding="utf-8")
        if shutil.which("rsvg-convert"):
            out = Path(tmp) / "view.png"
            subprocess.run(["rsvg-convert", "-w", str(px), "-o", str(out), str(svg_path)],
                           check=True, capture_output=True, timeout=120)
            return out.read_bytes()
        try:
            import cairosvg  # type: ignore
            return cairosvg.svg2png(url=str(svg_path), output_width=px)
        except Exception:  # noqa: BLE001 — missing native cairo lib etc.; try next backend
            pass
        if sys.platform == "darwin":
            subprocess.run(["qlmanage", "-t", "-s", str(px), "-o", tmp, str(svg_path)],
                           check=True, capture_output=True, timeout=120)
            out = Path(tmp) / "view.svg.png"
            if out.exists():
                return out.read_bytes()
    raise CanvasError("no SVG rasterizer available (install rsvg-convert or cairosvg)")


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
    # 서버는 GUI 앱이 아닌 백그라운드 프로세스라 activate 없이는
    # 다이얼로그가 포커스 없이 다른 창 뒤에 열린다
    script = ('tell me to activate\n'
              'POSIX path of (choose file with prompt "SVG 도면 선택" '
              'of type {"public.svg-image", "svg"})')
    try:
        completed = subprocess.run(["osascript", "-e", script],
                                   capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None
    if completed.returncode != 0:
        err = (completed.stderr or "").strip()
        if "-128" in err:  # user cancelled
            return None
        raise CanvasError(f"file dialog failed: {err or 'unknown osascript error'}")
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
        self.document.autosave_path = project_root / ".canvas_autosave.svg"
        # 브라우저↔에이전트 양방향 세션 채널
        self.session_lock = threading.Lock()
        self.selection: list[str] = []          # 브라우저에서 선택된 cid들
        self.viewport: list[float] | None = None  # 사용자가 보고 있는 viewBox
        self.inbox: list[dict[str, Any]] = []   # 사용자 → 에이전트 메시지
        self.notices: list[dict[str, Any]] = [] # 에이전트 → 브라우저 알림/포인터
        self.notice_seq = 0

    def push_notice(self, text: str, pointer: list[float] | None = None) -> int:
        with self.session_lock:
            self.notice_seq += 1
            self.notices.append({"seq": self.notice_seq, "text": text, "pointer": pointer})
            self.notices = self.notices[-50:]
            return self.notice_seq

    def try_restore_autosave(self) -> bool:
        path = self.document.autosave_path
        if path and path.is_file():
            try:
                self.document.load_text(path.read_text(encoding="utf-8"))
                return True
            except Exception:  # noqa: BLE001
                return False
        return False

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
            if len(body) > 8192 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                import gzip
                body = gzip.compress(body, compresslevel=5)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_bytes_raw(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
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
                                        max_results=int(params.get("max_results", 200)),
                                        drawn_only=str(params.get("drawn", "")).lower() in ("1", "true"),
                                        layer=params.get("layer") or None)})
                elif route == "/api/zones":
                    self.send_json({"status": "ok", "zones": doc.list_zones()})
                elif route == "/api/symbols":
                    from crab_archi_design.canvas_symbols import DISCIPLINES, catalog
                    self.send_json({"status": "ok", "disciplines": DISCIPLINES, "symbols": catalog()})
                elif route == "/api/layers":
                    self.send_json({"status": "ok", "layers": doc.list_layers()})
                elif route == "/api/session":
                    # 에이전트용: 브라우저 컨텍스트 + 사용자 메시지 (drain=1이면 인박스 비움)
                    drain = str(self.query().get("drain", "1")).lower() in ("1", "true")
                    with state.session_lock:
                        selection = list(state.selection)
                        viewport = state.viewport
                        messages = list(state.inbox)
                        if drain:
                            state.inbox.clear()
                    details = []
                    for cid in selection[:20]:
                        try:
                            details.append(doc.describe(doc.find(cid)))
                        except CanvasError:
                            continue
                    self.send_json({"status": "ok", "selection": details,
                                    "viewport": viewport, "user_messages": messages})
                elif route == "/api/session/browser":
                    # 브라우저용: 에이전트 알림/포인터 (since 이후만)
                    since = int(self.query().get("since", 0))
                    with state.session_lock:
                        fresh = [n for n in state.notices if n["seq"] > since]
                        seq = state.notice_seq
                    self.send_json({"status": "ok", "notices": fresh, "seq": seq})
                elif route == "/api/rooms":
                    params = self.query()
                    if params.get("x") and params.get("y"):
                        room = doc.recognize_room(float(params["x"]), float(params["y"]),
                                                  cell=float(params.get("cell", 3)),
                                                  max_span=float(params.get("max_span", 500)),
                                                  door_close=float(params.get("door_close", 12)))
                        self.send_json({"status": "ok", "room": room})
                    else:
                        rooms = doc.recognize_rooms(max_rooms=int(params.get("max_rooms", 40)),
                                                    cell=float(params.get("cell", 3)),
                                                    max_span=float(params.get("max_span", 500)),
                                                    door_close=float(params.get("door_close", 12)))
                        self.send_json({"status": "ok", "rooms": rooms})
                elif route == "/api/render":
                    params = self.query()
                    bbox = None
                    if params.get("bbox"):
                        bbox = [float(v) for v in params["bbox"].split(",")]
                        if len(bbox) != 4:
                            raise CanvasError("bbox must be x0,y0,x1,y1")
                    with doc.lock:
                        svg_text = doc.to_string()
                    png = render_png(svg_text, bbox, px=int(params.get("px", 1024)))
                    self.send_bytes_raw(png, "image/png")
                else:
                    self.send_json({"status": "error", "error": f"unknown route {route}"}, 404)
            except CanvasError as exc:
                self.send_json({"status": "error", "error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001 — rasterizer/parse failures
                self.send_json({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, 500)

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
                    # 브라우저 업로드 폴백: 파일명이 오면 uploads/ 아래를 저장 경로로 삼는다
                    name = re.sub(r"[^\w.\- ]", "_", str(payload.get("name") or "")).strip()
                    source = None
                    if name:
                        if not name.lower().endswith(".svg"):
                            name += ".svg"
                        source = state.project_root / "uploads" / name
                    result = doc.load_text(str(payload.get("svg") or ""), source=source)
                    extra = {"path": str(source)} if source else {}
                    self.send_json({"status": "ok", **result, **extra})
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
                elif route == "/api/session/update":
                    # 브라우저 → 선택/뷰포트 보고
                    with state.session_lock:
                        if isinstance(payload.get("selection"), list):
                            state.selection = [str(c) for c in payload["selection"]][:100]
                        if isinstance(payload.get("viewport"), list) and len(payload["viewport"]) == 4:
                            state.viewport = [float(v) for v in payload["viewport"]]
                    self.send_json({"status": "ok"})
                elif route == "/api/message":
                    # 브라우저 → 에이전트 메시지
                    text = str(payload.get("text") or "").strip()
                    if not text:
                        raise CanvasError("text is required")
                    import time
                    with state.session_lock:
                        state.inbox.append({"text": text, "ts": time.time(),
                                            "viewport": state.viewport,
                                            "selection": list(state.selection)})
                        state.inbox = state.inbox[-50:]
                    self.send_json({"status": "ok", "queued": len(state.inbox)})
                elif route == "/api/notify":
                    # 에이전트 → 브라우저 알림 (+선택적 포인터 좌표)
                    text = str(payload.get("text") or "").strip()
                    pointer = payload.get("pointer")
                    if pointer is not None and (not isinstance(pointer, list) or len(pointer) < 2):
                        raise CanvasError("pointer must be [x, y]")
                    seq = state.push_notice(text, [float(pointer[0]), float(pointer[1])] if pointer else None)
                    self.send_json({"status": "ok", "seq": seq})
                elif route == "/api/draft":
                    action = str(payload.get("action") or "")
                    handler = {"begin": doc.draft_begin, "commit": doc.draft_commit,
                               "rollback": doc.draft_rollback}.get(action)
                    if handler is None:
                        raise CanvasError("action must be begin | commit | rollback")
                    self.send_json({"status": "ok", **handler()})
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
    elif state.try_restore_autosave():
        print("[canvas] restored autosaved session", file=sys.stderr)
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
