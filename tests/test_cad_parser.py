from __future__ import annotations

import json
import subprocess
import unicodedata
from pathlib import Path

import ezdxf

from crab_archi_design.cad_parser import CAD_IR_SCHEMA, build_opencrab_pack, convert_dwg_to_dxf, inspect_pack, parse_dxf, run_batch
from crab_archi_design.cad_reconstruction import reconstruct_from_ir_file


def make_sample_dxf(path: Path) -> None:
    doc = ezdxf.new("R2018")
    for layer in ("A-WALL", "A-ROOM", "A-TEXT", "A-COLUMN", "A-DOOR"):
        doc.layers.add(layer)
    model = doc.modelspace()
    model.add_line((0, 0), (10_000, 0), dxfattribs={"layer": "A-WALL"})
    model.add_lwpolyline(
        [(0, 0), (10_000, 0), (10_000, 8_000), (0, 8_000)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    model.add_text("Main Hall", dxfattribs={"insert": (1_000, 1_000), "height": 300, "layer": "A-TEXT"})
    model.add_circle((5_000, 4_000), 250, dxfattribs={"layer": "A-COLUMN"})
    block = doc.blocks.new("DOOR_BLOCK")
    block.add_line((0, 0), (900, 0), dxfattribs={"layer": "A-DOOR"})
    model.add_blockref("DOOR_BLOCK", (2_500, 0), dxfattribs={"layer": "A-DOOR"})
    doc.saveas(path)


def test_parse_dxf_builds_architectural_ir(tmp_path: Path) -> None:
    source = tmp_path / "plan.dxf"
    make_sample_dxf(source)

    ir = parse_dxf(source)

    assert ir["schema"] == CAD_IR_SCHEMA
    assert ir["status"] == "active"
    assert ir["document"]["entity_count"] == 5
    assert ir["document"]["space_count"] == 1
    assert ir["document"]["source_sha256"]
    assert ir["roundtrip"]["exact_curve_parameters"] is True
    assert ir["block_entities"]
    assert ir["layers"][0]["raw_attributes"]
    roles = {item["role_hint"] for item in ir["entities"]}
    assert {"wall", "space", "main_hall", "column", "door"} <= roles
    assert ir["relations"]


def test_batch_writes_opencrab_pack_and_receipt(tmp_path: Path) -> None:
    input_dir = tmp_path / "drawings"
    input_dir.mkdir()
    make_sample_dxf(input_dir / "A-101.dxf")
    output_dir = tmp_path / "batch"

    result = run_batch(input_dir, output_dir, pack_title="Test CAD Pack")

    assert result["receipt"]["statistics"]["parsed"] == 1
    pack_dir = output_dir / "opencrab_pack"
    assert (pack_dir / "manifest.json").exists()
    assert (pack_dir / "graph" / "nodes.jsonl").exists()
    assert (pack_dir / "graph" / "edges.jsonl").exists()
    assert (pack_dir / "evidence" / "index.jsonl").exists()
    assert (pack_dir / "opencrab" / "ingest_payloads.jsonl").exists()

    inspection = inspect_pack(pack_dir)
    assert inspection["statistics"]["parsed_documents"] == 1
    nodes = [json.loads(line) for line in (pack_dir / "graph" / "nodes.jsonl").read_text().splitlines()]
    edges = [json.loads(line) for line in (pack_dir / "graph" / "edges.jsonl").read_text().splitlines()]
    node_ids = {row["id"] for row in nodes}
    assert nodes
    assert all(edge["from_id"] in node_ids and edge["to_id"] in node_ids for edge in edges)
    assert all(row.get("evidence_refs") for row in nodes)
    assert all(row.get("evidence_refs") for row in edges)
    canonical_sources = list((pack_dir / "sources").glob("*.dxf"))
    assert canonical_sources

    ir_path = next((pack_dir / "documents").glob("*.json"))
    reconstructed = reconstruct_from_ir_file(ir_path, tmp_path / "reconstructed.dxf")
    assert reconstructed["roundtrip"]["status"] == "pass"
    assert reconstructed["roundtrip"]["structured_signature_match_ratio"] == 1.0


def test_dwg_without_converter_is_explicitly_reported(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "drawings"
    input_dir.mkdir()
    (input_dir / "A-101.dwg").write_bytes(b"not a real DWG")

    monkeypatch.setattr("crab_archi_design.cad_parser.find_oda_converter", lambda _explicit=None: None)
    result = run_batch(
        input_dir,
        tmp_path / "batch",
        converter=str(tmp_path / "missing-ODAFileConverter"),
    )

    assert result["receipt"]["statistics"]["discovered"] == 1
    assert result["receipt"]["statistics"]["failed"] == 1
    assert result["rows"][0]["status"] == "converter_missing"
    assert result["manifest"]["statistics"]["failed_documents"] == 1


def test_dwg_converter_normalizes_macos_unicode_filename(tmp_path: Path, monkeypatch) -> None:
    source_name = unicodedata.normalize("NFD", "L-212 인공지반배수계획도.dwg")
    source = tmp_path / source_name
    source.write_bytes(b"not a real DWG")
    destination = tmp_path / "converted" / "drawing.dxf"
    observed: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_kwargs):
        observed["command"] = command
        target = Path(command[2])
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{unicodedata.normalize('NFC', source.stem)}.dxf").write_text("DXF", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("crab_archi_design.cad_parser.subprocess.run", fake_run)
    result = convert_dwg_to_dxf(source, destination, "/fake/ODAFileConverter")

    assert result["status"] == "converted"
    assert observed["command"][-1] == unicodedata.normalize("NFC", source.name)
    assert destination.read_text(encoding="utf-8") == "DXF"
