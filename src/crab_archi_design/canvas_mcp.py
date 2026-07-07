"""Stdio MCP bridge for the live SVG canvas (Blender-MCP style).

Claude Code / Codex / Antigravity connect here over stdio; every tool call is
forwarded to the running ``crab-archi-design-canvas`` HTTP server, so the agent
edits exactly the drawing the user sees in the browser, live.

Register with e.g.::

    claude mcp add crab-canvas -- crab-archi-design-canvas-mcp --port 8770
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "crab-archi-design-canvas-mcp"

NUM = {"type": "number"}
STR = {"type": "string"}
POINTS = {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                                     "minItems": 2, "maxItems": 2}, "minItems": 2,
          "description": "[[x, y], ...] in SVG viewBox coordinates."}
STYLE = {"type": "object", "description": "SVG presentation attributes, e.g. "
         "{\"stroke\": \"#d62728\", \"stroke-width\": \"3\", \"fill\": \"none\"}."}
CIDS = {"type": "array", "items": STR, "description": "Element handles (data-cid) from list_elements."}

ZONE_MODE = {"type": "string", "enum": ["community_shell", "no_go_zone", "lock_boundary",
                                        "protect_zone", "mutable_zone", "projectable_zone"]}
LAYER = {"type": "string", "description": "Target layer name — use a discipline (arch/landscape/"
         "electrical/mechanical/fire/civil) or any name; omit for the default sketch layer."}


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


# name -> (description, kind, extra). kind: "get"/"post" route or "op".
TOOLS: dict[str, dict[str, Any]] = {
    "canvas_status": {
        "description": "Health/status of the live canvas: loaded file, revision, element and zone counts.",
        "kind": ("get", "/api/health"), "schema": schema({}, []), "readonly": True,
    },
    "open_svg": {
        "description": "Load an SVG file into the live canvas (replaces the current drawing; the browser view updates).",
        "kind": ("post", "/api/open"), "schema": schema({"path": STR}, ["path"]),
    },
    "get_canvas_svg": {
        "description": "Return the full current SVG markup of the canvas.",
        "kind": ("get", "/api/doc"), "schema": schema({}, []), "readonly": True,
    },
    "list_elements": {
        "description": "List recognized graphic elements (walls, labels, shapes) with stable cid handles, tag, bbox, style, and text. Use cids with the edit tools. Set drawn=true to see ONLY strokes drawn in this canvas session (by the user in the browser or via MCP) — check this first when the user says they sketched something.",
        "kind": ("get", "/api/elements"),
        "schema": schema({"tag": {**STR, "description": "Filter by SVG tag, e.g. 'path', 'text', 'g'."},
                          "max_results": {"type": "integer"},
                          "drawn": {"type": "boolean", "description": "Only session-drawn strokes and zones (crab_drawn/crab_zones/crab_layer_* layers)."},
                          "layer": LAYER}, []),
        "readonly": True,
    },
    "list_zones": {
        "description": "List annotation zones (community shell, protected, mutable...) drawn on the canvas.",
        "kind": ("get", "/api/zones"), "schema": schema({}, []), "readonly": True,
    },
    "draw_line": {
        "description": "Draw a straight line on the canvas.",
        "kind": ("op", "draw_line"),
        "schema": schema({"x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM, "style": STYLE, "layer": LAYER},
                         ["x1", "y1", "x2", "y2"]),
    },
    "draw_polyline": {
        "description": "Draw a polyline (or closed polygon) through the given points.",
        "kind": ("op", "draw_polyline"),
        "schema": schema({"points": POINTS, "closed": {"type": "boolean"}, "style": STYLE, "layer": LAYER}, ["points"]),
    },
    "draw_curve": {
        "description": "Draw a smooth curve through the given points (Catmull-Rom interpolation).",
        "kind": ("op", "draw_curve"), "schema": schema({"points": POINTS, "style": STYLE, "layer": LAYER}, ["points"]),
    },
    "draw_path": {
        "description": "Draw a raw SVG path (full path-d syntax for arcs/beziers).",
        "kind": ("op", "draw_path"), "schema": schema({"d": STR, "style": STYLE, "layer": LAYER}, ["d"]),
    },
    "draw_rect": {
        "description": "Draw a rectangle.",
        "kind": ("op", "draw_rect"),
        "schema": schema({"x": NUM, "y": NUM, "width": NUM, "height": NUM, "rx": NUM, "style": STYLE, "layer": LAYER},
                         ["x", "y", "width", "height"]),
    },
    "draw_ellipse": {
        "description": "Draw a circle (ry omitted) or ellipse.",
        "kind": ("op", "draw_ellipse"),
        "schema": schema({"cx": NUM, "cy": NUM, "rx": NUM, "ry": NUM, "style": STYLE, "layer": LAYER},
                         ["cx", "cy", "rx"]),
    },
    "add_text": {
        "description": "Add a text label at the given position.",
        "kind": ("op", "add_text"),
        "schema": schema({"x": NUM, "y": NUM, "text": STR, "style": STYLE, "layer": LAYER}, ["x", "y", "text"]),
    },
    "delete_elements": {
        "description": "Delete elements by cid (from list_elements). Undoable.",
        "kind": ("op", "delete_elements"), "schema": schema({"cids": CIDS}, ["cids"]),
    },
    "move_element": {
        "description": "Translate an element by (dx, dy).",
        "kind": ("op", "move_element"),
        "schema": schema({"cid": STR, "dx": NUM, "dy": NUM}, ["cid", "dx", "dy"]),
    },
    "copy_element": {
        "description": "Duplicate an element (optionally offset by dx/dy). Returns the new cid.",
        "kind": ("op", "copy_element"),
        "schema": schema({"cid": STR, "dx": NUM, "dy": NUM}, ["cid"]),
    },
    "copy_style": {
        "description": "Copy all presentation attributes (stroke, fill, width...) from one element onto others.",
        "kind": ("op", "copy_style"),
        "schema": schema({"source_cid": STR, "target_cids": CIDS}, ["source_cid", "target_cids"]),
    },
    "set_style": {
        "description": "Set presentation attributes on elements.",
        "kind": ("op", "set_style"), "schema": schema({"cids": CIDS, "style": STYLE}, ["cids", "style"]),
    },
    "set_attrs": {
        "description": "Set or remove (value null) raw attributes on one element — reshape geometry directly (e.g. x2/y2 of a line, d of a path).",
        "kind": ("op", "set_attrs"),
        "schema": schema({"cid": STR, "attrs": {"type": "object"}}, ["cid", "attrs"]),
    },
    "set_zone": {
        "description": "Annotate a polygon zone: community_shell (site boundary), no_go_zone/lock_boundary/protect_zone (do not touch), mutable_zone/projectable_zone (editable). Zones guide regeneration.",
        "kind": ("op", "set_zone"),
        "schema": schema({"mode": ZONE_MODE, "points": {**POINTS, "minItems": 3}, "name": STR},
                         ["mode", "points"]),
    },
    "clear_zones": {
        "description": "Remove zones (all, or only one mode).",
        "kind": ("op", "clear_zones"), "schema": schema({"mode": ZONE_MODE}, []),
    },
    "browser_state": {
        "description": "What the user is doing in the browser RIGHT NOW: selected elements (full descriptions — resolves '이거/이 선' references), the viewBox they are looking at (pass to render_view to see the same area), and queued messages they typed to you (drained on read). Check this FIRST whenever the user references something on screen.",
        "kind": ("get", "/api/session"),
        "schema": schema({"drain": {"type": "boolean",
                                    "description": "Consume queued user messages (default true)."}}, []),
        "readonly": True,
    },
    "notify_user": {
        "description": "Send a short notice to the user's browser (toast in the log panel), optionally with pointer=[x,y] to flash a blinking marker on the drawing — use it to say '여기' while explaining.",
        "kind": ("post", "/api/notify"),
        "schema": schema({"text": STR,
                          "pointer": {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2,
                                      "description": "Drawing coordinates to point at."}}, ["text"]),
    },
    "list_symbols": {
        "description": "Catalog of discipline symbols (건축·조경·전기·기계·소방·토목) available for place_symbol: doors, columns, trees, shrubs, lights, outlets, panels, diffusers, valves, sprinklers, detectors, manholes, catch basins, slope arrows...",
        "kind": ("get", "/api/symbols"), "schema": schema({}, []), "readonly": True,
    },
    "place_symbol": {
        "description": "Stamp a parametric discipline symbol at (x,y) with optional rotation/scale/label — e.g. tree_deciduous for 조경 식재, light_ceiling/outlet for 전기, diffuser_supply/valve for 기계, sprinkler for 소방, manhole/slope_arrow for 토목. Goes onto the discipline's layer automatically (override with layer).",
        "kind": ("op", "place_symbol"),
        "schema": schema({"name": {**STR, "description": "Symbol name from list_symbols."},
                          "x": NUM, "y": NUM,
                          "rotation": {**NUM, "description": "Degrees clockwise."},
                          "scale": NUM, "layer": LAYER,
                          "label": {**STR, "description": "Optional tag text beside the symbol (수종명, 회로번호 등)."}},
                         ["name", "x", "y"]),
    },
    "list_layers": {
        "description": "List canvas session layers (crab_drawn, crab_zones, discipline layers) with element counts. Combine with list_elements(layer=...) to inspect one discipline.",
        "kind": ("get", "/api/layers"), "schema": schema({}, []), "readonly": True,
    },
    "recognize_rooms": {
        "description": "Recognize rooms by flood-filling against bold wall geometry (door gaps up to door_close units auto-sealed). Pass x/y for the room around one point, or omit to scan every text label. Returns bbox and area (m²/평 when a scale is set via set_scale). THE tool for area checks against design standards. Note: open-plan spaces connected by wide openings merge into one region (enclosed=false) — that reflects real connectivity.",
        "kind": ("get", "/api/rooms"),
        "schema": schema({"x": NUM, "y": NUM,
                          "cell": {**NUM, "description": "Fill-grid cell size in drawing units (default 3)."},
                          "max_span": {**NUM, "description": "Search window half-size (default 500)."},
                          "door_close": {**NUM, "description": "Seal wall gaps up to this many units as doors (default 12 ≈ 1m)."},
                          "max_rooms": {"type": "integer"}}, []),
        "readonly": True,
    },
    "stretch_elements": {
        "description": "CAD-style stretch: geometry points inside box move by (dx,dy); points outside stay, so walls crossing the box boundary lengthen instead of moving. Works on lines, polylines, rects, paths (curve control points included) and transformed elements. Use to enlarge rooms or pull walls. box=[x0,y0,x1,y1] in world coordinates.",
        "kind": ("op", "stretch"),
        "schema": schema({"box": {"type": "array", "items": NUM, "minItems": 4, "maxItems": 4},
                          "dx": NUM, "dy": NUM, "cids": CIDS,
                          "force": {"type": "boolean", "description": "Bypass protected-zone check."}},
                         ["box", "dx", "dy"]),
    },
    "measure": {
        "description": "Measure elements in world space: length (m when a scale is set), closed-shape area (m²/평), stroke thickness (mm, inline CSS + transforms honored) and line angle (deg). Handles lines, polylines, polygons, rects, circles, ellipses and paths (curves flattened). Use for wall lengths/thicknesses, boundary perimeters, area takeoffs, orthogonality checks.",
        "kind": ("post", "/api/measure"),
        "schema": schema({"cids": CIDS}, ["cids"]),
        "readonly": True,
    },
    "set_length": {
        "description": "Give a line an exact real length (m/mm via the drawing scale, or raw units). anchor picks the fixed part: start|end|center — the rest moves along the line's direction. Use for '이 벽 3.5m로', trimming/extending walls to spec.",
        "kind": ("op", "set_length"),
        "schema": schema({"cid": {**STR, "description": "Target <line> cid."},
                          "m": {**NUM, "description": "Target length in meters."},
                          "mm": NUM, "units": NUM,
                          "anchor": {"type": "string", "enum": ["start", "end", "center"]},
                          "force": {"type": "boolean"}}, ["cid"]),
    },
    "set_thickness": {
        "description": "Set stroke thickness to an exact real size (mm via the drawing scale, or raw units) — e.g. 200mm 내력벽, 100mm 칸막이. Local stroke-width is compensated for each element's transform so the drawn thickness is exact; inline style CSS conflicts are cleaned automatically.",
        "kind": ("op", "set_thickness"),
        "schema": schema({"cids": CIDS, "mm": NUM, "units": NUM,
                          "force": {"type": "boolean"}}, ["cids"]),
    },
    "set_area": {
        "description": "Uniformly scale a closed shape (rect/circle/ellipse/polygon/closed path) about its center to an exact area — m2, pyeong(평) or raw units2. Use for '이 존 30평으로 맞춰줘' style adjustments; respects protected zones.",
        "kind": ("op", "set_area"),
        "schema": schema({"cid": STR, "m2": NUM, "pyeong": NUM, "units2": NUM,
                          "force": {"type": "boolean"}}, ["cid"]),
    },
    "set_scale": {
        "description": "Set the drawing scale: mm_per_unit directly, or calibrate with a known real distance (known_mm) between two drawing points p1/p2 (e.g. a 2500mm parking stall). Enables m²/평 output in recognize_rooms.",
        "kind": ("op", "set_scale"),
        "schema": schema({"mm_per_unit": NUM, "known_mm": NUM,
                          "p1": {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2},
                          "p2": {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2}}, []),
    },
    "draft": {
        "description": "Transaction control for risky multi-step edits: begin snapshots the document, commit keeps changes, rollback restores the snapshot (stronger than undo for bulk edits).",
        "kind": ("post", "/api/draft"),
        "schema": schema({"action": {"type": "string", "enum": ["begin", "commit", "rollback"]}}, ["action"]),
    },
    "render_view": {
        "description": "Rasterize the canvas (or a bbox crop) to a PNG image so you can SEE the drawing. Use after edits to verify visually. bbox=[x0,y0,x1,y1] in drawing units.",
        "kind": ("render", "/api/render"),
        "schema": schema({"bbox": {"type": "array", "items": NUM, "minItems": 4, "maxItems": 4},
                          "px": {"type": "integer", "description": "Output width in pixels (default 1024)."}}, []),
        "readonly": True,
    },
    "undo": {
        "description": "Undo the last canvas mutation.",
        "kind": ("post", "/api/undo"), "schema": schema({}, []),
    },
    "save_svg": {
        "description": "Save the canvas to disk (defaults to the loaded file's path).",
        "kind": ("post", "/api/save"), "schema": schema({"path": STR}, []),
    },
    "regenerate_layout": {
        "description": "Run the deterministic Crab Archi Design layout pipeline on the current canvas: the drawing plus its zones become constraints, the prompt becomes design intent, and the generated alternative is loaded back into the canvas. Slow (up to minutes).",
        "kind": ("post", "/api/regenerate"),
        "schema": schema({
            "prompt": {**STR, "description": "Natural-language design direction."},
            "project_id": STR,
            "engine_adapter": {"type": "string", "enum": ["layout-svg-engine", "same-layer-svg-engine"]},
            "households": {"type": "integer"},
            "standards": {"type": "array", "items": STR},
            "load_result": {"type": "boolean", "description": "Load the generated SVG back into the canvas (default true)."},
        }, ["prompt"]),
    },
}


class CanvasClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, route: str, payload: dict[str, Any] | None = None,
                timeout: int = 1800) -> dict[str, Any]:
        url = self.base_url + route
        if method == "get" and payload:
            from urllib.parse import urlencode
            url += "?" + urlencode({k: v for k, v in payload.items() if v is not None})
        data = None if method == "get" else json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"},
                                         method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                return {"status": "error", "error": f"HTTP {exc.code}"}
        except urllib.error.URLError as exc:
            return {"status": "error",
                    "error": f"canvas server unreachable at {self.base_url} ({exc.reason}). "
                             "Start it with: crab-archi-design-canvas --open"}

    def request_bytes(self, route: str, params: dict[str, Any]) -> bytes:
        from urllib.parse import urlencode
        url = self.base_url + route + "?" + urlencode({k: v for k, v in params.items() if v is not None})
        with urllib.request.urlopen(url, timeout=300) as response:
            return response.read()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = TOOLS[name]
        kind, target = tool["kind"]
        if kind == "op":
            return self.request("post", "/api/op", {"op": target, "params": arguments})
        if kind == "render":
            import base64
            params = dict(arguments)
            if isinstance(params.get("bbox"), list):
                params["bbox"] = ",".join(str(v) for v in params["bbox"])
            try:
                png = self.request_bytes(target, params)
            except urllib.error.HTTPError as exc:
                try:
                    return json.loads(exc.read().decode("utf-8"))
                except Exception:  # noqa: BLE001
                    return {"status": "error", "error": f"HTTP {exc.code}"}
            except urllib.error.URLError as exc:
                return {"status": "error", "error": f"canvas server unreachable ({exc.reason})"}
            return {"status": "ok", "png_base64": base64.b64encode(png).decode(),
                    "bytes": len(png)}
        if kind == "get":
            return self.request("get", target, arguments)
        return self.request("post", target, arguments)


def mcp_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "title": name.replace("_", " ").title(),
            "description": tool["description"],
            "inputSchema": tool["schema"],
            "annotations": {
                "readOnlyHint": bool(tool.get("readonly")),
                "destructiveHint": name in {"delete_elements", "open_svg", "clear_zones", "regenerate_layout"},
                "openWorldHint": False,
            },
        }
        for name, tool in TOOLS.items()
    ]


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    is_error = payload.get("status") == "error"
    if "png_base64" in payload:
        return {
            "content": [
                {"type": "image", "data": payload["png_base64"], "mimeType": "image/png"},
                {"type": "text", "text": f"rendered PNG ({payload.get('bytes', 0)} bytes)"},
            ],
            "structuredContent": {"status": payload.get("status"), "bytes": payload.get("bytes")},
            "isError": is_error,
        }
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def handle_request(client: CanvasClient, message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    def response(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    if method == "initialize":
        return response({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Crab Archi Design Live Canvas",
                           "version": "0.1.0"},
            "instructions": (
                "Live SVG CAD canvas. Typical flow: open_svg (or canvas_status to see what the "
                "user already loaded) -> list_elements to recognize geometry/labels -> edit with "
                "draw_*/delete_elements/copy_*/set_* -> set_zone to mark protected/mutable areas "
                "-> regenerate_layout for a full deterministic redesign. Every edit appears "
                "instantly in the user's browser at the canvas server URL."
            ),
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return response({})
    if method == "tools/list":
        return response({"tools": mcp_tools()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": message_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": message_id,
                    "error": {"code": -32602, "message": "arguments must be an object"}}
        return response(tool_result(client.call(name, arguments)))
    return {"jsonrpc": "2.0", "id": message_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve_stdio(client: CanvasClient) -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        result = handle_request(client, message) if isinstance(message, dict) else None
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(prog=SERVER_NAME)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CRAB_CANVAS_PORT", 8770)))
    args = parser.parse_args()
    serve_stdio(CanvasClient(f"http://{args.host}:{args.port}"))


if __name__ == "__main__":
    main()
