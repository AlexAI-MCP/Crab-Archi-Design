from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from crab_archi_design.cli import PROJECT_NAME, PROJECT_VERSION, build_mcp_tool_manifest


PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "crab-archi-design-mcp"
DEFAULT_TIMEOUT_SECONDS = 300
GLOBAL_FIELDS = {"project_root", "cwd", "timeout_seconds", "raw_args"}
BOOLEAN_FIELDS = {
    "allow_missing_source",
    "replace",
    "skip_preview",
    "skip_apply",
    "skip_review_panel",
    "replace_standards",
    "replace_evidence",
    "replace_constraints",
    "reinit",
    "include_source_svg",
    "only_latest",
    "skip_opencrab_sync",
    "check_local_files",
    "strict",
    "open",
}
INTEGER_FIELDS = {"households", "max_labels", "timeout"}
ARRAY_FIELDS = {
    "standards",
    "engine_arg",
    "opencrab_result_file",
    "opencrab_result_json",
    "evidence_metadata",
    "standards_metadata",
    "result_file",
    "result_json",
    "metadata",
    "file",
}


def flag_to_key(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def key_to_flag(key: str) -> str:
    return "--" + key.replace("_", "-")


def unique_flags(tool: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for field in ["required_args", "typical_required_args_for_new_project", "optional_args"]:
        for flag in tool.get(field, []):
            if flag.startswith("--") and flag not in flags:
                flags.append(flag)
    return flags


def schema_property_for_key(key: str, flag: str) -> dict[str, Any]:
    description = f"Maps to `{flag}`."
    if key in BOOLEAN_FIELDS:
        return {"type": "boolean", "description": description}
    if key in INTEGER_FIELDS:
        return {"type": "integer", "description": description}
    if key in ARRAY_FIELDS:
        return {"type": "array", "items": {"type": "string"}, "description": f"Repeatable. {description}"}
    return {"type": "string", "description": description}


def tool_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "project_root": {
            "type": "string",
            "description": "Project root directory passed before the CLI subcommand. Defaults to `projects`.",
        },
        "cwd": {
            "type": "string",
            "description": "Working directory for the CLI process. Defaults to the MCP server working directory.",
        },
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3600,
            "description": "Process timeout in seconds.",
        },
        "raw_args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Advanced escape hatch: raw CLI arguments appended after structured arguments.",
        },
    }
    for flag in unique_flags(tool):
        properties[flag_to_key(flag)] = schema_property_for_key(flag_to_key(flag), flag)
    required = [flag_to_key(flag) for flag in tool.get("required_args", []) if flag.startswith("--")]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def mcp_tools() -> list[dict[str, Any]]:
    manifest = build_mcp_tool_manifest()
    tools = []
    for tool in manifest["tools"]:
        tools.append(
            {
                "name": tool["id"],
                "title": tool["id"].replace("_", " ").title(),
                "description": tool["description"],
                "inputSchema": tool_input_schema(tool),
                "annotations": {
                    "readOnlyHint": tool["id"] in {"project_status", "verify_package", "doctor", "mcp_manifest", "mcp_config", "doodle_editor"},
                    "destructiveHint": False,
                    "openWorldHint": tool["id"] in {"run_job", "workflow_run", "opencrab_sync", "apply_edit"},
                },
            }
        )
    return tools


def tool_by_name(name: str) -> dict[str, Any] | None:
    for tool in build_mcp_tool_manifest()["tools"]:
        if tool["id"] == name:
            return tool
    return None


def coerce_arguments(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be an object.")
    return value


def validate_argument_keys(tool: dict[str, Any], arguments: dict[str, Any]) -> None:
    allowed = {flag_to_key(flag) for flag in unique_flags(tool)}
    allowed.update(GLOBAL_FIELDS)
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ValueError(f"Unknown argument(s): {', '.join(unknown)}")
    missing = [flag_to_key(flag) for flag in tool.get("required_args", []) if flag_to_key(flag) not in arguments]
    if missing:
        raise ValueError(f"Missing required argument(s): {', '.join(missing)}")


def append_cli_argument(command: list[str], key: str, value: Any) -> None:
    if value is None or value is False:
        return
    flag = key_to_flag(key)
    if key in BOOLEAN_FIELDS:
        if value is True:
            command.append(flag)
        return
    if isinstance(value, list):
        for item in value:
            command.extend([flag, str(item)])
        return
    command.extend([flag, str(value)])


def build_cli_command(tool: dict[str, Any], arguments: dict[str, Any]) -> tuple[list[str], Path | None, int]:
    project_root = str(arguments.get("project_root") or os.environ.get("CRAB_ARCHI_PROJECT_ROOT") or "projects")
    command = [sys.executable, "-m", "crab_archi_design.cli", "--project-root", project_root, tool["cli_subcommand"]]
    for flag in unique_flags(tool):
        key = flag_to_key(flag)
        if key in arguments:
            append_cli_argument(command, key, arguments[key])
    for raw_arg in arguments.get("raw_args", []) or []:
        command.append(str(raw_arg))
    cwd = Path(arguments["cwd"]).expanduser() if arguments.get("cwd") else None
    timeout = int(arguments.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    return command, cwd, timeout


def call_tool(name: str, raw_arguments: Any) -> dict[str, Any]:
    tool = tool_by_name(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    arguments = coerce_arguments(raw_arguments)
    validate_argument_keys(tool, arguments)
    command, cwd, timeout = build_cli_command(tool, arguments)
    env = os.environ.copy()
    src_dir = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    primary_artifact = stdout.splitlines()[0] if stdout else None
    structured = {
        "tool": name,
        "command": command,
        "cwd": str(cwd) if cwd else os.getcwd(),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "primary_artifact": primary_artifact,
    }
    text = json.dumps(structured, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": completed.returncode != 0,
    }


def response(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        protocol_version = requested_version if requested_version == PROTOCOL_VERSION else PROTOCOL_VERSION
        return response(
            message_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "Crab Archi Design MCP", "version": PROJECT_VERSION},
                "instructions": "Use tools/list to discover Crab Archi Design exec tools. OpenCrab evidence is required before final SVG candidates are accepted.",
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return response(message_id, {})
    if method == "tools/list":
        return response(message_id, {"tools": mcp_tools()})
    if method == "tools/call":
        try:
            result = call_tool(str(params.get("name") or ""), params.get("arguments"))
        except ValueError as exc:
            return error_response(message_id, -32602, str(exc))
        except subprocess.TimeoutExpired as exc:
            return response(
                message_id,
                {
                    "content": [{"type": "text", "text": f"Tool timed out after {exc.timeout} seconds."}],
                    "structuredContent": {"timeout_seconds": exc.timeout},
                    "isError": True,
                },
            )
        return response(message_id, result)
    return error_response(message_id, -32601, f"Method not found: {method}")


def serve_stdio() -> None:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(error_response(None, -32700, "Parse error", {"error": str(exc)})) + "\n")
            sys.stdout.flush()
            continue
        if not isinstance(message, dict):
            sys.stdout.write(json.dumps(error_response(None, -32600, "Invalid Request")) + "\n")
            sys.stdout.flush()
            continue
        result = handle_request(message)
        if result is None:
            continue
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crab-archi-design-mcp")
    parser.add_argument("--stdio", action="store_true", help="Run the stdio JSON-RPC MCP bridge. This is the default.")
    return parser


def main() -> None:
    build_parser().parse_args()
    serve_stdio()


if __name__ == "__main__":
    main()
