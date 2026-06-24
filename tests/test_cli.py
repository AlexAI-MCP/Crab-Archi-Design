from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from crab_archi_design.layout_svg_engine import draw_layout
from crab_archi_design.solver.svg_mutation import apply_same_layer_geometry_patch, split_line_for_opening


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/crab_archi_design/cli.py"


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, capture_output=True, check=False)


def test_mcp_manifest_describes_exec_tools(tmp_path: Path) -> None:
    result = run_cli("mcp-manifest", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["schema"] == "crab-archi-design-mcp-tool-manifest-v1"
    assert manifest["opencrab"]["required"] is True
    assert manifest["opencrab"]["homepage"] == "https://opencrab.sh"
    assert manifest["transport"]["primary"] == "exec"
    tool_ids = {tool["id"] for tool in manifest["tools"]}
    assert {"create_job", "run_job", "validate_job", "workflow_run", "revision_run", "opencrab_request", "opencrab_sync", "topology_build", "recognize_svg_v2", "recognition_audit", "svg_patch_plan", "prompt_edit", "sketch_intent", "scale_attach", "apply_edit", "export_package", "verify_package", "doctor", "release_audit"} <= tool_ids
    assert "mcp_config" in tool_ids
    assert "mcp_smoke" in tool_ids
    create_job_tool = next(tool for tool in manifest["tools"] if tool["id"] == "create_job")
    assert "--brief" in create_job_tool["optional_args"]
    assert any("job.md" in output or "_job.md" in output for output in create_job_tool["outputs"])
    assert manifest["recommended_sequences"]["saas_job_runner"] == ["create_job", "validate_job", "run_job"]
    assert manifest["recommended_sequences"]["new_project_to_candidate"] == ["workflow_run", "export_package", "verify_package", "doctor", "release_audit"]
    assert manifest["recommended_sequences"]["opencrab_first_manual_loop"][0] == "opencrab_request"
    assert manifest["recommended_sequences"]["manual_revision_loop"][:4] == ["topology_build", "recognition_audit", "scale_attach", "svg_patch_plan"]
    assert manifest["recommended_sequences"]["mcp_server_bootstrap"] == ["mcp_manifest", "mcp_config", "mcp_smoke", "doctor"]
    assert manifest["security"]["source_svg_in_package"].startswith("opt-in")
    assert "same-layer-svg-engine" in manifest["security"]["engine_adapter_allowlist"]
    assert "MCP raw_args cannot use" in manifest["security"]["custom_engine_policy"]

    out = tmp_path / "mcp_manifest.json"
    result = run_cli("mcp-manifest", "--output", str(out), cwd=ROOT)
    assert result.returncode == 0, result.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema"] == "crab-archi-design-mcp-tool-manifest-v1"
    assert '"tool_count"' in result.stdout


def test_mcp_config_writes_runtime_config(tmp_path: Path) -> None:
    result = run_cli("mcp-config", "--server-name", "crab-test", "--project-root", "sandbox-projects", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["schema"] == "crab-archi-design-mcp-runtime-config-v1"
    assert config["server_name"] == "crab-test"
    server = config["codex"]["mcpServers"]["crab-test"]
    assert server["command"] == "crab-archi-design-mcp"
    assert server["args"] == ["--stdio"]
    assert server["env"]["CRAB_ARCHI_PROJECT_ROOT"] == "sandbox-projects"
    assert config["oauth_worker"]["handoff_sequence"] == [
        "create-job --project-id <project-id> --source-svg <source.svg> --standards <standards.csv> --ontology-pack <pack-id> --opencrab-result-file <opencrab.json> --constraint-sketch <constraints.json> --output <job.json> --validate --strict-validation --brief",
        "validate-job --job <job.json> --strict",
        "run-job --job <job.json> --strict",
    ]
    assert config["oauth_worker"]["manual_handoff_sequence"] == ["workflow-run", "export-package", "verify-package --strict", "doctor --strict"]
    assert config["oauth_worker"]["revision_sequence"] == ["revision-run", "export-package", "verify-package --strict", "doctor --strict"]
    assert config["smoke_test_messages"][0]["method"] == "initialize"

    out = tmp_path / "mcp_runtime_config.json"
    result = run_cli("mcp-config", "--output", str(out), "--server-name", "crab-test", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema"] == "crab-archi-design-mcp-runtime-config-v1"
    assert '"server_name": "crab-test"' in result.stdout


def test_mcp_smoke_validates_runtime_config(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp_config.json"
    result = run_cli("mcp-config", "--output", str(config_path), "--project-root", str(tmp_path / "projects"), cwd=ROOT)
    assert result.returncode == 0, result.stderr

    result = run_cli("mcp-smoke", "--config", str(config_path), "--output-dir", str(tmp_path / "diagnostics"), "--strict", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report_path = Path(result.stdout.splitlines()[0])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "crab-archi-design-mcp-smoke-report-v1"
    assert report["status"] == "pass"
    assert report["checks"]["initialize_ok"] is True
    assert report["checks"]["tools_list_ok"] is True
    assert report["checks"]["create_job_tool_available"] is True
    assert report["checks"]["run_job_tool_available"] is True
    assert report["checks"]["validate_job_tool_available"] is True
    assert report["checks"]["opencrab_request_tool_available"] is True
    assert report["checks"]["release_audit_tool_available"] is True
    assert report["checks"]["revision_run_tool_available"] is True
    assert report["checks"]["topology_build_tool_available"] is True
    assert "create_job" in report["tool_names"]
    assert "run_job" in report["tool_names"]
    assert "opencrab_request" in report["tool_names"]
    assert "release_audit" in report["tool_names"]
    assert "revision_run" in report["tool_names"]
    assert "workflow_run" in report["tool_names"]
    assert "topology_build" in report["tool_names"]


def read_json_line(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line, "MCP server closed stdout"
    return json.loads(line)


def write_json_line(process: subprocess.Popen[str], payload: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def test_mcp_stdio_server_lists_and_calls_tools(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    process = subprocess.Popen(
        [sys.executable, "-m", "crab_archi_design.mcp_server"],
        cwd=ROOT,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        write_json_line(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
            },
        )
        init_response = read_json_line(process)
        assert init_response["result"]["capabilities"]["tools"]["listChanged"] is False

        write_json_line(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        write_json_line(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        list_response = read_json_line(process)
        tools = {tool["name"]: tool for tool in list_response["result"]["tools"]}
        assert "create_job" in tools
        assert "run_job" in tools
        assert "validate_job" in tools
        assert "revision_run" in tools
        assert "opencrab_request" in tools
        assert "topology_build" in tools
        assert "doctor" in tools
        assert "release_audit" in tools
        assert "mcp_manifest" in tools
        assert "mcp_config" in tools
        assert tools["create_job"]["inputSchema"]["properties"]["brief"]["type"] == "boolean"
        assert tools["opencrab_request"]["inputSchema"]["properties"]["max_results"]["type"] == "integer"
        assert "project_id" in tools["workflow_run"]["inputSchema"]["properties"]

        write_json_line(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "doctor", "arguments": {"cwd": str(tmp_path), "timeout_seconds": 60}},
            },
        )
        call_response = read_json_line(process)
        result = call_response["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["returncode"] == 0
        assert "doctor_report" in result["structuredContent"]["stdout"]
        assert (tmp_path / "diagnostics").exists()

        write_json_line(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "doctor", "arguments": {"cwd": str(tmp_path), "raw_args": ["--allow-custom-engine"]}},
            },
        )
        raw_arg_response = read_json_line(process)
        assert raw_arg_response["error"]["code"] == -32602
        assert "raw_args may not include --allow-custom-engine" in raw_arg_response["error"]["message"]
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        process.wait(timeout=5)


def test_prompt_and_sketch_intents(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect x='1' y='1' width='8' height='8'/><text x='2' y='2'>그리너리 라운지</text></svg>",
        encoding="utf-8",
    )
    sketch = tmp_path / "sketch.json"
    sketch.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "s1", "mode": "open_connection", "points": [[0, 0], [1, 1]]}],
            }
        ),
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text("세대,프로그램,면적\n900세대,그리너리 라운지,80\n900세대,피트니스,70\n", encoding="utf-8")

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(Path(result.stdout.strip()).read_text(encoding="utf-8"))
    assert manifest["opencrab_mcp"]["required"] is True
    assert manifest["opencrab_mcp"]["homepage"] == "https://opencrab.sh"

    result = run_cli("--project-root", str(tmp_path / "projects"), "recognize-svg", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    recognition_manifest = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert recognition_manifest["status"] == "active"
    assert recognition_manifest["program_label_count"] == 1
    assert recognition_manifest["program_label_candidates"][0]["role_hint"] == "greenery_lounge"

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "prompt-edit",
        "--project-id",
        "demo",
        "--text",
        "Open the greenery lounge toward the hall and lock parking.",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    prompt_intent = Path(result.stdout.strip())
    data = json.loads(prompt_intent.read_text(encoding="utf-8"))
    assert data["schema"] == "crab-archi-design-natural-language-edit-intent-v1"
    assert any(item["target"] == "greenery_lounge" for item in data["operations"])
    assert any(item["target"] == "protected_constraints" for item in data["operations"])

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "sketch-intent",
        "--project-id",
        "demo",
        "--sketch",
        str(sketch),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    sketch_intent = Path(result.stdout.strip())
    data = json.loads(sketch_intent.read_text(encoding="utf-8"))
    assert data["schema"] == "crab-archi-design-doodle-edit-intent-v1"
    assert data["operations"][0]["edit_type"] == "open_connection"

    result = run_cli("--project-root", str(tmp_path / "projects"), "qa", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert '"status": "review_required"' in result.stdout

    evidence_source = tmp_path / "evidence.json"
    evidence_source.write_text(
        json.dumps({"node_id": "resource:dataset:community_svg_topology_ontology_v2", "quality_status": "pass"}),
        encoding="utf-8",
    )
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "evidence-attach",
        "--project-id",
        "demo",
        "--source",
        "localcrab",
        "--pack-id",
        "community_svg_topology_ontology_v2",
        "--summary",
        "LocalCrab pack quality gate passed.",
        "--source-file",
        str(evidence_source),
        "--metadata",
        "node_count=36168",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    evidence_manifest = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert evidence_manifest["status"] == "verified"
    assert evidence_manifest["evidence_count"] == 1

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "constraint-attach",
        "--project-id",
        "demo",
        "--sketch",
        str(sketch),
        "--role",
        "lock",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    constraint_manifest = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert constraint_manifest["status"] == "active"
    assert constraint_manifest["enforced_constraint_count"] == 1

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "standards-attach",
        "--project-id",
        "demo",
        "--file",
        str(standards),
        "--households",
        "900",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    standards_manifest = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert standards_manifest["status"] == "active"
    assert standards_manifest["standard_count"] == 1
    assert standards_manifest["standard_items"][0]["payload"]["matched_household_row_count"] == 2

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "scale-attach",
        "--project-id",
        "demo",
        "--known-mm",
        "8400",
        "--known-world-length",
        "100",
        "--evidence",
        "User-confirmed grid dimension.",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    scale_manifest = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert scale_manifest["status"] == "active"
    assert scale_manifest["selected_scale"]["mm_per_world"] == 84.0

    result = run_cli("--project-root", str(tmp_path / "projects"), "topology-build", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    topology_manifest = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert topology_manifest["status"] == "active"
    assert topology_manifest["node_count"] > 0
    assert topology_manifest["edge_count"] > 0

    result = run_cli("--project-root", str(tmp_path / "projects"), "qa", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert '"status": "pass"' in result.stdout
    assert '"recognition_manifest_active": true' in result.stdout
    assert '"topology_manifest_active": true' in result.stdout
    assert '"opencrab_evidence_verified": true' in result.stdout
    assert '"constraint_manifest_active": true' in result.stdout
    assert '"standards_manifest_active": true' in result.stdout

    result = run_cli("--project-root", str(tmp_path / "projects"), "edit-brief", "--project-id", "demo", "--intent", "all", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    brief_json = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    brief_md = Path(result.stdout.splitlines()[1]).read_text(encoding="utf-8")
    assert brief_json["status"] == "pass"
    assert brief_json["checks"]["recognition_manifest_active"] is True
    assert brief_json["checks"]["topology_manifest_active"] is True
    assert brief_json["checks"]["opencrab_evidence_verified"] is True
    assert brief_json["checks"]["constraint_manifest_active"] is True
    assert brief_json["checks"]["standards_manifest_active"] is True
    assert brief_json["checks"]["sketch_points_inside_viewbox"] is True
    assert "greenery_lounge" in brief_md
    assert "Recognition" in brief_md
    assert "Topology" in brief_md
    assert "Constraints" in brief_md
    assert "Standards" in brief_md

    result = run_cli("--project-root", str(tmp_path / "projects"), "project-status", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    status_json = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert status_json["overall_status"] == "ready_for_apply"
    assert status_json["gates"]["recognition_manifest_active"] is True
    assert status_json["gates"]["topology_manifest_active"] is True
    assert status_json["gates"]["opencrab_evidence_verified"] is True
    assert status_json["gates"]["constraint_manifest_active"] is True
    assert status_json["gates"]["standards_manifest_active"] is True
    assert status_json["gates"]["edit_intent_exists"] is True
    assert status_json["metrics"]["program_label_count"] == 1
    assert status_json["metrics"]["topology_node_count"] > 0
    assert status_json["metrics"]["topology_edge_count"] > 0
    assert status_json["metrics"]["edit_intent_count"] == 2

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "design-handoff",
        "--project-id",
        "demo",
        "--intent",
        "all",
        "--task",
        "Prepare a native SVG community layout alternative.",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    handoff_json = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    handoff_md = Path(result.stdout.splitlines()[1]).read_text(encoding="utf-8")
    assert handoff_json["status"] == "pass"
    assert handoff_json["schema"] == "crab-archi-design-design-handoff-v1"
    assert handoff_json["project_status"]["overall_status"] == "ready_for_apply"
    assert handoff_json["handoff_checks"]["latest_edit_brief_pass"] is True
    assert handoff_json["knowledge_context"]["recognition"]["program_label_count"] == 1
    assert handoff_json["knowledge_context"]["topology"]["status"] == "active"
    assert handoff_json["knowledge_context"]["evidence_items"][0]["pack_id"] == "community_svg_topology_ontology_v2"
    assert handoff_json["knowledge_context"]["standards_excerpt"][0]["프로그램"] == "그리너리 라운지"
    assert "OpenCrab MCP evidence" in handoff_json["prompt_blocks"]["system_prompt"]
    assert "greenery_lounge" in handoff_md
    assert "native_svg_alternative" in handoff_md


def test_recognize_svg_extracts_geometry_candidates(tmp_path: Path) -> None:
    source_svg = tmp_path / "geometry.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
              <rect x="50" y="50" width="700" height="420" fill="none" stroke="#111" stroke-width="5"/>
              <rect x="100" y="100" width="22" height="22" fill="#111"/>
              <rect x="50" y="210" width="700" height="8" fill="#111"/>
              <line x1="90" y1="260" x2="690" y2="260" stroke="#111" stroke-width="7"/>
              <text x="80" y="90">피트니스</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "geometry-demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "recognize-svg", "--project-id", "geometry-demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    recognition = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    summary = recognition["geometry_summary"]
    assert recognition["program_label_candidates"][0]["role_hint"] == "fitness_gx"
    assert summary["primitive_bbox_count"] >= 3
    assert summary["column_candidate_count"] >= 1
    assert summary["wall_candidate_count"] >= 1
    assert summary["room_envelope_candidate_count"] >= 1
    assert recognition["geometry_candidates"]["column_candidates"][0]["bbox"]["width"] == 22.0


def test_recognize_svg_extracts_text_matrix_transform_positions(tmp_path: Path) -> None:
    source_svg = tmp_path / "matrix-labels.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
              <rect x="50" y="50" width="700" height="420" fill="none" stroke="#111" stroke-width="5"/>
              <rect x="100" y="100" width="22" height="22" fill="#111"/>
              <text transform="matrix(1 -9.3132e-10 9.3132e-10 1 123.5 234.5)">작은도서관</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "matrix-label-demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "recognize-svg", "--project-id", "matrix-label-demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    recognition = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    label = recognition["program_label_candidates"][0]
    assert label["role_hint"] == "greenery_lounge"
    assert label["x"] == 123.5
    assert label["y"] == 234.5
    assert label["position_source"] == "matrix_transform"


def test_recognize_svg_v2_writes_world_coordinate_ir(tmp_path: Path) -> None:
    source_svg = tmp_path / "world.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
              <g id="community" transform="translate(10 5)">
                <rect id="room" x="1" y="2" width="3" height="4" fill="none" stroke="#111" stroke-width="0.5"/>
                <text id="label" transform="matrix(1 0 0 1 20 15)">피트니스</text>
              </g>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "world-demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "recognize-svg-v2", "--project-id", "world-demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    ir = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert ir["schema"] == "crab-archi-design-recognition-ir-v2"
    assert ir["parser_version"] == "2.0.0"
    assert ir["document"]["coordinate_space"] == "world"
    assert ir["document"]["unit_scale_mm"] == 1.0
    assert ir["document"]["unit_scale_x_mm"] == 1.0
    assert ir["document"]["unit_scale_y_mm"] == 1.0
    assert ir["document"]["unit_scale_consistent"] is True
    assert ir["document"]["unit_scale_relative_error"] == 0.0
    assert ir["document"]["physical_width_mm"] == 100.0
    assert ir["document"]["physical_height_mm"] == 50.0
    room = next(node for node in ir["nodes"] if node["source_id"] == "room")
    label = next(node for node in ir["nodes"] if node["source_id"] == "label")
    assert room["bbox"] == {"x": 11.0, "y": 7.0, "w": 3.0, "h": 4.0}
    assert room["bbox_mm"] == {"x": 11.0, "y": 7.0, "w": 3.0, "h": 4.0}
    assert room["area"] == 12.0
    assert room["area_mm2"] == 12.0
    assert room["area_m2"] == 0.000012
    assert room["perimeter"] == 14.0
    assert room["perimeter_mm"] == 14.0
    assert ir["summary"]["physical_metric_node_count"] >= 2
    assert label["text"]["anchor"] == [30.0, 20.0]


def test_topology_build_creates_target_graph(tmp_path: Path) -> None:
    source_svg = tmp_path / "topology.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
              <rect x="50" y="50" width="700" height="420" fill="none" stroke="#111" stroke-width="5"/>
              <line id="space-left" x1="50" y1="50" x2="50" y2="470" stroke="#111" stroke-width="5"/>
              <line id="space-right" x1="750" y1="50" x2="750" y2="470" stroke="#111" stroke-width="5"/>
              <line id="space-top" x1="50" y1="50" x2="750" y2="50" stroke="#111" stroke-width="5"/>
              <line id="space-bottom" x1="50" y1="470" x2="750" y2="470" stroke="#111" stroke-width="5"/>
              <rect x="100" y="100" width="22" height="22" fill="#111"/>
              <line x1="90" y1="260" x2="690" y2="260" stroke="#111" stroke-width="7"/>
              <text x="80" y="90">피트니스</text>
              <text x="360" y="120">홀</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text("세대,프로그램,면적\n900세대,피트니스,70\n900세대,홀,40\n", encoding="utf-8")
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[50, 50], [750, 50], [750, 470], [50, 470]]}],
            }
        ),
        encoding="utf-8",
    )

    for args in [
        ("init", "--project-id", "topology-demo", "--source-svg", str(source_svg), "--ontology-pack", "community_svg_topology_ontology_v2"),
        ("recognize-svg", "--project-id", "topology-demo"),
        ("recognize-svg-v2", "--project-id", "topology-demo"),
        ("standards-attach", "--project-id", "topology-demo", "--file", str(standards), "--households", "900"),
        ("constraint-attach", "--project-id", "topology-demo", "--sketch", str(constraint)),
    ]:
        result = run_cli("--project-root", str(tmp_path / "projects"), *args, cwd=ROOT)
        assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "topology-build", "--project-id", "topology-demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    topology = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert topology["schema"] == "crab-archi-design-topology-manifest-v1"
    assert topology["status"] == "active"
    assert topology["node_count"] > 0
    assert topology["edge_count"] > 0
    node_types = {node["type"] for node in topology["nodes"]}
    edge_types = {edge["type"] for edge in topology["edges"]}
    assert {"program_label", "room_envelope", "space_region", "program_cluster", "structural_column", "standard_program", "constraint"} <= node_types
    assert {"label_inside_envelope", "label_inside_space_region", "space_region_member_of_program_cluster", "column_inside_envelope", "standard_applies_to_program", "ontology_adjacency_target"} <= edge_types
    assert topology["graph_summary"]["protected_node_count"] >= 1
    assert topology["graph_summary"]["recognition_source"] == "recognition_ir_v2"
    assert topology["source_manifests"]["recognition_ir_v2"].endswith("recognition_ir_v2.json")
    assert topology["quality_gates"]["recognition_ir_v2_active"] is True
    assert any(node["source"] == "recognition_ir_v2.wall_ray_space_region" for node in topology["nodes"] if node["type"] == "space_region")


def test_recognition_audit_gates_svg_mutation_readiness(tmp_path: Path) -> None:
    source_svg = tmp_path / "audit.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 340">
              <rect x="30" y="30" width="360" height="250" fill="none" stroke="#111" stroke-width="5"/>
              <line x1="60" y1="160" x2="360" y2="160" stroke="#111" stroke-width="7"/>
              <rect x="130" y="145" width="12" height="12" fill="#111"/>
              <text transform="matrix(1 0 0 1 80 90)">작은도서관</text>
              <text x="80" y="220">피트니스</text>
              <text x="300" y="220">홀</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [
                    {"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[30, 30], [390, 30], [390, 280], [30, 280], [30, 30]]},
                    {"stroke_id": "mutable", "mode": "mutable", "target_hint": "internal_community_program_rework", "points": [[50, 50], [370, 50], [370, 260], [50, 260], [50, 50]]},
                    {"stroke_id": "no-go", "mode": "no_go", "target_hint": "parking_core_no_go", "points": [[400, 40], [470, 40], [470, 130], [400, 130], [400, 40]]},
                ],
            }
        ),
        encoding="utf-8",
    )

    for args in [
        ("init", "--project-id", "audit-demo", "--source-svg", str(source_svg), "--ontology-pack", "community_svg_topology_ontology_v2"),
        ("recognize-svg", "--project-id", "audit-demo"),
        ("constraint-attach", "--project-id", "audit-demo", "--sketch", str(constraint)),
        ("topology-build", "--project-id", "audit-demo"),
    ]:
        result = run_cli("--project-root", str(tmp_path / "projects"), *args, cwd=ROOT)
        assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "recognition-audit", "--project-id", "audit-demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    audit = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert audit["status"] == "pass"
    assert audit["design_generation_policy"] == "allow_projection_and_svg_mutation"
    assert all(audit["gates"].values())
    assert audit["metrics"]["positioned_program_label_count"] == 3
    assert audit["metrics"]["column_candidate_count"] >= 1


def test_svg_patch_plan_targets_existing_mutable_elements(tmp_path: Path) -> None:
    source_svg = tmp_path / "patch-plan.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 340">
              <rect x="30" y="30" width="360" height="250" fill="none" stroke="#111" stroke-width="5"/>
              <line id="space-left" x1="30" y1="30" x2="30" y2="280" stroke="#111" stroke-width="5"/>
              <line id="space-right" x1="390" y1="30" x2="390" y2="280" stroke="#111" stroke-width="5"/>
              <line id="space-top" x1="30" y1="30" x2="390" y2="30" stroke="#111" stroke-width="5"/>
              <line id="space-bottom" x1="30" y1="280" x2="390" y2="280" stroke="#111" stroke-width="5"/>
              <line x1="60" y1="160" x2="360" y2="160" stroke="#111" stroke-width="7"/>
              <line x1="250" y1="50" x2="250" y2="260" stroke="#111" stroke-width="7"/>
              <rect x="130" y="145" width="12" height="12" fill="#111"/>
              <text transform="matrix(1 0 0 1 80 90)">작은도서관</text>
              <text x="80" y="220">피트니스</text>
              <text x="300" y="220">홀</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [
                    {"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[30, 30], [390, 30], [390, 280], [30, 280], [30, 30]]},
                    {"stroke_id": "mutable", "mode": "mutable", "target_hint": "internal_community_program_rework", "points": [[50, 50], [370, 50], [370, 260], [50, 260], [50, 50]]},
                    {"stroke_id": "no-go", "mode": "no_go", "target_hint": "parking_core_no_go", "points": [[400, 40], [470, 40], [470, 130], [400, 130], [400, 40]]},
                ],
            }
        ),
        encoding="utf-8",
    )

    for args in [
        ("init", "--project-id", "patch-demo", "--source-svg", str(source_svg), "--ontology-pack", "community_svg_topology_ontology_v2"),
        ("recognize-svg", "--project-id", "patch-demo"),
        ("recognize-svg-v2", "--project-id", "patch-demo"),
        ("constraint-attach", "--project-id", "patch-demo", "--sketch", str(constraint)),
        ("topology-build", "--project-id", "patch-demo"),
        ("recognition-audit", "--project-id", "patch-demo"),
    ]:
        result = run_cli("--project-root", str(tmp_path / "projects"), *args, cwd=ROOT)
        assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "svg-patch-plan", "--project-id", "patch-demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    plan = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert plan["schema"] == "crab-archi-design-svg-patch-plan-v1"
    assert plan["status"] == "pass"
    assert plan["recognition_source"] == "recognition_ir_v2"
    assert plan["mutation_strategy"] == "same_layer_element_patch"
    assert plan["gates"]["overlay_generation_disallowed"] is True
    assert plan["metrics"]["mutable_candidate_count"] >= 1
    assert plan["metrics"]["opening_candidate_count"] >= 1
    assert plan["metrics"]["endpoint_move_candidate_count"] >= 1
    assert plan["metrics"]["program_anchor_count"] >= 3
    assert plan["metrics"]["program_cluster_count"] >= 1
    assert plan["metrics"]["program_cluster_candidate_count"] >= 1
    assert plan["metrics"]["editable_source_geometry_candidate_count"] >= 1
    assert plan["program_clusters"]
    assert plan["program_anchors"][0]["position_source"] == "topology.label_inside_space_region"
    assert plan["same_layer_mutable_candidates"][0]["addressing"] == "source_svg_element_index"
    assert plan["same_layer_mutable_candidates"][0]["recognition_source"] == "recognition_ir_v2.nodes"
    assert plan["same_layer_mutable_candidates"][0]["source_document_index"] == plan["same_layer_mutable_candidates"][0]["element_index"]
    assert plan["same_layer_mutable_candidates"][0]["recognition_node_id"]
    assert plan["same_layer_mutable_candidates"][0]["mutation_policy"] == "modify_or_remove_existing_element_only"
    assert "program_cluster_id" in plan["same_layer_mutable_candidates"][0]
    assert plan["same_layer_opening_candidates"][0]["operation"] == "split_line_for_opening"
    assert plan["same_layer_opening_candidates"][0]["mutation_policy"] == "split_existing_line_in_same_parent"
    opening_roles = {plan["same_layer_opening_candidates"][0]["program_role"], plan["same_layer_opening_candidates"][0]["connects_to_role"]}
    assert "hall_lobby" in opening_roles
    assert plan["same_layer_opening_candidates"][0]["topology_evidence"] == "OpenCrab topology prior"
    assert plan["same_layer_opening_candidates"][0]["adjacency_edge_id"]
    assert plan["same_layer_opening_candidates"][0]["opening_priority"] > 0
    assert plan["same_layer_endpoint_move_candidates"]
    assert plan["same_layer_endpoint_move_candidates"][0]["operation"] == "move_line_endpoint"
    assert plan["same_layer_endpoint_move_candidates"][0]["mutation_policy"] == "move_existing_line_endpoint_in_same_parent"
    assert plan["same_layer_endpoint_move_candidates"][0]["endpoint"] in {"start", "end"}
    assert plan["same_layer_endpoint_move_candidates"][0]["dx"] or plan["same_layer_endpoint_move_candidates"][0]["dy"]
    endpoint_template = next(item for item in plan["operation_templates"] if item["operation"] == "move_or_extend_existing_wall_segment")
    assert endpoint_template["target"] == "same_layer_endpoint_move_candidates"


def test_solver_same_layer_geometry_patch_collapses_existing_line() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80">
          <rect id="shell" x="5" y="5" width="90" height="70" fill="none" stroke="#111"/>
          <line id="partition" x1="20" y1="40" x2="80" y2="40" stroke="#111" stroke-width="5"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [
            {
                "element_index": 3,
                "tag": "line",
                "role_hint": "wall_candidate",
                "bbox": {"x": 20, "y": 40, "width": 60, "height": 0},
                "patch_priority": 100,
                "mutation_policy": "modify_or_remove_existing_element_only",
                "program_cluster_id": "program_cluster_001",
                "program_role": "greenery_lounge",
            }
        ],
        "locked_candidates": [
            {
                "element_index": 2,
                "tag": "rect",
                "role_hint": "room_envelope_candidate",
                "reason": "inside protected no-go/lock/protect polygon",
            }
        ],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4)
    partition = next(element for element in root.iter() if element.attrib.get("id") == "partition")
    shell = next(element for element in root.iter() if element.attrib.get("id") == "shell")

    assert summary["mutation_strategy"] == "same_layer_geometry_patch"
    assert summary["same_layer_geometry_mutation_count"] == 1
    assert summary["same_layer_removal_count"] == 1
    assert summary["locked_preservation"]["locked_candidate_count"] == 1
    assert summary["locked_preservation"]["locked_geometry_unchanged"] is True
    assert shell.attrib["x"] == "5"
    assert shell.attrib["width"] == "90"
    assert partition.attrib["x2"] == partition.attrib["x1"]
    assert partition.attrib["y2"] == partition.attrib["y1"]
    assert partition.attrib["display"] == "none"
    assert partition.attrib["data-crab-original-x2"] == "80"
    assert partition.attrib["data-crab-program-role"] == "greenery_lounge"


def test_solver_same_layer_geometry_patch_collapses_polyline_and_path_partitions() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80">
          <polyline id="poly-partition" points="10,20 40,20 80,20" fill="none" stroke="#111"/>
          <path id="path-partition" d="M 10 50 L 40 50 L 80 50" fill="none" stroke="#111"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [
            {
                "element_index": 2,
                "tag": "polyline",
                "role_hint": "wall_candidate",
                "bbox": {"x": 10, "y": 20, "width": 70, "height": 0},
                "patch_priority": 120,
                "mutation_policy": "modify_or_remove_existing_element_only",
            },
            {
                "element_index": 3,
                "tag": "path",
                "role_hint": "wall_candidate",
                "bbox": {"x": 10, "y": 50, "width": 70, "height": 0},
                "patch_priority": 110,
                "mutation_policy": "modify_or_remove_existing_element_only",
            },
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4)
    polyline = next(element for element in root.iter() if element.attrib.get("id") == "poly-partition")
    path = next(element for element in root.iter() if element.attrib.get("id") == "path-partition")

    assert summary["same_layer_removal_count"] == 2
    assert summary["same_layer_geometry_mutation_count"] == 2
    assert summary["edit_capability_summary"]["partition_remove"]["supported_count"] == 2
    assert summary["partition_remove_skips"] == []
    assert polyline.attrib["points"] == "10,20 10,20"
    assert polyline.attrib["data-crab-original-points"] == "10,20 40,20 80,20"
    assert path.attrib["d"] == "M 10,50 L 10,50"
    assert path.attrib["data-crab-original-d"] == "M 10 50 L 40 50 L 80 50"
    assert path.attrib["display"] == "none"


def test_solver_same_layer_geometry_patch_skips_curved_path_partition_remove() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80">
          <path id="curved-partition" d="M 10 40 C 30 20 60 60 90 40" fill="none" stroke="#111"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [
            {
                "element_index": 2,
                "tag": "path",
                "role_hint": "wall_candidate",
                "bbox": {"x": 10, "y": 20, "width": 80, "height": 40},
                "patch_priority": 120,
                "mutation_policy": "modify_or_remove_existing_element_only",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4)
    path = next(element for element in root.iter() if element.attrib.get("id") == "curved-partition")
    skip = summary["partition_remove_skips"][0]

    assert summary["same_layer_removal_count"] == 0
    assert summary["same_layer_geometry_mutation_count"] == 0
    assert summary["edit_capability_summary"]["partition_remove"]["review_required_count"] == 1
    assert summary["edit_capability_totals"]["review_required_count"] == 1
    assert summary["edit_capability_review_required_count"] == 1
    assert summary["edit_capability_manual_review_count"] == 1
    assert skip["review_required"] is True
    assert skip["reason"] == "unsupported path commands for CAD-like edit: C"
    assert path.attrib["d"] == "M 10 40 C 30 20 60 60 90 40"
    assert "display" not in path.attrib


def test_solver_same_layer_geometry_patch_skips_locked_mutation_target() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80">
          <line id="protected-wall" x1="20" y1="40" x2="80" y2="40" stroke="#111" stroke-width="5"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [
            {
                "element_index": 2,
                "tag": "line",
                "role_hint": "wall_candidate",
                "bbox": {"x": 20, "y": 40, "width": 60, "height": 0},
                "patch_priority": 100,
                "mutation_policy": "modify_or_remove_existing_element_only",
            }
        ],
        "locked_candidates": [
            {
                "element_index": 2,
                "tag": "line",
                "role_hint": "wall_candidate",
                "reason": "inside protected no-go/lock/protect polygon",
            }
        ],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4)

    protected_wall = next(element for element in root.iter() if element.attrib.get("id") == "protected-wall")
    assert summary["same_layer_removal_count"] == 0
    assert summary["locked_preservation"]["locked_geometry_unchanged"] is True
    assert summary["locked_target_skip_count"] == 1
    assert summary["locked_targets_not_selected"] is False
    assert summary["locked_target_skips"][0]["element_index"] == 2
    assert protected_wall.attrib["x2"] == "80"
    assert "display" not in protected_wall.attrib


def test_solver_split_line_for_opening_creates_same_layer_segments() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40">
          <line id="wall" x1="10" y1="20" x2="110" y2="20" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    wall = next(element for element in root.iter() if element.attrib.get("id") == "wall")

    result = split_line_for_opening(root, wall, 0.4, 0.6, operation_id="door_001")
    lines = [element for element in root.iter() if element.tag.endswith("line")]
    before, after = lines

    assert result["status"] == "applied"
    assert result["same_layer_segment_added"] is True
    assert len(lines) == 2
    assert before.attrib["x1"] == "10"
    assert before.attrib["x2"] == "50"
    assert before.attrib["data-crab-original-x2"] == "110"
    assert before.attrib["data-crab-opening-segment"] == "before"
    assert after.attrib["id"] == "wall__crab_door_001_after"
    assert after.attrib["x1"] == "70"
    assert after.attrib["x2"] == "110"
    assert after.attrib["data-crab-derived-from"] == "wall"
    assert after.attrib["data-crab-opening-segment"] == "after"
    assert all(element.attrib["data-crab-action"] == "split_line_for_opening" for element in lines)


def test_solver_same_layer_opening_splits_existing_path() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <path id="wall-path" d="M 10 20 L 60 20 L 110 20" fill="none" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_opening_candidates": [
            {
                "operation": "split_line_for_opening",
                "operation_id": "opening_001",
                "target_element_index": 2,
                "tag": "path",
                "mutation_policy": "split_existing_path_in_same_parent",
                "opening_start_ratio": 0.4,
                "opening_end_ratio": 0.6,
                "program_cluster_id": "program_cluster_path",
                "program_role": "golf_screen",
                "connects_to_role": "hall_lobby",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_openings=True, max_openings=4)
    paths = [element for element in root.iter() if element.tag.endswith("path")]
    before, after = paths

    assert summary["same_layer_opening_split_count"] == 1
    assert summary["same_layer_segment_added_count"] == 1
    assert summary["program_cluster_mutation_count"] == 1
    assert len(paths) == 2
    assert before.attrib["d"] == "M 10,20 L 50,20"
    assert before.attrib["data-crab-original-d"] == "M 10 20 L 60 20 L 110 20"
    assert before.attrib["data-crab-action"] == "split_path_for_opening"
    assert before.attrib["data-crab-opening-segment"] == "before"
    assert after.attrib["id"] == "wall-path__crab_opening_001_after"
    assert after.attrib["d"] == "M 70,20 L 110,20"
    assert after.attrib["data-crab-derived-from"] == "wall-path"
    assert after.attrib["data-crab-action"] == "split_path_for_opening"
    assert after.attrib["data-crab-opening-segment"] == "after"
    assert summary["opening_mutations"][0]["opening"] == {"x1": 50.0, "y1": 20.0, "x2": 70.0, "y2": 20.0}


def test_solver_same_layer_opening_splits_existing_polyline() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <polyline id="wall-polyline" points="10,20 60,20 110,20" fill="none" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_opening_candidates": [
            {
                "operation": "split_line_for_opening",
                "operation_id": "opening_001",
                "target_element_index": 2,
                "tag": "polyline",
                "mutation_policy": "split_existing_polyline_in_same_parent",
                "opening_start_ratio": 0.4,
                "opening_end_ratio": 0.6,
                "program_cluster_id": "program_cluster_polyline",
                "program_role": "greenery_lounge",
                "connects_to_role": "hall_lobby",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_openings=True, max_openings=4)
    polylines = [element for element in root.iter() if element.tag.endswith("polyline")]
    before, after = polylines

    assert summary["same_layer_opening_split_count"] == 1
    assert summary["same_layer_segment_added_count"] == 1
    assert summary["edit_capability_summary"]["opening_split"]["supported_count"] == 1
    assert before.attrib["points"] == "10,20 50,20"
    assert before.attrib["data-crab-original-points"] == "10,20 60,20 110,20"
    assert before.attrib["data-crab-action"] == "split_polyline_for_opening"
    assert after.attrib["id"] == "wall-polyline__crab_opening_001_after"
    assert after.attrib["points"] == "70,20 110,20"
    assert after.attrib["data-crab-derived-from"] == "wall-polyline"
    assert after.attrib["data-crab-action"] == "split_polyline_for_opening"
    assert summary["opening_mutations"][0]["opening"] == {"x1": 50.0, "y1": 20.0, "x2": 70.0, "y2": 20.0}


def test_solver_same_layer_opening_splits_transformed_polyline_by_world_length() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 140">
          <g id="scaled" transform="scale(2 1)">
            <polyline id="wall-polyline" points="0,0 100,0 100,100" fill="none" stroke="#111" stroke-width="4"/>
          </g>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_opening_candidates": [
            {
                "operation": "split_line_for_opening",
                "operation_id": "opening_001",
                "target_element_index": 3,
                "tag": "polyline",
                "mutation_policy": "split_existing_polyline_in_same_parent",
                "opening_start_ratio": 0.5,
                "opening_end_ratio": 0.75,
                "program_cluster_id": "program_cluster_polyline",
                "program_role": "greenery_lounge",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_openings=True, max_openings=4)
    polylines = [element for element in root.iter() if element.tag.endswith("polyline")]
    before, after = polylines
    mutation = summary["opening_mutations"][0]

    assert summary["same_layer_opening_split_count"] == 1
    assert before.attrib["points"] == "0,0 75,0"
    assert after.attrib["points"] == "100,25 100,100"
    assert before.attrib["data-crab-transform-aware"] == "true"
    assert after.attrib["data-crab-transform-aware"] == "true"
    assert mutation["transform_aware"] is True
    assert mutation["opening"] == {"x1": 75.0, "y1": 0.0, "x2": 100.0, "y2": 25.0}
    assert mutation["world_opening"] == {"x1": 150.0, "y1": 0.0, "x2": 200.0, "y2": 25.0}


def test_solver_same_layer_opening_splits_transformed_path_by_world_length() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 140">
          <g id="scaled" transform="scale(2 1)">
            <path id="wall-path" d="M 0 0 L 100 0 L 100 100" fill="none" stroke="#111" stroke-width="4"/>
          </g>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_opening_candidates": [
            {
                "operation": "split_line_for_opening",
                "operation_id": "opening_001",
                "target_element_index": 3,
                "tag": "path",
                "mutation_policy": "split_existing_path_in_same_parent",
                "opening_start_ratio": 0.5,
                "opening_end_ratio": 0.75,
                "program_cluster_id": "program_cluster_path",
                "program_role": "golf_screen",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_openings=True, max_openings=4)
    paths = [element for element in root.iter() if element.tag.endswith("path")]
    before, after = paths
    mutation = summary["opening_mutations"][0]

    assert summary["same_layer_opening_split_count"] == 1
    assert before.attrib["d"] == "M 0,0 L 75,0"
    assert after.attrib["d"] == "M 100,25 L 100,100"
    assert before.attrib["data-crab-transform-aware"] == "true"
    assert after.attrib["data-crab-transform-aware"] == "true"
    assert summary["edit_capability_summary"]["opening_split"]["supported_count"] == 1
    assert summary["edit_capability_summary"]["opening_split"]["review_required_count"] == 0
    assert mutation["transform_aware"] is True
    assert mutation["opening"] == {"x1": 75.0, "y1": 0.0, "x2": 100.0, "y2": 25.0}
    assert mutation["world_opening"] == {"x1": 150.0, "y1": 0.0, "x2": 200.0, "y2": 25.0}


def test_solver_same_layer_opening_skips_curved_path() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <path id="curved-wall" d="M 10 20 C 40 10 80 30 110 20" fill="none" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_opening_candidates": [
            {
                "operation": "split_line_for_opening",
                "operation_id": "opening_001",
                "target_element_index": 2,
                "tag": "path",
                "mutation_policy": "split_existing_path_in_same_parent",
                "opening_start_ratio": 0.4,
                "opening_end_ratio": 0.6,
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_openings=True, max_openings=4)
    paths = [element for element in root.iter() if element.tag.endswith("path")]

    assert len(paths) == 1
    assert paths[0].attrib["d"] == "M 10 20 C 40 10 80 30 110 20"
    assert summary["same_layer_opening_split_count"] == 0
    assert summary["opening_skips"][0]["action"] == "split_path_for_opening"
    assert summary["opening_skips"][0]["review_required"] is True
    assert summary["opening_skips"][0]["reason"] == "unsupported path commands for CAD-like edit: C"
    capability = summary["edit_capability_summary"]["opening_split"]
    assert capability["supported_count"] == 0
    assert capability["review_required_count"] == 1
    assert summary["edit_capability_totals"]["review_required_count"] == 1
    assert summary["edit_capability_review_required_count"] == 1
    assert summary["edit_capability_manual_review_count"] == 1
    assert "C" in capability["review_required_examples"][0]["reason"]
    assert "data-crab-action" not in paths[0].attrib


def test_solver_same_layer_opening_skips_locked_target() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40">
          <line id="protected-wall" x1="10" y1="20" x2="110" y2="20" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_opening_candidates": [
            {
                "operation": "split_line_for_opening",
                "operation_id": "opening_001",
                "target_element_index": 2,
                "tag": "line",
                "mutation_policy": "split_existing_line_in_same_parent",
                "opening_start_ratio": 0.4,
                "opening_end_ratio": 0.6,
            }
        ],
        "locked_candidates": [
            {
                "element_index": 2,
                "tag": "line",
                "role_hint": "wall_candidate",
                "reason": "inside protected no-go/lock/protect polygon",
            }
        ],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_openings=True, max_openings=4)
    lines = [element for element in root.iter() if element.tag.endswith("line")]

    assert len(lines) == 1
    assert lines[0].attrib["x2"] == "110"
    assert summary["same_layer_opening_split_count"] == 0
    assert summary["locked_target_skip_count"] == 1
    assert summary["locked_targets_not_selected"] is False
    assert summary["opening_skips"][0]["operation"] == "split_line_for_opening"
    assert summary["locked_preservation"]["locked_geometry_unchanged"] is True


def test_solver_same_layer_endpoint_move_updates_existing_line() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <line id="movable-wall" x1="10" y1="20" x2="110" y2="20" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 2,
                "tag": "line",
                "endpoint": "end",
                "dx": 12,
                "dy": -5,
                "endpoint_move_priority": 900,
                "program_cluster_id": "program_cluster_001",
                "program_role": "greenery_lounge",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "movable-wall")

    assert summary["same_layer_endpoint_move_count"] == 1
    assert summary["same_layer_geometry_mutation_count"] == 1
    assert summary["program_cluster_mutation_count"] == 1
    assert summary["endpoint_move_mutations"][0]["endpoint"] == "end"
    assert wall.attrib["x2"] == "122"
    assert wall.attrib["y2"] == "15"
    assert wall.attrib["data-crab-original-x2"] == "110"
    assert wall.attrib["data-crab-original-y2"] == "20"
    assert wall.attrib["data-crab-action"] == "move_line_endpoint"


def test_solver_same_layer_endpoint_move_updates_existing_polyline() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <polyline id="movable-wall" points="10,20 50,20 110,20" fill="none" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 2,
                "tag": "polyline",
                "endpoint": "start",
                "dx": 6,
                "dy": 7,
                "endpoint_move_priority": 900,
                "program_cluster_id": "program_cluster_002",
                "program_role": "fitness_gx",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "movable-wall")

    assert summary["same_layer_endpoint_move_count"] == 1
    assert summary["same_layer_geometry_mutation_count"] == 1
    assert summary["program_cluster_mutation_count"] == 1
    assert summary["endpoint_move_mutations"][0]["tag"] == "polyline"
    assert summary["endpoint_move_mutations"][0]["endpoint"] == "start"
    assert wall.attrib["points"] == "16,27 50,20 110,20"
    assert wall.attrib["data-crab-original-points"] == "10,20 50,20 110,20"
    assert wall.attrib["data-crab-action"] == "move_line_endpoint"
    assert wall.attrib["data-crab-same-layer-mutation"] == "same_layer_polyline_endpoint_move"


def test_solver_same_layer_endpoint_move_updates_existing_path() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <path id="movable-wall" d="M 10 20 L 50 20 L 110 20" fill="none" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 2,
                "tag": "path",
                "endpoint": "end",
                "dx": 12,
                "dy": -5,
                "endpoint_move_priority": 900,
                "program_cluster_id": "program_cluster_004",
                "program_role": "golf_screen",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "movable-wall")

    assert summary["same_layer_endpoint_move_count"] == 1
    assert summary["same_layer_geometry_mutation_count"] == 1
    assert summary["program_cluster_mutation_count"] == 1
    assert summary["endpoint_move_mutations"][0]["tag"] == "path"
    assert summary["endpoint_move_mutations"][0]["endpoint"] == "end"
    assert wall.attrib["d"] == "M 10,20 L 50,20 L 122,15"
    assert wall.attrib["data-crab-original-d"] == "M 10 20 L 50 20 L 110 20"
    assert wall.attrib["data-crab-action"] == "move_line_endpoint"
    assert wall.attrib["data-crab-same-layer-mutation"] == "same_layer_path_endpoint_move"


def test_solver_same_layer_endpoint_move_skips_curved_path() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <path id="curved-wall" d="M 10 20 C 40 10 80 30 110 20" fill="none" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 2,
                "tag": "path",
                "endpoint": "end",
                "dx": 12,
                "endpoint_move_priority": 900,
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "curved-wall")

    assert summary["same_layer_endpoint_move_count"] == 0
    assert summary["endpoint_move_skips"][0]["review_required"] is True
    assert summary["endpoint_move_skips"][0]["reason"] == "unsupported path commands for CAD-like edit: C"
    assert summary["edit_capability_totals"]["review_required_count"] == 1
    assert summary["edit_capability_review_required_count"] == 1
    assert wall.attrib["d"] == "M 10 20 C 40 10 80 30 110 20"
    assert "data-crab-action" not in wall.attrib


def test_solver_endpoint_move_converts_world_delta_through_group_transform() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 80">
          <g id="scaled" transform="scale(2)">
            <line id="movable-wall" x1="10" y1="20" x2="50" y2="20" stroke="#111" stroke-width="4"/>
          </g>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 3,
                "tag": "line",
                "endpoint": "end",
                "dx": 20,
                "dy": 0,
                "endpoint_move_priority": 900,
                "program_cluster_id": "program_cluster_003",
                "program_role": "hall_lobby",
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "movable-wall")
    mutation = summary["endpoint_move_mutations"][0]

    assert summary["same_layer_endpoint_move_count"] == 1
    assert wall.attrib["x2"] == "60"
    assert wall.attrib["y2"] == "20"
    assert wall.attrib["data-crab-transform-aware"] == "true"
    assert mutation["transform_aware"] is True
    assert mutation["before"] == {"x": 50.0, "y": 20.0}
    assert mutation["after"] == {"x": 60.0, "y": 20.0}
    assert mutation["world_before"] == {"x": 100.0, "y": 40.0}
    assert mutation["world_after"] == {"x": 120.0, "y": 40.0}


def test_solver_endpoint_move_skips_noninvertible_transform_before_mutation() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 80">
          <g id="collapsed" transform="scale(0 1)">
            <line id="movable-wall" x1="10" y1="20" x2="50" y2="20" stroke="#111" stroke-width="4"/>
          </g>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 3,
                "tag": "line",
                "endpoint": "end",
                "dx": 20,
                "dy": 0,
                "endpoint_move_priority": 900,
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "movable-wall")
    skip = summary["endpoint_move_skips"][0]

    assert summary["same_layer_endpoint_move_count"] == 0
    assert summary["edit_capability_summary"]["endpoint_move"]["review_required_count"] == 1
    assert summary["edit_capability_totals"]["review_required_count"] == 1
    assert summary["edit_capability_review_required_count"] == 1
    assert summary["edit_capability_manual_review_count"] == 1
    assert skip["review_required"] is True
    assert skip["reason"] == "requires invertible accumulated transform"
    assert wall.attrib["x2"] == "50"
    assert "data-crab-action" not in wall.attrib


def test_solver_endpoint_move_converts_world_absolute_target_through_group_transform() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">
          <g id="scaled" transform="scale(2)">
            <polyline id="movable-wall" points="10,20 30,20 50,20" fill="none" stroke="#111" stroke-width="4"/>
          </g>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 3,
                "tag": "polyline",
                "endpoint": "end",
                "x": 140,
                "endpoint_move_priority": 900,
            }
        ],
        "locked_candidates": [],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "movable-wall")
    mutation = summary["endpoint_move_mutations"][0]

    assert summary["same_layer_endpoint_move_count"] == 1
    assert wall.attrib["points"] == "10,20 30,20 70,20"
    assert wall.attrib["data-crab-transform-aware"] == "true"
    assert mutation["world_after"] == {"x": 140.0, "y": 40.0}


def test_solver_same_layer_endpoint_move_skips_locked_target() -> None:
    root = ET.fromstring(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 60">
          <line id="locked-wall" x1="10" y1="20" x2="110" y2="20" stroke="#111" stroke-width="4"/>
        </svg>
        """
    )
    plan = {
        "status": "pass",
        "same_layer_mutable_candidates": [],
        "same_layer_endpoint_move_candidates": [
            {
                "operation": "move_line_endpoint",
                "operation_id": "endpoint_move_001",
                "target_element_index": 2,
                "tag": "line",
                "endpoint": "end",
                "dx": 12,
            }
        ],
        "locked_candidates": [
            {
                "element_index": 2,
                "tag": "line",
                "role_hint": "wall_candidate",
                "reason": "inside protected no-go/lock/protect polygon",
            }
        ],
    }

    summary = apply_same_layer_geometry_patch(root, plan, max_mutations=4, apply_endpoint_moves=True, max_endpoint_moves=4)
    wall = next(element for element in root.iter() if element.attrib.get("id") == "locked-wall")

    assert summary["same_layer_endpoint_move_count"] == 0
    assert summary["locked_target_skip_count"] == 1
    assert summary["locked_targets_not_selected"] is False
    assert summary["endpoint_move_skips"][0]["operation"] == "move_line_endpoint"
    assert wall.attrib["x2"] == "110"
    assert "data-crab-action" not in wall.attrib


def test_apply_edit_runs_same_layer_svg_engine_without_overlay(tmp_path: Path) -> None:
    source_svg = tmp_path / "same-layer.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 340">
              <rect x="30" y="30" width="360" height="250" fill="none" stroke="#111" stroke-width="5"/>
              <line id="space-left" x1="30" y1="30" x2="30" y2="280" stroke="#111" stroke-width="5"/>
              <line id="space-right" x1="390" y1="30" x2="390" y2="280" stroke="#111" stroke-width="5"/>
              <line id="space-top" x1="30" y1="30" x2="390" y2="30" stroke="#111" stroke-width="5"/>
              <line id="space-bottom" x1="30" y1="280" x2="390" y2="280" stroke="#111" stroke-width="5"/>
              <line id="partition-h" x1="60" y1="160" x2="360" y2="160" stroke="#111" stroke-width="7"/>
              <line id="partition-v" x1="250" y1="50" x2="250" y2="260" stroke="#111" stroke-width="7"/>
              <rect id="column-a" x="130" y="145" width="12" height="12" fill="#111"/>
              <text x="80" y="90">작은도서관</text>
              <text x="80" y="220">피트니스</text>
              <text x="300" y="220">홀</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text("세대,프로그램,면적_m2\n900세대,그리너리 라운지,80\n900세대,피트니스,70\n", encoding="utf-8")
    evidence_source = tmp_path / "opencrab.json"
    evidence_source.write_text(json.dumps({"quality_status": "pass"}), encoding="utf-8")
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [
                    {"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[30, 30], [390, 30], [390, 280], [30, 280], [30, 30]]},
                    {"stroke_id": "mutable", "mode": "mutable", "target_hint": "internal_community_program_rework", "points": [[50, 50], [370, 50], [370, 260], [50, 260], [50, 50]]},
                    {"stroke_id": "no-go", "mode": "no_go", "target_hint": "parking_core_no_go", "points": [[400, 40], [470, 40], [470, 130], [400, 130], [400, 40]]},
                ],
            }
        ),
        encoding="utf-8",
    )

    for args in [
        ("init", "--project-id", "same-layer-demo", "--source-svg", str(source_svg), "--ontology-pack", "community_svg_topology_ontology_v2", "--engine-adapter", "same-layer-svg-engine"),
        ("recognize-svg", "--project-id", "same-layer-demo"),
        ("recognize-svg-v2", "--project-id", "same-layer-demo"),
        ("evidence-attach", "--project-id", "same-layer-demo", "--source", "localcrab", "--pack-id", "community_svg_topology_ontology_v2", "--summary", "Topology pack evidence verified.", "--source-file", str(evidence_source)),
        ("standards-attach", "--project-id", "same-layer-demo", "--file", str(standards), "--households", "900"),
        ("constraint-attach", "--project-id", "same-layer-demo", "--sketch", str(constraint)),
        ("topology-build", "--project-id", "same-layer-demo"),
        ("recognition-audit", "--project-id", "same-layer-demo"),
        ("svg-patch-plan", "--project-id", "same-layer-demo"),
        ("prompt-edit", "--project-id", "same-layer-demo", "--text", "Merge the library and cafe into a greenery lounge while preserving columns and protected zones."),
    ]:
        result = run_cli("--project-root", str(tmp_path / "projects"), *args, cwd=ROOT)
        assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "apply-edit", "--project-id", "same-layer-demo", "--skip-preview", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["checks"]["engine_report_status_pass"] is True
    assert report["checks"]["engine_quality_gates_pass"] is True
    assert report["checks"]["candidate_hard_gates_pass"] is True
    assert report["candidate_quality"]["hard_status"] == "pass"
    assert report["engine_report_quality"]["gates"]["crab-archi-design-same-layer-engine-report-v1"]["locked_geometry_unchanged"] is True
    assert report["candidate_quality"]["gate_groups"]["engine_hard"]["crab-archi-design-same-layer-engine-report-v1.locked_geometry_unchanged"] is True
    assert report["engine_report_quality"]["gates"]["crab-archi-design-same-layer-engine-report-v1"]["locked_targets_not_selected"] is True
    assert report["engine_report_quality"]["gates"]["crab-archi-design-same-layer-engine-report-v1"]["same_layer_endpoint_moves_applied_or_not_requested"] is True
    alternative = Path(report["copied_artifacts"]["svg"][0])
    alternative_text = alternative.read_text(encoding="utf-8")
    assert "crab_archi_design_same_layer_engine_candidate" in alternative_text
    assert "data-crab-same-layer-mutation" in alternative_text
    assert 'data-crab-action="remove_internal_partition"' in alternative_text
    assert 'display="none"' in alternative_text
    assert "<image" not in alternative_text
    assert "crab_archi_design_layout_engine_candidate" not in alternative_text

    source_count = sum(1 for _ in ET.parse(source_svg).getroot().iter())
    alternative_root = ET.parse(alternative).getroot()
    alternative_count = sum(1 for _ in alternative_root.iter())
    assert alternative_count == source_count
    removed_lines = [element for element in alternative_root.iter() if element.attrib.get("data-crab-action") == "remove_internal_partition"]
    assert removed_lines
    assert any(element.attrib.get("x2") == element.attrib.get("x1") and element.attrib.get("y2") == element.attrib.get("y1") for element in removed_lines)

    engine_report = json.loads(Path(report["copied_artifacts"]["report"][0]).read_text(encoding="utf-8"))
    assert engine_report["schema"] == "crab-archi-design-same-layer-engine-report-v1"
    assert engine_report["summary"]["mutation_strategy"] == "same_layer_geometry_patch"
    assert engine_report["summary"]["same_layer_mutation_count"] >= 1
    assert engine_report["summary"]["same_layer_geometry_mutation_count"] >= 1
    assert engine_report["summary"]["same_layer_removal_count"] >= 1
    gates = engine_report["quality"]["gates"]
    assert gates["new_overlay_elements_added"] is True
    assert gates["edit_capability_summary_present"] is True
    assert gates["edit_capability_review_counts_reported"] is True
    assert gates["existing_elements_mutated"] is True
    assert gates["existing_geometry_mutated"] is True
    assert gates["same_layer_internal_partitions_removed"] is True
    assert gates["program_cluster_targets_used"] is True

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "apply-edit",
        "--project-id",
        "same-layer-demo",
        "--skip-preview",
        "--engine-arg=--apply-openings",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    opening_report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert opening_report["status"] == "pass"
    opening_alternative = Path(opening_report["copied_artifacts"]["svg"][0])
    opening_root = ET.parse(opening_alternative).getroot()
    opening_count = sum(1 for _ in opening_root.iter())
    opening_engine_report = json.loads(Path(opening_report["copied_artifacts"]["report"][0]).read_text(encoding="utf-8"))
    assert opening_engine_report["summary"]["same_layer_opening_split_count"] >= 1
    assert opening_engine_report["summary"]["same_layer_segment_added_count"] >= 1
    assert opening_count == source_count + opening_engine_report["summary"]["same_layer_segment_added_count"]
    assert any(element.attrib.get("data-crab-action") == "split_line_for_opening" for element in opening_root.iter())
    assert opening_engine_report["quality"]["gates"]["same_layer_openings_applied_or_not_requested"] is True


def test_edit_brief_flags_out_of_viewbox_sketch(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'></svg>", encoding="utf-8")
    sketch = tmp_path / "outside_sketch.json"
    sketch.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "s1", "mode": "program_shift", "points": [[0, 0], [12, 12]]}],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    evidence_source = tmp_path / "evidence.json"
    evidence_source.write_text(json.dumps({"quality_status": "pass"}), encoding="utf-8")
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "evidence-attach",
        "--project-id",
        "demo",
        "--source-file",
        str(evidence_source),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "opencrab-request",
        "--project-id",
        "demo",
        "--intent",
        "Find precedent topology for 900-household community layout automation.",
        "--source-tool",
        "opencrab_search_documents",
        "--max-results",
        "8",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    request_path = Path(result.stdout.splitlines()[0])
    request_md_path = Path(result.stdout.splitlines()[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request_md = request_md_path.read_text(encoding="utf-8")
    assert request["schema"] == "crab-archi-design-opencrab-request-v1"
    assert request["recommended_tool_call"]["tool"] == "opencrab_search_documents"
    assert request["recommended_tool_call"]["arguments"]["limit"] == 8
    assert request["pack_id"] == "community_svg_topology_ontology_v2"
    assert "opencrab-sync" in request["next_commands"]["sync_result"]
    assert request["expected_result_file"].endswith("opencrab_result_001.json")
    assert "OpenCrab MCP Request: demo" in request_md

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "sketch-intent",
        "--project-id",
        "demo",
        "--sketch",
        str(sketch),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "edit-brief", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    brief_json = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert brief_json["status"] == "review_required"
    assert brief_json["checks"]["opencrab_evidence_verified"] is True
    assert brief_json["checks"]["sketch_points_inside_viewbox"] is False
    assert brief_json["sketch_analysis"][0]["out_of_bounds_points"] == 1


def test_opencrab_sync_attaches_mcp_evidence(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'></svg>", encoding="utf-8")
    mcp_result = tmp_path / "opencrab_result.json"
    mcp_result.write_text(
        json.dumps(
            {
                "status": "ok",
                "query": "community SVG topology 900 households",
                "answer": "Use the A-801 topology evidence and keep protected zones locked.",
                "evidence": [
                    {
                        "id": "ev-001",
                        "document_id": "doc-001",
                        "workspace_id": "workspace-001",
                        "text": "A-801 room envelope is redraw ready for greenery, fitness, golf, and wellness groups.",
                        "score": 0.91,
                        "source": "Community SVG 다중 타깃 profile 검증 Evidence",
                        "metadata": {"source_type": "mcp"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "opencrab-sync",
        "--project-id",
        "demo",
        "--result-file",
        str(mcp_result),
        "--source-tool",
        "opencrab_search_documents",
        "--metadata",
        "tool_call=live_mcp",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    sync_path = Path(result.stdout.splitlines()[0])
    evidence_path = Path(result.stdout.splitlines()[1])
    sync_json = json.loads(sync_path.read_text(encoding="utf-8"))
    evidence_manifest = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert sync_json["schema"] == "crab-archi-design-opencrab-sync-v1"
    assert sync_json["normalized_evidence_count"] == 1
    assert sync_json["normalized_results"][0]["answer"].startswith("Use the A-801")
    assert evidence_manifest["status"] == "verified"
    assert evidence_manifest["evidence_count"] == 1
    item = evidence_manifest["evidence_items"][0]
    assert item["source"] == "opencrab_mcp"
    assert item["source_tool"] == "opencrab_search_documents"
    assert item["metadata"]["tool_call"] == "live_mcp"
    assert item["metadata"]["normalized_evidence_count"] == "1"
    assert item["payload"]["evidence"][0]["id"] == "ev-001"
    assert "redraw ready" in item["payload"]["evidence"][0]["text"]


def test_apply_edit_runs_builtin_reference_engine(tmp_path: Path) -> None:
    command_cwd = tmp_path
    project_root_arg = "projects"
    source_svg = tmp_path / "original.svg"
    source_svg.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 60'><rect x='5' y='5' width='90' height='50'/><text x='10' y='12'>greenery lounge</text></svg>",
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text("households,program,area\n900,greenery_lounge,80\n900,fitness,70\n", encoding="utf-8")
    constraint_sketch = tmp_path / "constraint_sketch.json"
    constraint_sketch.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "c1", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[0, 0], [100, 0], [100, 60], [0, 60]]}],
            }
        ),
        encoding="utf-8",
    )
    evidence_source = tmp_path / "opencrab_result.json"
    evidence_source.write_text(
        json.dumps(
            {
                "status": "ok",
                "query": "community SVG topology",
                "evidence": [{"id": "ev-001", "text": "Reference topology evidence is available.", "source": "OpenCrab"}],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "--project-root",
        project_root_arg,
        "init",
        "--project-id",
        "demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        "--engine-adapter",
        "reference-svg-engine",
        cwd=command_cwd,
    )
    assert result.returncode == 0, result.stderr
    result = run_cli("--project-root", project_root_arg, "recognize-svg", "--project-id", "demo", cwd=command_cwd)
    assert result.returncode == 0, result.stderr
    result = run_cli("--project-root", project_root_arg, "opencrab-sync", "--project-id", "demo", "--result-file", str(evidence_source), cwd=command_cwd)
    assert result.returncode == 0, result.stderr
    result = run_cli("--project-root", project_root_arg, "standards-attach", "--project-id", "demo", "--file", str(standards), "--households", "900", cwd=command_cwd)
    assert result.returncode == 0, result.stderr
    result = run_cli("--project-root", project_root_arg, "constraint-attach", "--project-id", "demo", "--sketch", str(constraint_sketch), cwd=command_cwd)
    assert result.returncode == 0, result.stderr
    result = run_cli("--project-root", project_root_arg, "topology-build", "--project-id", "demo", cwd=command_cwd)
    assert result.returncode == 0, result.stderr
    result = run_cli(
        "--project-root",
        project_root_arg,
        "prompt-edit",
        "--project-id",
        "demo",
        "--text",
        "Open the greenery lounge toward the hall and keep protected geometry locked.",
        cwd=command_cwd,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", project_root_arg, "apply-edit", "--project-id", "demo", "--skip-preview", cwd=command_cwd)
    assert result.returncode == 0, result.stderr
    report_path = Path(result.stdout.splitlines()[0])
    if not report_path.is_absolute():
        report_path = command_cwd / report_path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["checks"]["engine_report_quality_found"] is True
    assert report["checks"]["engine_report_status_pass"] is True
    assert report["checks"]["engine_quality_gates_pass"] is True
    assert report["checks"]["native_svg_no_images"] is True
    assert report["checks"]["candidate_hard_gates_pass"] is True
    assert report["candidate_quality"]["hard_status"] == "pass"
    alternative = Path(report["copied_artifacts"]["svg"][0])
    if not alternative.is_absolute():
        alternative = command_cwd / alternative
    assert alternative.name == "alternative_001.svg"
    alternative_text = alternative.read_text(encoding="utf-8")
    assert "crab_archi_design_reference_engine_candidate" in alternative_text
    assert "<image" not in alternative_text
    engine_report_path = Path(report["copied_artifacts"]["report"][0])
    if not engine_report_path.is_absolute():
        engine_report_path = command_cwd / engine_report_path
    engine_report = json.loads(engine_report_path.read_text(encoding="utf-8"))
    assert engine_report["schema"] == "crab-archi-design-reference-engine-report-v1"
    assert engine_report["quality"]["gates"]["reference_layer_added"] is True
    assert engine_report["quality"]["gates"]["opencrab_evidence_available"] is True
    assert engine_report["summary"]["operation_count"] >= 1


def test_apply_edit_runs_builtin_layout_engine(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 340">
              <rect x="30" y="30" width="300" height="260" fill="#fff" stroke="#111"/>
              <text x="55" y="70">작은도서관</text>
              <text x="155" y="70">주민카페</text>
              <text x="55" y="210">피트니스</text>
              <text x="260" y="190">골프</text>
              <rect x="130" y="145" width="10" height="10" fill="#111"/>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text(
        "세대,프로그램,면적_m2\n"
        "900세대,그리너리 라운지,80\n"
        "900세대,피트니스,70\n"
        "900세대,골프클럽,95\n"
        "900세대,사우나/라커/샤워,85\n",
        encoding="utf-8",
    )
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [
                    {"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[30, 30], [330, 30], [390, 130], [300, 300], [40, 290], [30, 30]]},
                    {"stroke_id": "mutable", "mode": "mutable_zone", "target_hint": "internal_community_program_rework", "points": [[55, 55], [310, 55], [330, 250], [70, 270], [55, 55]]},
                    {"stroke_id": "no-go", "mode": "no_go_zone", "target_hint": "parking_core_no_go", "points": [[325, 95], [455, 130], [380, 300], [300, 255]]},
                ],
            }
        ),
        encoding="utf-8",
    )
    opencrab_result = tmp_path / "opencrab.json"
    opencrab_result.write_text(
        json.dumps(
            {
                "status": "ok",
                "query": "community room envelope topology",
                "evidence": [{"id": "e1", "text": "Use central hall, greenery lounge, fitness, golf, and wellness hierarchy.", "source": "OpenCrab"}],
            }
        ),
        encoding="utf-8",
    )

    for args in [
        (
            "init",
            "--project-id",
            "demo",
            "--source-svg",
            str(source_svg),
            "--households",
            "900",
            "--standards",
            str(standards),
            "--ontology-pack",
            "community_svg_topology_ontology_v2",
            "--engine-adapter",
            "layout-svg-engine",
        ),
        ("recognize-svg", "--project-id", "demo"),
        ("opencrab-sync", "--project-id", "demo", "--result-file", str(opencrab_result)),
        ("standards-attach", "--project-id", "demo", "--file", str(standards), "--households", "900"),
        ("constraint-attach", "--project-id", "demo", "--sketch", str(constraint)),
        ("topology-build", "--project-id", "demo"),
        ("prompt-edit", "--project-id", "demo", "--text", "Create a standards-based layout with no parking or core intrusion."),
    ]:
        result = run_cli("--project-root", str(tmp_path / "projects"), *args, cwd=ROOT)
        assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "apply-edit", "--project-id", "demo", "--skip-preview", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["checks"]["engine_report_quality_found"] is True
    assert report["checks"]["engine_report_status_pass"] is True
    assert report["checks"]["engine_quality_gates_pass"] is True
    assert report["checks"]["candidate_hard_gates_pass"] is True
    assert report["candidate_quality"]["hard_status"] == "pass"
    assert report["candidate_quality"]["gate_groups"]["engine_hard"]["crab-archi-design-layout-engine-report-v1.rooms_inside_community_shell"] is True
    alternative = Path(report["copied_artifacts"]["svg"][0])
    alternative_text = alternative.read_text(encoding="utf-8")
    assert "crab_archi_design_layout_engine_candidate" in alternative_text
    assert 'data-engine="layout-svg-engine"' in alternative_text
    assert 'data-role="recognized-column-preserved"' in alternative_text
    assert 'id="crab_archi_design_plan_detail_layer"' in alternative_text
    assert 'data-role="source-redesign-cleanup-mask"' in alternative_text
    assert 'data-mask-source="mutable_zone"' in alternative_text
    assert 'data-role="partition-wall"' in alternative_text
    assert 'data-role="door-opening"' in alternative_text
    assert 'data-role="corridor-axis"' in alternative_text
    assert 'data-role="interior-glazing"' in alternative_text
    assert "<image" not in alternative_text
    for role in ["greenery_lounge", "fitness_gx", "golf_screen", "sauna_locker_shower", "hall_lobby"]:
        assert f'data-program="{role}"' in alternative_text

    engine_report = json.loads(Path(report["copied_artifacts"]["report"][0]).read_text(encoding="utf-8"))
    assert engine_report["schema"] == "crab-archi-design-layout-engine-report-v1"
    assert engine_report["reference_only"] is False
    gates = engine_report["quality"]["gates"]
    assert gates["layout_layer_added"] is True
    assert gates["community_shell_found"] is True
    assert gates["rooms_inside_community_shell"] is True
    assert gates["no_go_intrusion_free"] is True
    assert gates["layout_coverage_sufficient"] is True
    assert gates["room_aspect_efficiency"] is True
    assert gates["redesign_cleanup_mask_applied"] is True
    assert gates["plan_detail_layer_added"] is True
    assert gates["door_openings_planned"] is True
    assert gates["corridor_axis_planned"] is True
    assert gates["standard_programs_planned"] is True
    assert gates["large_programs_present"] is True
    assert gates["large_program_hierarchy"] is True
    assert gates["recognized_columns_preserved"] is True
    assert engine_report["summary"]["recognized_column_count"] >= 1
    assert engine_report["summary"]["room_count"] >= 7
    assert engine_report["summary"]["layout_coverage_ratio"] >= 0.78
    assert engine_report["summary"]["room_shell_violations"] == []
    assert engine_report["summary"]["redesign_cleanup_mask"]["applied"] is True
    assert engine_report["summary"]["redesign_cleanup_mask"]["mask_source"] == "mutable_zone"
    assert engine_report["summary"]["redesign_cleanup_mask"]["geometry"] == "polygon"
    assert engine_report["summary"]["plan_detail"]["partition_wall_count"] >= engine_report["summary"]["room_count"]
    assert engine_report["summary"]["plan_detail"]["door_opening_count"] >= 4
    assert engine_report["summary"]["plan_detail"]["corridor_axis_count"] >= 1


def test_apply_edit_marks_review_required_when_columns_are_unrecognized(tmp_path: Path) -> None:
    source_svg = tmp_path / "no_columns.svg"
    source_svg.write_text(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 340">
              <rect x="30" y="30" width="300" height="260" fill="#fff" stroke="#111"/>
              <text x="55" y="70">작은도서관</text>
              <text x="55" y="210">피트니스</text>
              <text x="260" y="190">골프</text>
            </svg>
            """
        ).strip(),
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text(
        "세대,프로그램,면적_m2\n"
        "900세대,그리너리 라운지,80\n"
        "900세대,피트니스,70\n"
        "900세대,골프클럽,95\n"
        "900세대,사우나/라커/샤워,85\n",
        encoding="utf-8",
    )
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [
                    {"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[30, 30], [330, 30], [390, 130], [300, 300], [40, 290], [30, 30]]},
                    {"stroke_id": "mutable", "mode": "mutable_zone", "target_hint": "internal_community_program_rework", "points": [[55, 55], [310, 55], [330, 250], [70, 270], [55, 55]]},
                ],
            }
        ),
        encoding="utf-8",
    )
    opencrab_result = tmp_path / "opencrab.json"
    opencrab_result.write_text(
        json.dumps({"status": "ok", "query": "community topology", "evidence": [{"id": "e1", "text": "Use hall-linked community topology.", "source": "OpenCrab"}]}),
        encoding="utf-8",
    )
    for args in [
        ("init", "--project-id", "demo", "--source-svg", str(source_svg), "--households", "900", "--standards", str(standards), "--ontology-pack", "community_svg_topology_ontology_v2", "--engine-adapter", "layout-svg-engine"),
        ("recognize-svg", "--project-id", "demo"),
        ("opencrab-sync", "--project-id", "demo", "--result-file", str(opencrab_result)),
        ("standards-attach", "--project-id", "demo", "--file", str(standards), "--households", "900"),
        ("constraint-attach", "--project-id", "demo", "--sketch", str(constraint)),
        ("topology-build", "--project-id", "demo"),
        ("prompt-edit", "--project-id", "demo", "--text", "Create a standards-based layout with preserved structure."),
    ]:
        result = run_cli("--project-root", str(tmp_path / "projects"), *args, cwd=ROOT)
        assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "apply-edit", "--project-id", "demo", "--skip-preview", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert report["status"] == "review_required"
    assert report["checks"]["engine_report_quality_found"] is True
    assert report["checks"]["engine_report_status_pass"] is False
    assert report["checks"]["engine_quality_gates_pass"] is False
    gates = next(iter(report["engine_report_quality"]["gates"].values()))
    assert gates["recognized_columns_preserved"] is False


def test_layout_engine_repairs_rooms_inside_community_shell() -> None:
    root = ET.fromstring(
        textwrap.dedent(
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 260">
              <rect x="20" y="20" width="340" height="210" fill="#fff" stroke="#111"/>
            </svg>
            """
        ).strip()
    )
    solver_input = {
        "constraint_manifest": {
            "constraint_items": [
                {
                    "role": "community_shell",
                    "points": [[45, 45], [330, 45], [210, 145], [45, 210], [45, 45]],
                }
            ]
        },
        "standards_manifest": {
            "standard_items": [
                {
                    "payload": {
                        "selected_rows": [
                            {"세대": "900세대", "프로그램": "그리너리 라운지", "면적_m2": "80"},
                            {"세대": "900세대", "프로그램": "피트니스", "면적_m2": "70"},
                            {"세대": "900세대", "프로그램": "골프클럽", "면적_m2": "95"},
                            {"세대": "900세대", "프로그램": "사우나/라커/샤워", "면적_m2": "85"},
                        ]
                    }
                }
            ]
        },
        "recognition_manifest": {"geometry_candidates": {"column_candidates": []}},
    }

    summary = draw_layout(root, solver_input)
    assert summary["shell_found"] is True
    assert summary["room_shell_violations"] == []
    assert summary["layout_repair"]["strategy"] == "shell_aware_grid_search"
    assert summary["layout_repair"]["repair_applied"] is True
    assert summary["layout_repair"]["feasible_layout_boxes"] > 0
    assert summary["room_plan"]["selected_room_plan_shell_violation_count"] == 0
    assert summary["layout_fill_ratio"] >= 0.98
    assert summary["redesign_cleanup_mask"]["applied"] is True
    assert summary["redesign_cleanup_mask"]["mask_source"] == "community_shell"
    assert summary["plan_detail"]["door_opening_count"] >= 4


def test_workflow_run_executes_full_reference_pipeline(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 80'><rect x='8' y='8' width='104' height='64'/><text x='16' y='20'>fitness</text></svg>",
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text("households,program,area\n900,greenery_lounge,80\n900,fitness,70\n", encoding="utf-8")
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_shell", "points": [[0, 0], [120, 0], [120, 80], [0, 80]]}],
            }
        ),
        encoding="utf-8",
    )
    opencrab_result = tmp_path / "opencrab.json"
    opencrab_result.write_text(
        json.dumps({"status": "ok", "query": "fitness lounge topology", "evidence": [{"id": "e1", "text": "Fitness and lounge evidence.", "source": "OpenCrab"}]}),
        encoding="utf-8",
    )

    result = run_cli(
        "--project-root",
        "projects",
        "workflow-run",
        "--project-id",
        "demo",
        "--source-svg",
        str(source_svg),
        "--households",
        "900",
        "--standards",
        str(standards),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        "--opencrab-result-file",
        str(opencrab_result),
        "--opencrab-source-tool",
        "opencrab_search_documents",
        "--constraint-sketch",
        str(constraint),
        "--prompt",
        "Improve the greenery lounge and fitness connection while preserving protected geometry.",
        "--engine-adapter",
        "reference-svg-engine",
        "--skip-preview",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    workflow_path = tmp_path / result.stdout.splitlines()[0]
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert workflow["schema"] == "crab-archi-design-workflow-run-v1"
    assert workflow["status"] == "pass"
    assert workflow["final_project_status"]["overall_status"] == "complete_candidate_ready"
    step_status = {step["name"]: step["status"] for step in workflow["steps"]}
    assert step_status["init"] == "pass"
    assert step_status["recognize-svg"] == "pass"
    assert step_status["standards-attach"] == "pass"
    assert step_status["opencrab-sync"] == "pass"
    assert step_status["constraint-attach"] == "pass"
    assert step_status["topology-build"] == "pass"
    assert step_status["edit-brief"] == "pass"
    assert step_status["design-handoff"] == "pass"
    assert step_status["apply-edit"] == "pass"
    assert step_status["review-panel"] == "pass"
    alternative = tmp_path / workflow["latest_artifacts"]["alternative_svg"]
    panel = tmp_path / workflow["latest_artifacts"]["review_panel"]
    assert alternative.exists()
    assert panel.exists()
    assert "crab_archi_design_reference_engine_candidate" in alternative.read_text(encoding="utf-8")

    result = run_cli("--project-root", "projects", "export-package", "--project-id", "demo", "--include-source-svg", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    export_manifest_path = tmp_path / result.stdout.splitlines()[0]
    zip_path = tmp_path / result.stdout.splitlines()[1]
    export_manifest = json.loads(export_manifest_path.read_text(encoding="utf-8"))
    assert export_manifest["schema"] == "crab-archi-design-export-package-v1"
    assert export_manifest["package_status"] == "pass"
    assert export_manifest["project_status"]["overall_status"] == "complete_candidate_ready"
    assert export_manifest["file_count"] >= 10
    assert export_manifest["zip_sha256"]
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "export_manifest.json" in names
    assert any(name.endswith("project_manifest.json") for name in names)
    assert any(name.endswith("project_status.json") for name in names)
    assert any(name.endswith("topology_manifest.json") for name in names)
    assert any(name.endswith("alternative_001.svg") for name in names)
    assert any(name.endswith(".html") and "review_panel" in name for name in names)
    assert any(name.endswith("workflow_run_001.json") for name in names)
    assert any(name.endswith("original.svg") for name in names)

    result = run_cli("--project-root", "projects", "verify-package", "--zip", str(zip_path), "--strict", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    verify_report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert verify_report["schema"] == "crab-archi-design-export-verification-v1"
    assert verify_report["status"] == "pass"
    assert verify_report["checks"]["zip_integrity_ok"] is True
    assert verify_report["checks"]["archive_file_hashes_ok"] is True
    assert verify_report["missing_archive_paths"] == []
    assert verify_report["hash_mismatches"] == []

    result = run_cli(
        "--project-root",
        "projects",
        "verify-package",
        "--zip",
        str(zip_path),
        "--manifest",
        str(export_manifest_path),
        "--check-local-files",
        "--strict",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    verify_report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert verify_report["status"] == "pass"
    assert verify_report["checks"]["local_file_hashes_ok"] is True

    result = run_cli(
        "--project-root",
        "projects",
        "doctor",
        "--project-id",
        "demo",
        "--zip",
        str(zip_path),
        "--check-local-files",
        "--strict",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    doctor_report_path = Path(result.stdout.splitlines()[0])
    if not doctor_report_path.is_absolute():
        doctor_report_path = tmp_path / doctor_report_path
    doctor_report = json.loads(doctor_report_path.read_text(encoding="utf-8"))
    assert doctor_report["schema"] == "crab-archi-design-doctor-report-v1"
    assert doctor_report["status"] == "pass"
    assert doctor_report["local_checks"]["doodle_editor_exists"] is True
    assert doctor_report["local_checks"]["layout_svg_engine_exists"] is True
    assert doctor_report["project_checks"]["project_candidate_ready"] is True
    assert doctor_report["package_verification"]["status"] == "pass"
    assert doctor_report["required_checks"]["package.package_verify_pass"] is True

    result = run_cli(
        "--project-root",
        "projects",
        "release-audit",
        "--project-id",
        "demo",
        "--zip",
        str(zip_path),
        "--check-local-files",
        "--strict",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    audit_report_path = Path(result.stdout.splitlines()[0])
    if not audit_report_path.is_absolute():
        audit_report_path = tmp_path / audit_report_path
    audit_report = json.loads(audit_report_path.read_text(encoding="utf-8"))
    assert audit_report["schema"] == "crab-archi-design-release-audit-v1"
    assert audit_report["status"] == "pass"
    assert audit_report["checks"]["project_candidate_ready"] is True
    assert audit_report["checks"]["opencrab_evidence_verified"] is True
    assert audit_report["checks"]["latest_alternative_native_svg"] is True
    assert audit_report["checks"]["package_verify_pass"] is True
    assert audit_report["checks"]["doctor_pass"] is True
    assert audit_report["package_verification"]["status"] == "pass"
    assert audit_report["doctor_report"]["status"] == "pass"


def test_revision_run_executes_existing_project_loop(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 140 90'><rect x='10' y='10' width='110' height='65'/><text x='18' y='24'>greenery lounge</text><text x='18' y='52'>fitness</text></svg>",
        encoding="utf-8",
    )
    standards = tmp_path / "standards.csv"
    standards.write_text("households,program,area\n900,greenery_lounge,80\n900,fitness,70\n", encoding="utf-8")
    constraint = tmp_path / "constraint.json"
    constraint.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "shell", "mode": "community_shell", "target_hint": "community_shell", "points": [[0, 0], [140, 0], [140, 90], [0, 90]]}],
            }
        ),
        encoding="utf-8",
    )
    opencrab_result = tmp_path / "opencrab.json"
    opencrab_result.write_text(
        json.dumps({"status": "ok", "query": "greenery fitness topology", "evidence": [{"id": "e1", "text": "Keep the lounge and fitness connected to the hall.", "source": "OpenCrab"}]}),
        encoding="utf-8",
    )

    result = run_cli(
        "--project-root",
        "projects",
        "workflow-run",
        "--project-id",
        "revision-demo",
        "--source-svg",
        str(source_svg),
        "--households",
        "900",
        "--standards",
        str(standards),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        "--opencrab-result-file",
        str(opencrab_result),
        "--constraint-sketch",
        str(constraint),
        "--prompt",
        "Prepare a topology-backed community layout.",
        "--engine-adapter",
        "reference-svg-engine",
        "--skip-apply",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    workflow = json.loads((tmp_path / result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert workflow["status"] == "pass"
    assert workflow["final_project_status"]["overall_status"] == "ready_for_apply"

    result = run_cli(
        "--project-root",
        "projects",
        "revision-run",
        "--project-id",
        "revision-demo",
        "--text",
        "Open the greenery lounge further toward fitness while keeping protected geometry locked.",
        "--engine-adapter",
        "reference-svg-engine",
        "--skip-preview",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    revision_path = tmp_path / result.stdout.splitlines()[0]
    revision = json.loads(revision_path.read_text(encoding="utf-8"))
    assert revision["schema"] == "crab-archi-design-revision-run-v1"
    assert revision["status"] == "pass"
    assert revision["final_project_status"]["overall_status"] == "complete_candidate_ready"
    step_status = {step["name"]: step["status"] for step in revision["steps"]}
    assert step_status["recognize-svg"] == "skipped"
    assert step_status["topology-build"] == "pass"
    assert step_status["prompt-edit"] == "pass"
    assert step_status["edit-brief"] == "pass"
    assert step_status["design-handoff"] == "pass"
    assert step_status["apply-edit"] == "pass"
    assert step_status["review-panel"] == "pass"
    assert revision["final_project_status"]["metrics"]["edit_intent_count"] >= 2
    assert (tmp_path / revision["latest_artifacts"]["topology_manifest"]).exists()
    assert (tmp_path / revision["latest_artifacts"]["alternative_svg"]).exists()
    assert (tmp_path / revision["latest_artifacts"]["review_panel"]).exists()


def test_validate_job_spec_checks_inputs(tmp_path: Path) -> None:
    result = run_cli("validate-job", "--job", str(ROOT / "examples" / "job_spec_sample.json"), "--output-dir", str(tmp_path / "diagnostics"), "--strict", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert report["schema"] == "crab-archi-design-job-validation-v1"
    assert report["status"] == "pass"
    assert report["checks"]["schema_supported"] is True
    assert report["checks"]["source_svg_present_or_existing_project"] is True
    assert report["checks"]["standards_present_or_existing_manifest"] is True
    assert report["checks"]["opencrab_or_evidence_input_present"] is True
    assert report["checks"]["constraint_sketch_present_or_existing_manifest"] is True
    assert report["checks"]["required_files_exist"] is True
    assert report["missing_files"] == []

    bad_job = tmp_path / "bad_job.json"
    bad_job.write_text(
        json.dumps(
            {
                "schema": "crab-archi-design-job-spec-v1",
                "project_id": "bad-demo",
                "source_svg": "missing.svg",
                "standards": ["missing.csv"],
                "ontology_pack": "community_svg_topology_ontology_v2",
                "opencrab_result_file": ["missing_opencrab.json"],
                "constraint_sketch": "missing_constraints.json",
                "engine_adapter": "layout-svg-engine",
            }
        ),
        encoding="utf-8",
    )
    result = run_cli("validate-job", "--job", str(bad_job), "--output-dir", str(tmp_path / "diagnostics"), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert report["status"] == "review_required"
    assert report["checks"]["required_files_exist"] is False
    assert {item["key"] for item in report["missing_files"]} >= {"source_svg", "standards", "opencrab_result_file", "constraint_sketch"}

    custom_engine_job = tmp_path / "custom_engine_job.json"
    custom_engine_job.write_text(
        json.dumps(
            {
                "schema": "crab-archi-design-job-spec-v1",
                "project_id": "custom-engine-demo",
                "source_svg": str(ROOT / "examples" / "original_sample.svg"),
                "standards": [str(ROOT / "examples" / "area_standard_sample.csv")],
                "ontology_pack": "community_svg_topology_ontology_v2",
                "opencrab_result_file": [str(ROOT / "examples" / "opencrab_mcp_result_sample.json")],
                "constraint_sketch": str(ROOT / "examples" / "constraint_sketch_sample.json"),
                "engine_adapter": str(tmp_path / "custom_engine.py"),
            }
        ),
        encoding="utf-8",
    )
    result = run_cli("validate-job", "--job", str(custom_engine_job), "--output-dir", str(tmp_path / "diagnostics"), cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert report["status"] == "review_required"
    assert report["checks"]["engine_adapter_allowed"] is False
    assert report["engine_policy"]["reason"] == "custom_adapter_requires_explicit_allow_custom_engine"


def test_create_job_writes_validatable_spec(tmp_path: Path) -> None:
    job_path = tmp_path / "job_specs" / "generated_job.json"
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "create-job",
        "--project-id",
        "generated-demo",
        "--source-svg",
        "examples/original_sample.svg",
        "--households",
        "900",
        "--standards",
        "examples/area_standard_sample.csv",
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        "--opencrab-result-file",
        "examples/opencrab_mcp_result_sample.json",
        "--opencrab-source-tool",
        "opencrab_search_documents",
        "--constraint-sketch",
        "examples/constraint_sketch_sample.json",
        "--prompt",
        "Improve the greenery lounge hierarchy while preserving protected geometry.",
        "--engine-adapter",
        "layout-svg-engine",
        "--output",
        str(job_path),
        "--validate",
        "--validation-output-dir",
        str(tmp_path / "diagnostics"),
        "--strict-validation",
        "--skip-preview",
        "--brief",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout.splitlines()
    assert Path(stdout[0]) == job_path
    validation_path = Path(stdout[1])
    brief_path = Path(stdout[2])
    summary = json.loads(stdout[3])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    brief = brief_path.read_text(encoding="utf-8")
    assert summary["validation_status"] == "pass"
    assert summary["job_brief"] == str(brief_path)
    assert job["schema"] == "crab-archi-design-job-spec-v1"
    assert job["project_root"] == str(tmp_path / "projects")
    assert job["project_id"] == "generated-demo"
    assert job["households"] == 900
    assert job["standards"] == ["examples/area_standard_sample.csv"]
    assert job["opencrab_source_tool"] == "opencrab_search_documents"
    assert job["skip_preview"] is True
    assert validation["status"] == "pass"
    assert "# Crab Archi Design Job Brief: generated-demo" in brief
    assert "Validation status: `pass`" in brief
    assert "crab-archi-design run-job --job" in brief

    result = run_cli("validate-job", "--job", str(job_path), "--output-dir", str(tmp_path / "diagnostics_again"), "--strict", cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_quickstart_script_executes_full_sample_path(tmp_path: Path) -> None:
    out_dir = tmp_path / "quickstart"
    project_id = "pytest-quickstart"
    result = subprocess.run(
        ["bash", str(ROOT / "examples" / "run_quickstart.sh"), str(out_dir), project_id],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Quickstart complete" in result.stdout
    job_path = out_dir / "job_specs" / f"{project_id}_job.json"
    brief_path = job_path.with_suffix(".md")
    audit_path = out_dir / "projects" / project_id / "audits" / "release_audit_001.json"
    assert job_path.exists()
    assert brief_path.exists()
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["status"] == "pass"
    assert audit["checks"]["package_verify_pass"] is True


def test_run_job_executes_sample_pipeline(tmp_path: Path) -> None:
    job_path = tmp_path / "job.json"
    job = {
        "schema": "crab-archi-design-job-spec-v1",
        "project_root": str(tmp_path / "projects"),
        "project_id": "job-demo",
        "source_svg": str(ROOT / "examples" / "original_sample.svg"),
        "households": 900,
        "standards": [str(ROOT / "examples" / "area_standard_sample.csv")],
        "ontology_pack": "community_svg_topology_ontology_v2",
        "opencrab_result_file": [str(ROOT / "examples" / "opencrab_mcp_result_sample.json")],
        "opencrab_source_tool": "opencrab_search_documents",
        "constraint_sketch": str(ROOT / "examples" / "constraint_sketch_sample.json"),
        "prompt": "Improve the greenery lounge hierarchy while preserving protected geometry.",
        "engine_adapter": "layout-svg-engine",
        "skip_preview": True,
        "export_package": True,
        "verify_package": True,
        "doctor": True,
        "strict": True,
    }
    job_path.write_text(json.dumps(job), encoding="utf-8")

    result = run_cli("run-job", "--job", str(job_path), "--strict", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    report_path = Path(result.stdout.splitlines()[0])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "crab-archi-design-job-run-report-v1"
    assert report["status"] == "pass"
    assert report["failed_steps"] == []
    step_status = {step["name"]: step["status"] for step in report["steps"]}
    assert step_status["workflow-run"] == "pass"
    assert step_status["export-package"] == "pass"
    assert step_status["verify-package"] == "pass"
    assert step_status["doctor"] == "pass"
    assert Path(report["artifacts"]["workflow_report"]).exists()
    assert Path(report["artifacts"]["export_manifest"]).exists()
    assert Path(report["artifacts"]["export_zip"]).exists()
    assert Path(report["artifacts"]["verify_report"]).exists()
    assert Path(report["artifacts"]["doctor_report"]).exists()

    workflow = json.loads(Path(report["artifacts"]["workflow_report"]).read_text(encoding="utf-8"))
    assert workflow["status"] == "pass"
    assert Path(workflow["latest_artifacts"]["topology_manifest"]).exists()
    alternative = Path(workflow["latest_artifacts"]["alternative_svg"])
    assert "crab_archi_design_layout_engine_candidate" in alternative.read_text(encoding="utf-8")
    doctor = json.loads(Path(report["artifacts"]["doctor_report"]).read_text(encoding="utf-8"))
    assert doctor["status"] == "pass"


def test_apply_edit_runs_engine_and_collects_svg(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect x='1' y='1' width='8' height='8'/><text x='2' y='2'>fitness</text></svg>", encoding="utf-8")
    engine = tmp_path / "fake_engine.py"
    engine.write_text(
        textwrap.dedent(
            """
            import json
            import os
            from pathlib import Path

            run_dir = Path(os.environ["CRAB_ARCHI_RUN_DIR"])
            out = run_dir / "candidate.svg"
            out.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><text>alt</text></svg>", encoding="utf-8")
            report = run_dir / "engine.json"
            report.write_text(json.dumps({"source_svg": os.environ["CRAB_ARCHI_SOURCE_SVG"], "output_svg": str(out)}), encoding="utf-8")
            print(report)
            """
        ).strip(),
        encoding="utf-8",
    )

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "demo",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        "--engine-adapter",
        str(engine),
        "--allow-custom-engine",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "recognize-svg", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr

    evidence_source = tmp_path / "engine_evidence.json"
    evidence_source.write_text(json.dumps({"pack_id": "community_svg_topology_ontology_v2", "quality_status": "pass"}), encoding="utf-8")
    standards = tmp_path / "engine_standards.csv"
    standards.write_text("households,program,area\n900,greenery_lounge,80\n900,fitness,70\n", encoding="utf-8")
    constraint_sketch = tmp_path / "constraint_sketch.json"
    constraint_sketch.write_text(
        json.dumps(
            {
                "coordinate_space": "source_svg_viewbox",
                "strokes": [{"stroke_id": "c1", "mode": "community_shell", "target_hint": "community_outer_shell", "points": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "evidence-attach",
        "--project-id",
        "demo",
        "--source",
        "localcrab",
        "--pack-id",
        "community_svg_topology_ontology_v2",
        "--summary",
        "Topology pack evidence verified.",
        "--source-file",
        str(evidence_source),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "standards-attach",
        "--project-id",
        "demo",
        "--file",
        str(standards),
        "--households",
        "900",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "constraint-attach",
        "--project-id",
        "demo",
        "--sketch",
        str(constraint_sketch),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("--project-root", str(tmp_path / "projects"), "topology-build", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "prompt-edit",
        "--project-id",
        "demo",
        "--text",
        "Open the greenery lounge toward the hall and lock parking.",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "apply-edit",
        "--project-id",
        "demo",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    report_path = Path(result.stdout.splitlines()[0])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "review_required"
    assert report["checks"]["engine_report_quality_found"] is False
    assert report["checks"]["engine_report_status_pass"] is False
    assert report["checks"]["engine_quality_gates_pass"] is False
    assert report["checks"]["candidate_hard_gates_pass"] is False
    assert report["candidate_quality"]["hard_status"] == "review_required"
    assert "engine_report_quality_found" in report["candidate_quality"]["hard_failures"]
    assert report["checks"]["native_svg_no_images"] is True
    assert report["checks"]["recognition_manifest_active"] is True
    assert report["checks"]["topology_manifest_active"] is True
    assert report["checks"]["opencrab_evidence_verified"] is True
    assert report["checks"]["constraint_manifest_active"] is True
    assert report["checks"]["standards_manifest_active"] is True
    assert report["copied_artifacts"]["svg"]
    alternative = Path(report["copied_artifacts"]["svg"][0])
    assert alternative.name == "alternative_001.svg"
    assert "alt" in alternative.read_text(encoding="utf-8")

    solver_input = json.loads(Path(report["solver_input"]).read_text(encoding="utf-8"))
    assert solver_input["intents"][0]["schema"] == "crab-archi-design-natural-language-edit-intent-v1"
    assert solver_input["recognition_manifest"]["status"] == "active"
    assert solver_input["topology_manifest"]["status"] == "active"
    assert solver_input["evidence_manifest"]["status"] == "verified"
    assert solver_input["constraint_manifest"]["status"] == "active"
    assert solver_input["standards_manifest"]["status"] == "active"

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "review-panel",
        "--project-id",
        "demo",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    panel_path = Path(result.stdout.splitlines()[0])
    panel = panel_path.read_text(encoding="utf-8")
    assert "demo Review Panel" in panel
    assert "Original" in panel
    assert "Alternative" in panel
    assert "native_svg_no_images" in panel
    assert "Recognition" in panel
    assert "Topology" in panel
    assert "Evidence" in panel
    assert "Constraints" in panel
    assert "Standards" in panel
    assert "community_svg_topology_ontology_v2" in panel
    assert "greenery_lounge" in panel

    result = run_cli("--project-root", str(tmp_path / "projects"), "project-status", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    status_path = Path(result.stdout.splitlines()[0])
    status_json = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_path.name == "project_status.json"
    assert status_json["overall_status"] == "candidate_review_required"
    assert status_json["gates"]["latest_apply_pass"] is False
    assert status_json["gates"]["latest_alternative_exists"] is True
    assert status_json["gates"]["latest_alternative_native_svg"] is True
    assert status_json["latest_artifacts"]["alternative_svg"].endswith("alternative_001.svg")
    assert status_json["latest_artifacts"]["review_panel"].endswith(".html")
    assert status_json["metrics"]["latest_apply_status"] == "review_required"


def test_apply_edit_rejects_untrusted_custom_engine_by_default(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><text x='2' y='2'>fitness</text></svg>", encoding="utf-8")
    engine = tmp_path / "fake_engine.py"
    engine.write_text("print('should not run')", encoding="utf-8")

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "init",
        "--project-id",
        "blocked-engine",
        "--source-svg",
        str(source_svg),
        "--ontology-pack",
        "community_svg_topology_ontology_v2",
        "--engine-adapter",
        str(engine),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "prompt-edit",
        "--project-id",
        "blocked-engine",
        "--text",
        "Test custom engine rejection.",
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "--project-root",
        str(tmp_path / "projects"),
        "apply-edit",
        "--project-id",
        "blocked-engine",
        cwd=ROOT,
    )
    assert result.returncode != 0
    assert "not allowlisted" in result.stderr


def test_doodle_editor_command_prints_local_editor() -> None:
    result = run_cli("doodle-editor", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert Path(lines[0]).name == "doodle_editor.html"
    assert lines[1].startswith("file://")
