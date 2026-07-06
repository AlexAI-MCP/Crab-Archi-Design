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
        "description": "List recognized graphic elements (walls, labels, shapes) with stable cid handles, tag, bbox, style, and text. Use cids with the edit tools.",
        "kind": ("get", "/api/elements"),
        "schema": schema({"tag": {**STR, "description": "Filter by SVG tag, e.g. 'path', 'text', 'g'."},
                          "max_results": {"type": "integer"}}, []), "readonly": True,
    },
    "list_zones": {
        "description": "List annotation zones (community shell, protected, mutable...) drawn on the canvas.",
        "kind": ("get", "/api/zones"), "schema": schema({}, []), "readonly": True,
    },
    "draw_line": {
        "description": "Draw a straight line on the canvas.",
        "kind": ("op", "draw_line"),
        "schema": schema({"x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM, "style": STYLE},
                         ["x1", "y1", "x2", "y2"]),
    },
    "draw_polyline": {
        "description": "Draw a polyline (or closed polygon) through the given points.",
        "kind": ("op", "draw_polyline"),
        "schema": schema({"points": POINTS, "closed": {"type": "boolean"}, "style": STYLE}, ["points"]),
    },
    "draw_curve": {
        "description": "Draw a smooth curve through the given points (Catmull-Rom interpolation).",
        "kind": ("op", "draw_curve"), "schema": schema({"points": POINTS, "style": STYLE}, ["points"]),
    },
    "draw_path": {
        "description": "Draw a raw SVG path (full path-d syntax for arcs/beziers).",
        "kind": ("op", "draw_path"), "schema": schema({"d": STR, "style": STYLE}, ["d"]),
    },
    "draw_rect": {
        "description": "Draw a rectangle.",
        "kind": ("op", "draw_rect"),
        "schema": schema({"x": NUM, "y": NUM, "width": NUM, "height": NUM, "rx": NUM, "style": STYLE},
                         ["x", "y", "width", "height"]),
    },
    "draw_ellipse": {
        "description": "Draw a circle (ry omitted) or ellipse.",
        "kind": ("op", "draw_ellipse"),
        "schema": schema({"cx": NUM, "cy": NUM, "rx": NUM, "ry": NUM, "style": STYLE},
                         ["cx", "cy", "rx"]),
    },
    "add_text": {
        "description": "Add a text label at the given position.",
        "kind": ("op", "add_text"),
        "schema": schema({"x": NUM, "y": NUM, "text": STR, "style": STYLE}, ["x", "y", "text"]),
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

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = TOOLS[name]
        kind, target = tool["kind"]
        if kind == "op":
            return self.request("post", "/api/op", {"op": target, "params": arguments})
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
