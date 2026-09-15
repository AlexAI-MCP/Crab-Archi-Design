from __future__ import annotations

from pathlib import Path

import ezdxf

from crab_archi_design.cad_mcp_server import call_tool
from crab_archi_design.cad_parser import run_batch


def make_sample_dxf(path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.layers.add("A-WALL")
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-WALL"})
    doc.saveas(path)


def test_opencrab_payload_tool_returns_reviewable_rows(tmp_path: Path) -> None:
    input_dir = tmp_path / "drawings"
    input_dir.mkdir()
    make_sample_dxf(input_dir / "A-101.dxf")
    output_dir = tmp_path / "batch"
    run_batch(input_dir, output_dir)

    result = call_tool(
        "cad_opencrab_ingest_payload",
        {"pack_dir": str(output_dir / "opencrab_pack"), "max_rows": 1},
    )

    payload = result["structuredContent"]
    assert payload["row_count"] == 1
    assert payload["rows"][0]["title"].endswith("A-101.dxf")
    assert payload["rows"][0]["create_pack"] is True
    assert payload["rows"][0]["pack_category"] == "mcp"
    assert "content" not in payload["rows"][0]


def test_reconstruct_tool_returns_roundtrip_report(tmp_path: Path) -> None:
    input_dir = tmp_path / "drawings"
    input_dir.mkdir()
    make_sample_dxf(input_dir / "A-101.dxf")
    output_dir = tmp_path / "batch"
    run_batch(input_dir, output_dir)
    ir_path = next((output_dir / "opencrab_pack" / "documents").glob("*.json"))
    result = call_tool(
        "cad_reconstruct_dxf",
        {
            "ir_json": str(ir_path),
            "output_dxf": str(tmp_path / "reconstructed.dxf"),
        },
    )

    payload = result["structuredContent"]
    assert payload["replay"]["status"] == "pass"
    assert payload["roundtrip"]["status"] == "pass"
    assert payload["roundtrip"]["structured_signature_match_ratio"] == 1.0
