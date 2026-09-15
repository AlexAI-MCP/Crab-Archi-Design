"""Per-browser CAD session, executed inside a Pyodide worker, without a server.

Only explicit document operations cross this boundary. No filesystem, subprocess,
credentials, shared state, or arbitrary Python execution is exposed to the UI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from defusedxml.ElementTree import fromstring

from crab_archi_design.canvas_document import CanvasDocument, CanvasError, local_name

MAX_SVG_BYTES = 32 * 1024 * 1024
EDIT_OPS = frozenset({
    "draw_line", "draw_polyline", "draw_path", "draw_curve", "draw_rect",
    "draw_ellipse", "add_text", "delete_elements", "move_element",
    "copy_element", "copy_style", "set_style", "set_attrs", "set_zone",
    "clear_zones", "stretch", "set_scale", "place_symbol", "set_length",
    "set_thickness", "set_area", "transform_elements", "array_elements",
    "auto_arrange_in_polyline", "populate_kids_zone",
})
BLOCKED_TAGS = {"script", "foreignobject", "iframe", "object", "embed",
                "animate", "animatemotion", "animatetransform", "set", "style"}


def validate_attribute(key: str, value: object) -> None:
    name = key.rsplit("}", 1)[-1].lower()
    text = str(value or "").strip()
    if name.startswith("on") or name in {"src", "srcdoc", "base"}:
        raise CanvasError(f"Active SVG attribute is not supported: {name}")
    if name == "href" and text and not text.startswith("#"):
        if not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+", text):
            raise CanvasError("SVG external references are not supported; embed or flatten them first")
    if name in {"style", "fill", "stroke", "filter", "clip-path", "mask", "cursor",
                "marker-start", "marker-mid", "marker-end"}:
        # Keep local paint servers; exclude CSS escapes and remote resource loads.
        if re.search(r"@|\\|/\*|expression\s*\(|javascript\s*:", text, re.I):
            raise CanvasError("Active SVG CSS is not supported")
        for target in re.findall(r"url\s*\((.*?)\)", text, re.I):
            if not target.strip(" \t\r\n\"'").startswith("#"):
                raise CanvasError("SVG CSS must reference local definitions only")


def validate_svg(text: str) -> None:
    if len(text.encode("utf-8")) > MAX_SVG_BYTES:
        raise CanvasError("SVG exceeds the 32 MB browser editing limit")
    root = fromstring(text)
    if local_name(root) != "svg":
        raise CanvasError("root element is not <svg>")
    for element in root.iter():
        if local_name(element).lower() in BLOCKED_TAGS:
            raise CanvasError("Active SVG/style blocks are not supported; export presentation attributes first")
        for key, value in element.attrib.items():
            validate_attribute(key, value)


def validate_values(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CanvasError("Geometry values must be finite")
    if isinstance(value, dict):
        for item in value.values():
            validate_values(item)
    elif isinstance(value, list):
        for item in value:
            validate_values(item)


class BrowserSession:
    def __init__(self) -> None:
        self.document = CanvasDocument()
        self.source_sha256 = ""
        self.messages: list[dict] = []
        self.selection: list[str] = []
        self.viewport: list[float] | None = None

    def request(self, route: str, body: dict | None = None) -> dict:
        try:
            return self._request(route, body or {})
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def _request(self, route: str, body: dict) -> dict:
        if not isinstance(body, dict):
            raise CanvasError("Request body must be an object")
        path = urlparse(route).path
        query = parse_qs(urlparse(route).query)
        doc = self.document
        if path == "/api/health":
            return {"status": "ok", "mode": "browser", "agent_connected": False,
                    "schema": "crab-browser-health-v1", **doc.status()}
        if path == "/api/doc":
            status = doc.status()
            if not status["loaded"]:
                return {"status": "empty", **status}
            if str(status["revision"]) == query.get("rev", [None])[0]:
                return {"status": "unchanged", "revision": status["revision"]}
            return {"status": "ok", **status, "svg": doc.to_string()}
        if path in {"/api/open-text", "/api/restore"}:
            svg = str(body.get("svg", ""))
            validate_svg(svg)
            name = re.sub(r"[^\w. -]", "_", str(body.get("name") or "drawing.svg"))[:160]
            result = doc.load_text(svg, source=Path(name))
            self.source_sha256 = hashlib.sha256(svg.encode()).hexdigest()
            self.messages.clear()
            self.selection.clear()
            self.viewport = None
            if path == "/api/restore":
                digest = str(body.get("source_sha256", ""))
                if re.fullmatch(r"[0-9a-f]{64}", digest):
                    self.source_sha256 = digest
                self.messages = [message for message in body.get("messages", [])[-100:]
                                 if isinstance(message, dict) and isinstance(message.get("text"), str)]
            return {"status": "ok", "path": name, **result}
        if path == "/api/session/browser":
            return {"status": "ok", "notices": [], "seq": 0}
        if path == "/api/session/update":
            self.selection = [str(cid) for cid in body.get("selection", [])][:100]
            self.viewport = body.get("viewport")
            return {"status": "ok"}
        doc.require_root()
        if path == "/api/op":
            op = body.get("op")
            if op not in EDIT_OPS:
                raise CanvasError(f"Unsupported browser operation: {op}")
            params = body.get("params", {})
            if not isinstance(params, dict):
                raise CanvasError("params must be an object")
            if params.get("force"):
                raise CanvasError("Public editing cannot bypass protected zones")
            validate_values(params)
            for attrs in (params.get("attrs", {}), params.get("style", {})):
                for key, value in attrs.items():
                    validate_attribute(key, value)
            if op == "array_elements" and not 1 <= int(params.get("count", 0)) <= 100:
                raise CanvasError("Array count must be between 1 and 100")
            return {"status": "ok", **doc.apply(op, params)}
        if path == "/api/undo":
            return {"status": "ok", **doc.undo()}
        if path == "/api/redo":
            return {"status": "ok", **doc.redo()}
        if path == "/api/zones":
            return {"status": "ok", "zones": doc.list_zones()}
        if path == "/api/measure":
            return {"status": "ok", "measurements": doc.measure(body.get("cids", []))}
        if path == "/api/save":
            return {"status": "ok", "svg": doc.to_string(),
                    "saved": doc.source_path.name if doc.source_path else "drawing.svg"}
        if path == "/api/message":
            text = str(body.get("text", "")).strip()
            if not text:
                raise CanvasError("Enter a design request first")
            if len(text) > 16000:
                raise CanvasError("Design request exceeds 16000 characters")
            self.messages.append({"text": text, "selection": list(self.selection),
                                  "viewport": self.viewport, "revision": doc.revision})
            self.messages = self.messages[-100:]
            return {"status": "ok", "recorded": True, "agent_connected": False,
                    "queued": len(self.messages)}
        if path == "/api/design-request":
            return {"status": "ok", "request": {
                "schema": "crab-archi-design-browser-handoff-v1",
                "coordinate_space": "source_svg", "source_file": str(doc.source_path),
                "source_sha256": self.source_sha256,
                "edited_svg_sha256": hashlib.sha256(doc.to_string().encode()).hexdigest(),
                "revision": doc.revision, "viewBox": doc.viewbox(),
                "scale_mm_per_world": doc.mm_per_unit(), "strokes": doc.zones_as_strokes(),
                "messages": self.messages, "agent_connected": False,
                "opencrab_evidence_required": True, "design_validation": "not_run",
            }}
        raise CanvasError(f"This operation requires the local Codex/MCP studio: {path}")


session = BrowserSession()


def dispatch(raw: str) -> str:
    payload = json.loads(raw)
    return json.dumps(session.request(payload["route"], payload.get("body")), ensure_ascii=False)
