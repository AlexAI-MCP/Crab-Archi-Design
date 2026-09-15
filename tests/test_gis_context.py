from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from crab_archi_design.gis_context import (
    QGIS_MCP_SNAPSHOT_SCHEMA,
    QgisMcpClient,
    QgisMcpError,
    build_gis_context,
    capture_qgis_preview,
    capture_qgis_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/crab_archi_design/cli.py"


class FakeQgisClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, command: str, _params: dict | None = None) -> dict:
        self.calls.append(command)
        responses = {
            "get_qgis_info": {"qgis_version": "3.44.12", "plugins_count": 5, "profile_folder": "/private/profile"},
            "get_project_info": {
                "filename": "/private/projects/uijeongbu.qgz",
                "title": "Uijeongbu site",
                "layer_count": 1,
                "crs": "EPSG:3857",
            },
            "get_canvas_extent": {"xmin": 14130000, "ymin": 4530000, "xmax": 14155000, "ymax": 4557000, "crs": "EPSG:3857", "width": 1280, "height": 900},
            "get_layers": {"layers": [{"id": "terrain", "name": "Terrain", "type": "raster", "visible": True}], "total_count": 1},
            "get_layer_info": {
                "id": "terrain",
                "name": "Terrain",
                "type": "raster",
                "crs": "EPSG:3857",
                "extent": {"xmin": 14130000, "ymin": 4530000, "xmax": 14155000, "ymax": 4557000},
                "source": "type=xyz&url=https://tiles.example/{z}/{x}/{y}.png&token=secret-token",
                "provider": "wms",
                "is_valid": True,
                "width": 0,
                "height": 0,
                "band_count": 0,
            },
            "get_canvas_screenshot": {
                "base64_data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLh2wAAAABJRU5ErkJggg==",
                "mime_type": "image/png",
                "width": 1,
                "height": 1,
            },
        }
        return responses[command]


def test_capture_qgis_snapshot_reads_metadata_only_and_redacts_sources() -> None:
    client = FakeQgisClient()
    snapshot = capture_qgis_snapshot(client)

    assert snapshot["schema"] == QGIS_MCP_SNAPSHOT_SCHEMA
    assert client.calls == ["get_qgis_info", "get_project_info", "get_canvas_extent", "get_layers", "get_layer_info"]
    assert snapshot["project"]["file_name"] == "uijeongbu.qgz"
    assert snapshot["qgis"].get("profile_folder") is None
    assert snapshot["layers"][0]["metadata_status"] == "captured"
    assert "secret-token" not in snapshot["layers"][0]["source"]
    assert "token=<redacted>" in snapshot["layers"][0]["source"]


def test_capture_qgis_preview_keeps_qgis_image_outside_svg() -> None:
    image, preview = capture_qgis_preview(FakeQgisClient())

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert preview["mime_type"] == "image/png"
    assert preview["width"] == 1
    assert preview["height"] == 1
    assert preview["role"] == "qgis_review_sidecar_only"
    assert preview["final_svg_embedding_allowed"] is False


def test_gis_context_is_advisory_and_blocks_direct_svg_coordinate_transfer() -> None:
    snapshot = capture_qgis_snapshot(FakeQgisClient())
    snapshot["visual_reference"] = {
        "available": False,
        "status": "unavailable",
        "role": "qgis_review_sidecar_only",
        "error": "QGIS MCP read failed: timed out",
    }
    context = build_gis_context(
        snapshot,
        context_id="gis_context_001",
        project_id="uijeongbu-site",
        source_file="/tmp/qgis_snapshot_001.json",
        observations=["North-facing terrain needs review."],
    )

    assert context["status"] == "context_ready"
    assert context["spatial_reference"]["coordinate_mapping_status"] == "unmapped_to_source_svg"
    assert context["gates"]["direct_qgis_to_svg_coordinate_transfer_allowed"] is False
    assert context["design_policy"]["gis_role"] == "evidence_and_context_only"
    assert context["observations"] == ["North-facing terrain needs review."]
    assert context["visual_reference"]["available"] is False
    assert context["visual_reference"]["status"] == "unavailable"
    assert context["visual_reference"]["final_svg_embedding_allowed"] is False


def test_qgis_client_refuses_non_read_only_commands_before_connecting() -> None:
    with pytest.raises(QgisMcpError, match="Blocked non-read-only"):
        QgisMcpClient().request("add_web_layer", {"url": "https://example.invalid"})


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, capture_output=True, check=False)


def test_cli_attaches_gis_context_and_passes_it_to_opencrab_request(tmp_path: Path) -> None:
    source_svg = tmp_path / "source.svg"
    source_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect x="0" y="0" width="100" height="100"/></svg>',
        encoding="utf-8",
    )
    project_root = tmp_path / "projects"
    init = run_cli(
        "--project-root",
        str(project_root),
        "init",
        "--project-id",
        "uijeongbu-site",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert init.returncode == 0, init.stderr

    snapshot = capture_qgis_snapshot(FakeQgisClient())
    snapshot_path = project_root / "uijeongbu-site" / "gis" / "qgis_snapshot_001.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path = snapshot_path.with_name("qgis_preview_001.png")
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    snapshot["visual_reference"] = {
        "available": True,
        "status": "captured",
        "file": str(preview_path),
        "sha256": "fixture",
        "mime_type": "image/png",
        "width": 1,
        "height": 1,
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    attached = run_cli(
        "--project-root",
        str(project_root),
        "gis-context-attach",
        "--project-id",
        "uijeongbu-site",
        "--snapshot",
        str(snapshot_path),
        "--summary",
        "Uijeongbu terrain context",
        "--observation",
        "Review slope before external-space placement.",
        cwd=ROOT,
    )
    assert attached.returncode == 0, attached.stderr
    context_path, manifest_path = [Path(line) for line in attached.stdout.splitlines()[:2]]
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert context["status"] == "context_ready"
    assert context_manifest["required_for_final_svg"] is False
    assert context_manifest["design_projection_status"] == "review_required"

    status_result = run_cli("--project-root", str(project_root), "project-status", "--project-id", "uijeongbu-site", cwd=ROOT)
    assert status_result.returncode == 0, status_result.stderr
    status = json.loads(Path(status_result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert status["gis_context"]["status"] == "context_ready"
    assert status["gates"]["gis_direct_svg_coordinate_transfer_blocked"] is True
    assert status["latest_artifacts"]["gis_context_manifest"] == str(manifest_path)

    request_result = run_cli("--project-root", str(project_root), "opencrab-request", "--project-id", "uijeongbu-site", cwd=ROOT)
    assert request_result.returncode == 0, request_result.stderr
    request = json.loads(Path(request_result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert "QGIS context: status=context_ready" in request["query"]
    assert request["project_context"]["gis_context"]["layer_count"] == 1

    tool_manifest = run_cli("mcp-manifest", cwd=ROOT)
    assert tool_manifest.returncode == 0, tool_manifest.stderr
    tools = {tool["id"] for tool in json.loads(tool_manifest.stdout)["tools"]}
    assert {"qgis_capture", "gis_context_attach"} <= tools

    export_result = run_cli(
        "--project-root",
        str(project_root),
        "export-package",
        "--project-id",
        "uijeongbu-site",
        cwd=ROOT,
    )
    assert export_result.returncode == 0, export_result.stderr
    zip_path = Path(export_result.stdout.splitlines()[1])
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "project/uijeongbu-site/gis/qgis_snapshot_001.json" in names
    assert "project/uijeongbu-site/gis/qgis_preview_001.png" in names
