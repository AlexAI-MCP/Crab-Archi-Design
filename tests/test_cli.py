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
    assert '"status": "pass"' in result.stdout


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
    assert report["copied_artifacts"]["svg"]
    alternative = Path(report["copied_artifacts"]["svg"][0])
    assert alternative.name == "alternative_001.svg"
    assert "alt" in alternative.read_text(encoding="utf-8")

    solver_input = json.loads(Path(report["solver_input"]).read_text(encoding="utf-8"))
    assert solver_input["intents"][0]["schema"] == "crab-archi-design-natural-language-edit-intent-v1"
