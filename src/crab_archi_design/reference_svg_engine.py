from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def qname(tag: str) -> str:
    return f"{{{SVG_NS}}}{tag}"


def local_tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def parse_viewbox(raw: Any) -> list[float]:
    if isinstance(raw, str) and raw.strip():
        try:
            values = [float(item) for item in raw.replace(",", " ").split()]
        except ValueError:
            values = []
        if len(values) == 4 and values[2] > 0 and values[3] > 0:
            return values
    return [0.0, 0.0, 1000.0, 700.0]


def count_images(root: ET.Element) -> int:
    return sum(1 for element in root.iter() if local_tag(element) == "image")


def collect_operations(solver_input: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for intent in solver_input.get("intents", []):
        for operation in intent.get("operations", []):
            operations.append(
                {
                    "source": intent.get("source", {}).get("type"),
                    "target": operation.get("target") or operation.get("target_hint") or operation.get("edit_type") or "operation",
                    "action": operation.get("action") or operation.get("edit_type") or "review",
                    "method": operation.get("method") or operation.get("snap_policy") or "reference",
                }
            )
    return operations


def short_label(value: Any, limit: int = 42) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."


def add_text(parent: ET.Element, x: float, y: float, text: str, size: float, fill: str = "#111827") -> ET.Element:
    element = ET.SubElement(
        parent,
        qname("text"),
        {
            "x": f"{x:.3f}",
            "y": f"{y:.3f}",
            "font-family": "Arial, sans-serif",
            "font-size": f"{size:.3f}",
            "fill": fill,
        },
    )
    element.text = text
    return element


def add_reference_layer(root: ET.Element, solver_input: dict[str, Any]) -> dict[str, Any]:
    viewbox = parse_viewbox(root.attrib.get("viewBox"))
    min_x, min_y, width, height = viewbox
    operations = collect_operations(solver_input)
    evidence_count = len((solver_input.get("evidence_manifest") or {}).get("evidence_items", []))
    standard_count = len((solver_input.get("standards_manifest") or {}).get("standard_items", []))
    constraint_count = len((solver_input.get("constraint_manifest") or {}).get("constraint_items", []))
    recognition = solver_input.get("recognition_manifest") or {}
    label_count = recognition.get("program_label_count", 0)

    group = ET.SubElement(
        root,
        qname("g"),
        {
            "id": "crab_archi_design_reference_engine_candidate",
            "data-engine": "reference-svg-engine",
            "data-generated-at": now(),
        },
    )

    pad = max(width, height) * 0.012
    x = min_x + pad
    y = min_y + pad
    panel_width = max(width * 0.26, 180.0)
    row_height = max(height * 0.026, 16.0)
    panel_height = row_height * (7 + min(len(operations), 8))

    ET.SubElement(
        group,
        qname("rect"),
        {
            "x": f"{x:.3f}",
            "y": f"{y:.3f}",
            "width": f"{panel_width:.3f}",
            "height": f"{panel_height:.3f}",
            "rx": "0",
            "fill": "#f8fafc",
            "fill-opacity": "0.92",
            "stroke": "#0f766e",
            "stroke-width": f"{max(width, height) * 0.0015:.3f}",
        },
    )
    add_text(group, x + pad, y + row_height, "Crab Archi Design reference SVG", row_height * 0.42, "#0f766e")
    add_text(group, x + pad, y + row_height * 2.0, f"Project: {solver_input.get('project_id')}", row_height * 0.34)
    add_text(group, x + pad, y + row_height * 2.85, f"Evidence: {evidence_count}  Standards: {standard_count}", row_height * 0.34)
    add_text(group, x + pad, y + row_height * 3.7, f"Constraints: {constraint_count}  Program labels: {label_count}", row_height * 0.34)

    colors = ["#22c55e", "#3b82f6", "#f59e0b", "#14b8a6", "#a855f7", "#ef4444", "#64748b", "#0ea5e9"]
    band_x = x + pad
    band_y = y + row_height * 4.4
    band_width = panel_width - pad * 2
    for index, operation in enumerate(operations[:8]):
        target = short_label(operation.get("target"), 30)
        action = short_label(operation.get("action"), 32)
        row_y = band_y + row_height * index
        ET.SubElement(
            group,
            qname("rect"),
            {
                "x": f"{band_x:.3f}",
                "y": f"{row_y:.3f}",
                "width": f"{band_width:.3f}",
                "height": f"{row_height * 0.72:.3f}",
                "fill": colors[index % len(colors)],
                "fill-opacity": "0.16",
                "stroke": colors[index % len(colors)],
                "stroke-width": f"{max(width, height) * 0.0007:.3f}",
            },
        )
        add_text(group, band_x + pad * 0.35, row_y + row_height * 0.5, f"{target}: {action}", row_height * 0.3)

    ET.SubElement(
        group,
        qname("rect"),
        {
            "x": f"{min_x + pad:.3f}",
            "y": f"{min_y + pad:.3f}",
            "width": f"{width - pad * 2:.3f}",
            "height": f"{height - pad * 2:.3f}",
            "fill": "none",
            "stroke": "#0f766e",
            "stroke-width": f"{max(width, height) * 0.001:.3f}",
            "stroke-dasharray": f"{pad:.3f} {pad * 0.7:.3f}",
            "data-role": "native-svg-reference-boundary",
        },
    )

    return {
        "viewBox": viewbox,
        "operation_count": len(operations),
        "evidence_count": evidence_count,
        "standard_count": standard_count,
        "constraint_count": constraint_count,
        "program_label_count": label_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-preview", action="store_true")
    args, _ = parser.parse_known_args()
    _ = args

    solver_input_path = Path(os.environ["CRAB_ARCHI_SOLVER_INPUT"])
    run_dir = Path(os.environ["CRAB_ARCHI_RUN_DIR"])
    solver_input = json.loads(solver_input_path.read_text(encoding="utf-8"))
    source_svg = Path(os.environ.get("CRAB_ARCHI_SOURCE_SVG") or solver_input["manifest"]["source_svg"]).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)

    tree = ET.parse(source_svg)
    root = tree.getroot()
    source_image_count = count_images(root)
    summary = add_reference_layer(root, solver_input)
    output_svg = run_dir / "reference_engine_candidate.svg"
    tree.write(output_svg, encoding="utf-8", xml_declaration=True)

    output_tree = ET.parse(output_svg)
    output_image_count = count_images(output_tree.getroot())
    report = {
        "schema": "crab-archi-design-reference-engine-report-v1",
        "created_at": now(),
        "status": "pass",
        "engine": "reference-svg-engine",
        "reference_only": True,
        "source_svg": str(source_svg),
        "solver_input": str(solver_input_path),
        "output_svg": str(output_svg),
        "summary": summary,
        "quality": {
            "gates": {
                "native_svg_only": output_image_count == source_image_count,
                "reference_layer_added": True,
                "source_mutation_uses_only_additive_reference_layer": True,
                "opencrab_evidence_available": summary["evidence_count"] > 0,
                "constraints_available": summary["constraint_count"] > 0,
                "standards_available": summary["standard_count"] > 0,
            }
        },
    }
    report_path = run_dir / "reference_engine_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    print(output_svg)


if __name__ == "__main__":
    main()
