from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/crab_archi_design/cli.py"


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, capture_output=True, check=False)


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

    result = run_cli("--project-root", str(tmp_path / "projects"), "qa", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert '"status": "pass"' in result.stdout
    assert '"recognition_manifest_active": true' in result.stdout
    assert '"opencrab_evidence_verified": true' in result.stdout
    assert '"constraint_manifest_active": true' in result.stdout
    assert '"standards_manifest_active": true' in result.stdout

    result = run_cli("--project-root", str(tmp_path / "projects"), "edit-brief", "--project-id", "demo", "--intent", "all", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    brief_json = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    brief_md = Path(result.stdout.splitlines()[1]).read_text(encoding="utf-8")
    assert brief_json["status"] == "pass"
    assert brief_json["checks"]["recognition_manifest_active"] is True
    assert brief_json["checks"]["opencrab_evidence_verified"] is True
    assert brief_json["checks"]["constraint_manifest_active"] is True
    assert brief_json["checks"]["standards_manifest_active"] is True
    assert brief_json["checks"]["sketch_points_inside_viewbox"] is True
    assert "greenery_lounge" in brief_md
    assert "Recognition" in brief_md
    assert "Constraints" in brief_md
    assert "Standards" in brief_md

    result = run_cli("--project-root", str(tmp_path / "projects"), "project-status", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    status_json = json.loads(Path(result.stdout.splitlines()[0]).read_text(encoding="utf-8"))
    assert status_json["overall_status"] == "ready_for_apply"
    assert status_json["gates"]["recognition_manifest_active"] is True
    assert status_json["gates"]["opencrab_evidence_verified"] is True
    assert status_json["gates"]["constraint_manifest_active"] is True
    assert status_json["gates"]["standards_manifest_active"] is True
    assert status_json["gates"]["edit_intent_exists"] is True
    assert status_json["metrics"]["program_label_count"] == 1
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
    assert handoff_json["knowledge_context"]["evidence_items"][0]["pack_id"] == "community_svg_topology_ontology_v2"
    assert handoff_json["knowledge_context"]["standards_excerpt"][0]["프로그램"] == "그리너리 라운지"
    assert "OpenCrab MCP evidence" in handoff_json["prompt_blocks"]["system_prompt"]
    assert "greenery_lounge" in handoff_md
    assert "native_svg_alternative" in handoff_md


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


def test_apply_edit_runs_engine_and_collects_svg(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><text x='2' y='2'>fitness</text></svg>", encoding="utf-8")
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
    assert report["status"] == "pass"
    assert report["checks"]["native_svg_no_images"] is True
    assert report["checks"]["recognition_manifest_active"] is True
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
    assert status_json["overall_status"] == "complete_candidate_ready"
    assert status_json["gates"]["latest_apply_pass"] is True
    assert status_json["gates"]["latest_alternative_exists"] is True
    assert status_json["gates"]["latest_alternative_native_svg"] is True
    assert status_json["latest_artifacts"]["alternative_svg"].endswith("alternative_001.svg")
    assert status_json["latest_artifacts"]["review_panel"].endswith(".html")
    assert status_json["metrics"]["latest_apply_status"] == "pass"


def test_doodle_editor_command_prints_local_editor() -> None:
    result = run_cli("doodle-editor", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert Path(lines[0]).name == "doodle_editor.html"
    assert lines[1].startswith("file://")
