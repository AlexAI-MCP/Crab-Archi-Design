"""Stdio MCP server for CrabCADParser.

This server exposes the local parser and generated OpenCrab payloads.  It does
not store credentials and it does not submit a pack to a remote service by
itself; the returned ``opencrab_ingest_text`` payloads are intentionally
explicit so an OpenCrab MCP client can review and submit them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from crab_archi_design.cad_parser import inspect_pack, run_batch
from crab_archi_design.cad_reconstruction import reconstruct_from_ir_file


PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "crabcadparser-mcp"
SERVER_VERSION = "0.2.0"


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS = [
    {
        "name": "cad_batch_run",
        "title": "Run CAD batch parser",
        "description": "Parse a local directory of DWG/DXF files into CAD IR and an OpenCrab Pack v1 bundle. DWG conversion uses an optional local ODA File Converter.",
        "readOnlyHint": False,
        "inputSchema": schema(
            {
                "input_dir": {"type": "string", "description": "Explicit local directory containing DWG/DXF files."},
                "output_dir": {"type": "string", "description": "Explicit local output directory."},
                "recursive": {"type": "boolean", "default": True},
                "converter": {"type": "string", "description": "Optional ODAFileConverter executable path."},
                "output_version": {"type": "string", "default": "ACAD2000"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 120},
                "max_files": {"type": "integer", "minimum": 1},
                "resume": {"type": "boolean", "default": True},
                "pack_title": {"type": "string"},
            },
            ["input_dir"],
        ),
    },
    {
        "name": "cad_batch_status",
        "title": "Read CAD batch status",
        "description": "Read a generated CrabCADParser batch receipt without parsing or modifying any files.",
        "readOnlyHint": True,
        "inputSchema": schema({"receipt": {"type": "string", "description": "Path to batch_receipt.json."}}, ["receipt"]),
    },
    {
        "name": "cad_pack_inspect",
        "title": "Inspect CAD ontology pack",
        "description": "Inspect the manifest, quality report, counts, and OpenCrab payload path for a generated pack.",
        "readOnlyHint": True,
        "inputSchema": schema({"pack_dir": {"type": "string"}}, ["pack_dir"]),
    },
    {
        "name": "cad_opencrab_ingest_payload",
        "title": "Get OpenCrab ingest payload",
        "description": "Return the explicit OpenCrab opencrab_ingest_text payload rows from a generated pack. Content is omitted unless include_content=true.",
        "readOnlyHint": True,
        "inputSchema": schema({"pack_dir": {"type": "string"}, "include_content": {"type": "boolean", "default": False}, "max_rows": {"type": "integer", "minimum": 1, "maximum": 100}}, ["pack_dir"]),
    },
    {
        "name": "cad_reconstruct_dxf",
        "title": "Reconstruct DXF from CAD IR",
        "description": "Replay a parsed CAD IR JSON into a new DXF and return a round-trip fidelity report. Use mode=source for exact canonical DXF fallback.",
        "readOnlyHint": False,
        "inputSchema": schema(
            {
                "ir_json": {"type": "string", "description": "Path to a CAD IR JSON file."},
                "output_dxf": {"type": "string", "description": "Explicit output DXF path."},
                "mode": {"type": "string", "enum": ["structured", "source"], "default": "structured"},
                "report": {"type": "string", "description": "Optional round-trip report path."},
            },
            ["ir_json", "output_dxf"],
        ),
    },
]


def response(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def validate_local_path(value: Any, *, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A non-empty local path is required.")
    path = Path(value).expanduser()
    if must_exist and not path.exists():
        raise ValueError(f"Path does not exist: {path}")
    return path


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "cad_batch_run":
        input_dir = validate_local_path(arguments.get("input_dir"))
        if not input_dir.is_dir():
            raise ValueError(f"input_dir is not a directory: {input_dir}")
        output_dir = validate_local_path(arguments["output_dir"], must_exist=False) if arguments.get("output_dir") else input_dir.parent / f"{input_dir.name}_crabcadparser"
        result = run_batch(
            input_dir,
            output_dir,
            recursive=bool(arguments.get("recursive", True)),
            converter=arguments.get("converter"),
            output_version=str(arguments.get("output_version") or "ACAD2000"),
            timeout_seconds=int(arguments.get("timeout_seconds") or 120),
            max_files=int(arguments["max_files"]) if arguments.get("max_files") else None,
            resume=bool(arguments.get("resume", True)),
            pack_title=arguments.get("pack_title"),
        )
        return tool_result({"status": "pass" if result["receipt"]["statistics"]["failed"] == 0 else "review_required", "receipt": result["receipt"], "manifest": result["manifest"]})
    if name == "cad_batch_status":
        receipt_path = validate_local_path(arguments.get("receipt"))
        if receipt_path.name != "batch_receipt.json":
            raise ValueError("receipt must point to batch_receipt.json")
        return tool_result(json.loads(receipt_path.read_text(encoding="utf-8")))
    if name == "cad_pack_inspect":
        return tool_result(inspect_pack(validate_local_path(arguments.get("pack_dir"))))
    if name == "cad_opencrab_ingest_payload":
        pack_dir = validate_local_path(arguments.get("pack_dir"))
        payload_path = pack_dir / "opencrab" / "ingest_payloads.jsonl"
        if not payload_path.exists():
            raise ValueError(f"Missing payload file: {payload_path}")
        rows = [json.loads(line) for line in payload_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        requested_limit = arguments.get("max_rows")
        limit = len(rows) if requested_limit is None else min(max(int(requested_limit), 1), 100)
        if not bool(arguments.get("include_content", False)):
            rows = [{key: value for key, value in row.items() if key != "content"} for row in rows]
        return tool_result({"payload_path": str(payload_path.resolve()), "row_count": len(rows), "rows": rows[:limit], "truncated": len(rows) > limit})
    if name == "cad_reconstruct_dxf":
        ir_path = validate_local_path(arguments.get("ir_json"))
        output_path = validate_local_path(arguments.get("output_dxf"), must_exist=False)
        report_path = validate_local_path(arguments.get("report"), must_exist=False) if arguments.get("report") else None
        result = reconstruct_from_ir_file(
            ir_path,
            output_path,
            mode=str(arguments.get("mode") or "structured"),
            report_path=report_path,
        )
        return tool_result(result)
    raise ValueError(f"Unknown tool: {name}")


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return response(message_id, {
            "protocolVersion": requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "CrabCADParser MCP", "version": SERVER_VERSION},
            "instructions": "Use cad_batch_run for explicit local DWG/DXF directories. Review the generated quality report before submitting OpenCrab payloads.",
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return response(message_id, {})
    if method == "tools/list":
        return response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return error_response(message_id, -32602, "Tool arguments must be an object.")
        try:
            return response(message_id, call_tool(name, arguments))
        except Exception as exc:
            return response(message_id, tool_result({"error": str(exc)}, is_error=True))
    return error_response(message_id, -32601, f"Method not found: {method}")


def serve_stdio() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(error_response(None, -32700, f"Parse error: {exc}")) + "\n")
            sys.stdout.flush()
            continue
        if not isinstance(message, dict):
            sys.stdout.write(json.dumps(error_response(None, -32600, "Invalid Request")) + "\n")
            sys.stdout.flush()
            continue
        result = handle_request(message)
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(prog=SERVER_NAME)
    parser.add_argument("--stdio", action="store_true", help="Run the stdio MCP bridge (default).")
    parser.parse_args()
    serve_stdio()


if __name__ == "__main__":
    main()
