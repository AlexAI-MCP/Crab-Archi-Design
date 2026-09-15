"""Read-only QGIS MCP snapshots and safe GIS context normalization.

GIS facts are useful design context, but they do not share a coordinate space
with a source CAD/SVG drawing by default.  This module intentionally captures
only QGIS metadata plus an optional separate visual reference and retains that
boundary in its output: a future solver may use a reviewed georeference
mapping, but raw QGIS coordinates can never be treated as SVG edit coordinates
here.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QGIS_MCP_DEFAULT_HOST = "127.0.0.1"
QGIS_MCP_DEFAULT_PORT = 9876
QGIS_MCP_FRAME = struct.Struct(">I")
QGIS_MCP_SNAPSHOT_SCHEMA = "crab-archi-design-qgis-snapshot-v1"
GIS_CONTEXT_SCHEMA = "crab-archi-design-gis-context-v1"
GIS_CONTEXT_MANIFEST_SCHEMA = "crab-archi-design-gis-context-manifest-v1"

READ_ONLY_QGIS_COMMANDS = frozenset(
    {
        "get_qgis_info",
        "get_project_info",
        "get_canvas_extent",
        "get_canvas_screenshot",
        "get_layers",
        "get_layer_info",
    }
)
_LOCAL_QGIS_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_QGIS_PREVIEW_BYTES = 12 * 1024 * 1024
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization|authcfg)\s*=\s*([^&;\s]+)"
)
_SENSITIVE_KEY_RE = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|authorization)")


class QgisMcpError(RuntimeError):
    """A safe, actionable failure while reading the local QGIS MCP bridge."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_local_qgis_host(host: str) -> bool:
    return host.strip().lower().strip("[]") in _LOCAL_QGIS_HOSTS


def _redact_text(value: str) -> str:
    return _SENSITIVE_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def redact_sensitive(value: Any) -> Any:
    """Return a serialisable copy without credentials embedded in QGIS sources."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            redacted[key_text] = "<redacted>" if _SENSITIVE_KEY_RE.search(key_text) else redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def normalize_extent(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    keys = ("xmin", "ymin", "xmax", "ymax")
    result = {key: _number(value.get(key)) for key in keys}
    if any(item is None for item in result.values()):
        return None
    return {key: float(result[key]) for key in keys}


def extent_is_valid(extent: dict[str, float] | None) -> bool:
    return bool(extent and extent["xmin"] < extent["xmax"] and extent["ymin"] < extent["ymax"])


def _short_error(error: Exception | str) -> str:
    return _redact_text(str(error)).replace("\n", " ")[:280]


class QgisMcpClient:
    """Minimal client for the QGIS MCP plugin's local length-prefixed socket.

    The public QGIS MCP stdio server is intentionally not embedded as a Python
    dependency.  The plugin itself exposes a compact local protocol, so this
    adapter stays stdlib-only and accepts only a fixed read-only command list.
    """

    def __init__(
        self,
        host: str = QGIS_MCP_DEFAULT_HOST,
        port: int = QGIS_MCP_DEFAULT_PORT,
        timeout: float = 20.0,
        token: str | None = None,
        allow_remote: bool = False,
    ) -> None:
        if not allow_remote and not is_local_qgis_host(host):
            raise QgisMcpError("QGIS capture only connects to localhost unless a reviewed remote adapter is added.")
        if not 1 <= int(port) <= 65535:
            raise QgisMcpError(f"Invalid QGIS MCP port: {port}")
        if timeout <= 0:
            raise QgisMcpError("QGIS MCP timeout must be positive.")
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.token = token or None
        self._socket: socket.socket | None = None

    @classmethod
    def from_environment(
        cls,
        host: str = QGIS_MCP_DEFAULT_HOST,
        port: int = QGIS_MCP_DEFAULT_PORT,
        timeout: float = 20.0,
        token_env: str = "QGIS_MCP_TOKEN",
    ) -> "QgisMcpClient":
        return cls(host=host, port=port, timeout=timeout, token=os.environ.get(token_env) or None)

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._socket.settimeout(self.timeout)
        except OSError as error:
            raise QgisMcpError(
                f"Could not connect to local QGIS MCP at {self.host}:{self.port}. "
                "Open QGIS, enable QGIS MCP, and choose Run MCP."
            ) from error

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> "QgisMcpClient":
        self.connect()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def _recv_exact(self, count: int) -> bytes:
        if self._socket is None:
            raise QgisMcpError("QGIS MCP client is not connected.")
        data = bytearray()
        while len(data) < count:
            chunk = self._socket.recv(count - len(data))
            if not chunk:
                raise QgisMcpError("QGIS MCP closed the connection before returning a response.")
            data.extend(chunk)
        return bytes(data)

    def request(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if command not in READ_ONLY_QGIS_COMMANDS:
            raise QgisMcpError(f"Blocked non-read-only QGIS MCP command: {command}")
        self.connect()
        if self._socket is None:
            raise QgisMcpError("QGIS MCP client is not connected.")
        payload: dict[str, Any] = {"type": command, "params": params or {}}
        if self.token:
            payload["token"] = self.token
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self._socket.sendall(QGIS_MCP_FRAME.pack(len(encoded)))
            self._socket.sendall(encoded)
            response_length = QGIS_MCP_FRAME.unpack(self._recv_exact(4))[0]
            if response_length > 20 * 1024 * 1024:
                raise QgisMcpError("QGIS MCP response exceeds the 20 MB metadata capture limit.")
            response = json.loads(self._recv_exact(response_length).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self.close()
            raise QgisMcpError(f"QGIS MCP read failed: {_short_error(error)}") from error
        if not isinstance(response, dict):
            raise QgisMcpError("QGIS MCP returned an invalid response object.")
        if response.get("status") != "success":
            raise QgisMcpError(f"QGIS MCP {command} failed: {_short_error(response.get('message') or response)}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise QgisMcpError(f"QGIS MCP {command} returned a non-object result.")
        return redact_sensitive(result)


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    filename = project.get("filename")
    return {
        "loaded": bool(filename),
        "file_name": Path(str(filename)).name if filename else None,
        "title": project.get("title") or None,
        "crs": project.get("crs") or None,
        "layer_count": _number(project.get("layer_count")),
    }


def _layer_summary(layer: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = detail or {}
    source = detail.get("source")
    return {
        "id": layer.get("id") or detail.get("id"),
        "name": layer.get("name") or detail.get("name") or "Unnamed layer",
        "type": layer.get("type") or detail.get("type") or "unknown",
        "visible": bool(layer.get("visible", False)),
        "crs": detail.get("crs") or None,
        "extent": normalize_extent(detail.get("extent")),
        "provider": detail.get("provider") or None,
        "source": _redact_text(str(source)) if source else None,
        "is_valid": detail.get("is_valid"),
        "feature_count": _number(detail.get("feature_count")),
        "geometry_type": detail.get("geometry_type"),
        "width": _number(detail.get("width")),
        "height": _number(detail.get("height")),
        "band_count": _number(detail.get("band_count")),
    }


def capture_qgis_snapshot(client: Any, max_layers: int = 50) -> dict[str, Any]:
    """Capture QGIS metadata without inspecting or changing features or styling."""
    if not 1 <= int(max_layers) <= 200:
        raise QgisMcpError("max_layers must be between 1 and 200.")
    qgis = client.request("get_qgis_info")
    project = client.request("get_project_info")
    canvas = client.request("get_canvas_extent")
    layer_listing = client.request("get_layers", {"limit": int(max_layers), "offset": 0})
    raw_layers = layer_listing.get("layers", [])
    if not isinstance(raw_layers, list):
        raise QgisMcpError("QGIS MCP get_layers returned an invalid layer list.")

    layers: list[dict[str, Any]] = []
    for raw_layer in raw_layers:
        if not isinstance(raw_layer, dict):
            continue
        detail: dict[str, Any] | None = None
        detail_error: str | None = None
        layer_id = raw_layer.get("id")
        if layer_id:
            try:
                detail = client.request("get_layer_info", {"layer_id": layer_id})
            except QgisMcpError as error:
                detail_error = _short_error(error)
        layer = _layer_summary(raw_layer, detail)
        layer["metadata_status"] = "captured" if detail is not None else "review_required"
        if detail_error:
            layer["metadata_error"] = detail_error
        layers.append(layer)

    normalized_canvas = {
        "crs": canvas.get("crs") or None,
        "extent": normalize_extent(canvas),
        "width": _number(canvas.get("width")),
        "height": _number(canvas.get("height")),
    }
    total_layer_count = _number(layer_listing.get("total_count"))
    commands_executed = [
        "get_qgis_info",
        "get_project_info",
        "get_canvas_extent",
        "get_layers",
        "get_layer_info",
    ]
    return {
        "schema": QGIS_MCP_SNAPSHOT_SCHEMA,
        "captured_at": utc_now(),
        "capture_mode": "qgis_mcp_read_only",
        "read_only_commands": commands_executed,
        "qgis": {
            "version": qgis.get("qgis_version") or None,
            "plugins_count": _number(qgis.get("plugins_count")),
        },
        "project": _project_summary(project),
        "canvas": normalized_canvas,
        "layers": layers,
        "layer_total_count": total_layer_count,
        "truncated": bool(total_layer_count is not None and len(layers) < int(total_layer_count)),
    }


def capture_qgis_preview(client: Any) -> tuple[bytes, dict[str, Any]]:
    """Return a bounded QGIS canvas PNG for a GIS-only review sidecar.

    The bytes never become part of a source or final SVG. They are retained
    separately so reviewers can see the QGIS context that accompanied a design
    decision without weakening the native-SVG output rule.
    """
    payload = client.request("get_canvas_screenshot")
    encoded = payload.get("base64_data")
    if not isinstance(encoded, str) or not encoded:
        raise QgisMcpError("QGIS MCP screenshot response did not contain PNG base64 data.")
    try:
        image = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as error:
        raise QgisMcpError("QGIS MCP screenshot response contained invalid base64 data.") from error
    if len(image) > MAX_QGIS_PREVIEW_BYTES:
        raise QgisMcpError("QGIS MCP screenshot exceeds the 12 MB GIS review-preview limit.")
    if len(image) < 24 or not image.startswith(PNG_SIGNATURE) or image[12:16] != b"IHDR":
        raise QgisMcpError("QGIS MCP screenshot was not a valid PNG preview.")
    width, height = struct.unpack(">II", image[16:24])
    if width < 1 or height < 1:
        raise QgisMcpError("QGIS MCP screenshot has invalid dimensions.")
    reported_width = _number(payload.get("width"))
    reported_height = _number(payload.get("height"))
    if reported_width is not None and int(reported_width) != width:
        raise QgisMcpError("QGIS MCP screenshot width did not match its PNG payload.")
    if reported_height is not None and int(reported_height) != height:
        raise QgisMcpError("QGIS MCP screenshot height did not match its PNG payload.")
    return image, {
        "mime_type": "image/png",
        "width": width,
        "height": height,
        "source_command": "get_canvas_screenshot",
        "role": "qgis_review_sidecar_only",
        "final_svg_embedding_allowed": False,
    }


def build_gis_context(
    snapshot: dict[str, Any],
    *,
    context_id: str,
    project_id: str,
    source_file: str,
    source_tool: str = "qgis_mcp_read_only",
    summary: str | None = None,
    confidence: float = 0.8,
    observations: list[str] | None = None,
    analysis: dict[str, Any] | None = None,
    attached_at: str | None = None,
) -> dict[str, Any]:
    """Make a design-safe GIS context artifact from a QGIS metadata snapshot."""
    if not isinstance(snapshot, dict):
        raise QgisMcpError("GIS context input must be a QGIS snapshot JSON object.")
    if not 0.0 <= float(confidence) <= 1.0:
        raise QgisMcpError("GIS context confidence must be between 0 and 1.")
    canvas = snapshot.get("canvas") if isinstance(snapshot.get("canvas"), dict) else {}
    layers = snapshot.get("layers") if isinstance(snapshot.get("layers"), list) else []
    normalized_layers = [redact_sensitive(item) for item in layers if isinstance(item, dict)]
    extent = normalize_extent(canvas.get("extent"))
    crs = canvas.get("crs")
    captured_metadata_count = sum(1 for layer in normalized_layers if layer.get("metadata_status") == "captured")
    valid_layer_count = sum(1 for layer in normalized_layers if layer.get("is_valid") is not False)
    gates = {
        "qgis_snapshot_schema_supported": snapshot.get("schema") in {None, QGIS_MCP_SNAPSHOT_SCHEMA},
        "qgis_read_only_capture": snapshot.get("capture_mode") in {None, "qgis_mcp_read_only"},
        "canvas_crs_present": bool(crs),
        "canvas_extent_valid": extent_is_valid(extent),
        "layer_metadata_captured": captured_metadata_count > 0,
        "valid_layer_present": valid_layer_count > 0,
        "source_svg_georeference_confirmed": False,
        "direct_qgis_to_svg_coordinate_transfer_allowed": False,
        "human_review_required_before_design_projection": True,
    }
    context_ready = all(
        gates[key]
        for key in (
            "qgis_snapshot_schema_supported",
            "qgis_read_only_capture",
            "canvas_crs_present",
            "canvas_extent_valid",
            "layer_metadata_captured",
            "valid_layer_present",
        )
    )
    status = "context_ready" if context_ready else "review_required"
    visual_reference = snapshot.get("visual_reference") if isinstance(snapshot.get("visual_reference"), dict) else None
    normalized_visual_reference = None
    if visual_reference:
        visual_available = bool(visual_reference.get("available", visual_reference.get("file")))
        normalized_visual_reference = {
            "available": visual_available,
            "status": visual_reference.get("status") or ("captured" if visual_available else "unavailable"),
            "file": str(visual_reference.get("file")) if visual_reference.get("file") else None,
            "sha256": str(visual_reference.get("sha256")) if visual_reference.get("sha256") else None,
            "mime_type": visual_reference.get("mime_type") or None,
            "width": _number(visual_reference.get("width")),
            "height": _number(visual_reference.get("height")),
            "role": "qgis_review_sidecar_only",
            "final_svg_embedding_allowed": False,
            "error": _redact_text(str(visual_reference.get("error"))) if visual_reference.get("error") else None,
        }
    return {
        "schema": GIS_CONTEXT_SCHEMA,
        "id": context_id,
        "project_id": project_id,
        "attached_at": attached_at or utc_now(),
        "status": status,
        "summary": summary or "QGIS read-only site and surrounding-context snapshot.",
        "confidence": float(confidence),
        "source": {
            "tool": source_tool,
            "snapshot_file": str(source_file),
            "snapshot_schema": snapshot.get("schema"),
            "captured_at": snapshot.get("captured_at"),
            "qgis_version": (snapshot.get("qgis") or {}).get("version") if isinstance(snapshot.get("qgis"), dict) else None,
        },
        "spatial_reference": {
            "crs": crs,
            "extent": extent,
            "coordinate_mapping_status": "unmapped_to_source_svg",
            "coordinate_mapping_policy": "A reviewed georeference mapping is required before GIS geometry can influence SVG coordinates or solver constraints.",
        },
        "layers": normalized_layers,
        "layer_count": len(normalized_layers),
        "captured_layer_metadata_count": captured_metadata_count,
        "observations": [str(item) for item in observations or [] if str(item).strip()],
        "analysis": redact_sensitive(analysis or {}),
        "visual_reference": normalized_visual_reference,
        "gates": gates,
        "design_policy": {
            "gis_role": "evidence_and_context_only",
            "allowed": [
                "cite layer metadata and verified QGIS analysis in OpenCrab queries and design handoffs",
                "derive human-reviewed site, access, setback, view, slope, flood, or amenity considerations",
            ],
            "disallowed": [
                "directly mutate source SVG geometry from GIS coordinates",
                "add GIS raster tiles or screenshots to final SVG output",
                "override protected parking, columns, cores, ramps, stairs, egress, or community shell",
            ],
        },
    }


def summarize_gis_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {
            "status": "missing",
            "layer_count": 0,
            "coordinate_mapping_status": "missing",
            "layer_names": [],
        }
    spatial = context.get("spatial_reference") if isinstance(context.get("spatial_reference"), dict) else {}
    layers = context.get("layers") if isinstance(context.get("layers"), list) else []
    return {
        "id": context.get("id"),
        "status": context.get("status", "review_required"),
        "summary": context.get("summary"),
        "confidence": context.get("confidence"),
        "crs": spatial.get("crs"),
        "extent": spatial.get("extent"),
        "coordinate_mapping_status": spatial.get("coordinate_mapping_status", "unmapped_to_source_svg"),
        "layer_count": context.get("layer_count", len(layers)),
        "layer_names": [str(item.get("name")) for item in layers[:24] if isinstance(item, dict) and item.get("name")],
        "observations": context.get("observations", [])[:20],
        "source": context.get("source", {}),
        "visual_reference_available": bool((context.get("visual_reference") or {}).get("available")),
        "gates": context.get("gates", {}),
    }
