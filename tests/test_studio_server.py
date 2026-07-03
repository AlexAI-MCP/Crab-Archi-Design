from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from crab_archi_design.studio_server import serve, split_strokes, synthesize_default_constraints

ROOT = Path(__file__).resolve().parents[1]


def start_server(project_root: Path):
    server = serve(project_root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}"


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: int = 600) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:  # error bodies are JSON too
        return json.loads(error.read().decode("utf-8"))


def test_split_strokes_routes_constraint_and_edit_modes() -> None:
    constraints, edits, warnings = split_strokes(
        [
            {"mode": "community_shell", "points": [[0, 0], [10, 0], [10, 10]]},
            {"mode": "open_connection", "points": [[1, 1], [2, 2]]},
            {"mode": "unknown_mode", "points": [[1, 1], [2, 2]]},
            {"mode": "mutable_zone", "points": [[5, 5]]},
        ]
    )
    assert [item["mode"] for item in constraints] == ["community_shell"]
    assert [item["mode"] for item in edits] == ["open_connection"]
    assert len(warnings) == 2


def test_synthesize_default_constraints_fills_missing_zone_groups() -> None:
    viewbox = [0.0, 0.0, 1000.0, 800.0]
    added, notes = synthesize_default_constraints([], viewbox)
    modes = [item["mode"] for item in added]
    assert modes.count("community_shell") == 1
    assert modes.count("mutable_zone") == 1
    assert modes.count("no_go_zone") == 4
    assert len(notes) == 3

    shell_only = [{"mode": "community_shell", "points": [[0, 0], [10, 0], [10, 10]]}]
    added, notes = synthesize_default_constraints(shell_only, viewbox)
    modes = [item["mode"] for item in added]
    assert "community_shell" not in modes
    assert "mutable_zone" in modes
    assert len(notes) == 2

    complete = [
        {"mode": "community_shell", "points": []},
        {"mode": "mutable_zone", "points": []},
        {"mode": "lock_boundary", "points": []},
    ]
    added, notes = synthesize_default_constraints(complete, viewbox)
    assert added == [] and notes == []


def test_studio_run_prompt_only_uses_auto_defaults(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    server, base = start_server(project_root)
    try:
        report = post_json(
            f"{base}/api/run",
            {
                "project_id": "prompt-only",
                "source_svg": str(ROOT / "examples/original_sample.svg"),
                "prompt": "Open the greenery lounge toward the main hall and keep parking/core locked.",
                "households": 900,
                "standards": [str(ROOT / "examples/area_standard_sample.csv")],
                "opencrab_result_files": [str(ROOT / "examples/opencrab_mcp_result_sample.json")],
                "viewBox": [0, 0, 1450, 1000],
                "scale": {"mm_per_world": 30, "evidence": "Studio operator confirmed drawing scale."},
                "strokes": [],
            },
        )
        assert report["status"] == "pass", report
        assert any("기본 쉘" in note for note in report["warnings"])
        assert any("가변 구역" in note for note in report["warnings"])
        assert report["constraint_sketch"] and Path(report["constraint_sketch"]).exists()
    finally:
        server.shutdown()


def test_studio_server_health_load_and_run(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    server, base = start_server(project_root)
    try:
        health = get_json(f"{base}/api/health")
        assert health["status"] == "ok"
        assert "community_shell" in health["constraint_modes"]
        assert "open_connection" in health["edit_modes"]

        loaded = post_json(f"{base}/api/load-svg", {"path": str(ROOT / "examples/original_sample.svg")})
        assert loaded["status"] == "ok"
        assert "<svg" in loaded["content"]

        missing = post_json(f"{base}/api/load-svg", {"path": str(tmp_path / "nope.svg")})
        assert missing["status"] == "error"

        report = post_json(
            f"{base}/api/run",
            {
                "project_id": "studio-test",
                "source_svg": str(ROOT / "examples/original_sample.svg"),
                "prompt": "Open the greenery lounge toward the main hall and keep parking/core locked.",
                "households": 900,
                "standards": [str(ROOT / "examples/area_standard_sample.csv")],
                "opencrab_result_files": [str(ROOT / "examples/opencrab_mcp_result_sample.json")],
                "viewBox": [0, 0, 1450, 1000],
                "scale": {"mm_per_world": 30, "evidence": "Studio operator confirmed drawing scale."},
                "strokes": [
                    {"mode": "community_shell", "points": [[80, 80], [1080, 80], [1080, 880], [80, 880], [80, 80]]},
                    {"mode": "no_go_zone", "points": [[1095, 180], [1410, 340], [1350, 760], [1090, 700]]},
                    {"mode": "mutable_zone", "points": [[100, 100], [1060, 100], [1060, 860], [100, 860], [100, 100]]},
                    {"mode": "open_connection", "points": [[370, 500], [400, 500], [430, 500], [460, 500]]},
                ],
            },
        )
        assert report["status"] == "pass", report
        assert report["command_kind"] == "workflow-run"
        assert report["constraint_sketch"] and Path(report["constraint_sketch"]).exists()
        assert report["edit_sketch"] and Path(report["edit_sketch"]).exists()
        artifacts = report["result"]["latest_artifacts"]
        alternative = artifacts["alternative_svg"]

        artifact_url = f"{base}/api/artifact?path=" + urllib.parse.quote(alternative)
        with urllib.request.urlopen(artifact_url, timeout=30) as response:
            body = response.read().decode("utf-8")
        assert "data-crab" in body

        outside = post_json(f"{base}/api/load-svg", {"path": "/etc/hosts"})
        assert outside["status"] == "error"

        status = get_json(f"{base}/api/status?project_id=studio-test")
        assert status.get("overall_status") == "complete_candidate_ready"
    finally:
        server.shutdown()
