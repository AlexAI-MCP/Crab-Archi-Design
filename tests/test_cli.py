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
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'></svg>", encoding="utf-8")
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

    result = run_cli("--project-root", str(tmp_path / "projects"), "qa", "--project-id", "demo", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert '"status": "pass"' in result.stdout
    assert '"opencrab_evidence_verified": true' in result.stdout


def test_apply_edit_runs_engine_and_collects_svg(tmp_path: Path) -> None:
    source_svg = tmp_path / "original.svg"
    source_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'></svg>", encoding="utf-8")
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

    evidence_source = tmp_path / "engine_evidence.json"
    evidence_source.write_text(json.dumps({"pack_id": "community_svg_topology_ontology_v2", "quality_status": "pass"}), encoding="utf-8")
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
    assert report["checks"]["opencrab_evidence_verified"] is True
    assert report["copied_artifacts"]["svg"]
    alternative = Path(report["copied_artifacts"]["svg"][0])
    assert alternative.name == "alternative_001.svg"
    assert "alt" in alternative.read_text(encoding="utf-8")

    solver_input = json.loads(Path(report["solver_input"]).read_text(encoding="utf-8"))
    assert solver_input["intents"][0]["schema"] == "crab-archi-design-natural-language-edit-intent-v1"
    assert solver_input["evidence_manifest"]["status"] == "verified"

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
    assert "Evidence" in panel
    assert "community_svg_topology_ontology_v2" in panel
    assert "greenery_lounge" in panel


def test_doodle_editor_command_prints_local_editor() -> None:
    result = run_cli("doodle-editor", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert Path(lines[0]).name == "doodle_editor.html"
    assert lines[1].startswith("file://")
