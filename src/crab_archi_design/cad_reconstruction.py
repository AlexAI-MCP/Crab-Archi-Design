"""Structured DXF replay and round-trip fidelity checks for CrabCADParser.

The parser keeps a canonical DXF copy for exact fallback, but this module is
the important second path: it recreates a new DXF from the structured CAD IR.
That makes the IR useful for design edits instead of treating the source file
as an opaque attachment.
"""

from __future__ import annotations

import json
import math
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from crab_archi_design.cad_parser import parse_dxf, write_json


def _vec(value: Any) -> Any:
    if isinstance(value, dict) and "x" in value and "y" in value:
        result = [value["x"], value["y"]]
        if "z" in value:
            result.append(value["z"])
        return tuple(result)
    if isinstance(value, list):
        return tuple(value)
    return value


def _attrs(record: dict[str, Any], *, allowed: set[str] | None = None) -> dict[str, Any]:
    raw = dict(record.get("raw_attributes") or {})
    if allowed is not None:
        raw = {key: value for key, value in raw.items() if key in allowed}
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"handle", "owner", "reactors", "subclass", "name", "text", "insert", "location", "start", "end", "center", "radius", "major_axis", "ratio", "start_angle", "end_angle", "start_param", "end_param", "vertices", "flags", "degree", "fit_points", "control_points", "knots", "weights", "defpoint", "defpoint2", "defpoint3", "defpoint4", "text_midpoint"}:
            continue
        result[key] = _vec(value)
    return result


COMMON_ATTRS = {
    "layer",
    "color",
    "true_color",
    "linetype",
    "lineweight",
    "ltscale",
    "transparency",
    "plotstyle",
    "material_handle",
    "invisible",
    "paperspace",
    "thickness",
    "extrusion",
    "rotation",
    "xscale",
    "yscale",
    "zscale",
}


def _safe_attrs(record: dict[str, Any], extra: set[str] | None = None) -> dict[str, Any]:
    allowed = COMMON_ATTRS | (extra or set())
    result = _attrs(record, allowed=allowed)
    # Some source DXFs contain a zero/None lineweight which ezdxf rejects on
    # creation.  Omitting the optional style value lets the layer supply it.
    if result.get("lineweight") is None:
        result.pop("lineweight", None)
    return result


def _add_entity(target: Any, record: dict[str, Any]) -> tuple[bool, str | None]:
    """Replay one supported entity; return (success, warning)."""

    kind = str(record.get("type", ""))
    geometry = record.get("geometry") or {}
    attrs = _safe_attrs(record)
    try:
        if kind == "LINE":
            target.add_line(_vec(geometry.get("start")), _vec(geometry.get("end")), dxfattribs=attrs)
            return True, None
        if kind == "LWPOLYLINE":
            vertices = geometry.get("vertices") or []
            points = [
                (
                    item.get("x", 0.0),
                    item.get("y", 0.0),
                    item.get("start_width", 0.0),
                    item.get("end_width", 0.0),
                    item.get("bulge", 0.0),
                )
                for item in vertices
            ]
            target.add_lwpolyline(points, format="xyseb", close=bool(record.get("closed")), dxfattribs=attrs)
            return True, None
        if kind == "POLYLINE":
            vertices = geometry.get("vertices") or []
            points = [
                (
                    *(item.get("location") or [0.0, 0.0, 0.0]),
                    item.get("start_width", 0.0),
                    item.get("end_width", 0.0),
                    item.get("bulge", 0.0),
                )
                for item in vertices
            ]
            is_3d = any(len(item.get("location") or []) > 2 and abs(float((item.get("location") or [0, 0, 0])[2])) > 1e-9 for item in vertices)
            if is_3d:
                target.add_polyline3d([item[:3] for item in points], close=bool(record.get("closed")), dxfattribs=attrs)
            else:
                target.add_polyline2d(
                    [(item[0], item[1], item[3], item[4], item[5]) for item in points],
                    format="xyseb",
                    close=bool(record.get("closed")),
                    dxfattribs=attrs,
                )
            return True, None
        if kind == "ARC":
            target.add_arc(
                _vec(geometry.get("center")),
                float(geometry.get("radius", 0.0)),
                float(geometry.get("start_angle", 0.0)),
                float(geometry.get("end_angle", 360.0)),
                dxfattribs=attrs,
            )
            return True, None
        if kind == "CIRCLE":
            target.add_circle(_vec(geometry.get("center")), float(geometry.get("radius", 0.0)), dxfattribs=attrs)
            return True, None
        if kind == "ELLIPSE":
            target.add_ellipse(
                _vec(geometry.get("center")),
                _vec(geometry.get("major_axis")),
                ratio=float(geometry.get("ratio", 1.0)),
                start_param=float(geometry.get("start_param", 0.0)),
                end_param=float(geometry.get("end_param", math.tau)),
                dxfattribs=attrs,
            )
            return True, None
        if kind == "SPLINE":
            fit_points = [_vec(item) for item in geometry.get("fit_points") or []]
            control_points = [_vec(item) for item in geometry.get("control_points") or []]
            spline = target.add_spline(fit_points=fit_points or None, degree=int(geometry.get("degree", 3) or 3), dxfattribs=attrs)
            if control_points:
                spline.control_points = control_points
            if geometry.get("knots"):
                spline.knots = [float(item) for item in geometry["knots"]]
            if geometry.get("weights"):
                spline.weights = [float(item) for item in geometry["weights"]]
            return True, None
        if kind == "INSERT":
            block_name = str(geometry.get("block_name") or "")
            target.add_blockref(block_name, _vec(geometry.get("insert")), dxfattribs=attrs)
            return True, None
        if kind == "TEXT":
            text_attrs = _safe_attrs(record, {"style", "height", "rotation", "width", "oblique", "text_generation_flag", "align_point", "halign", "valign"})
            text_attrs["insert"] = _vec(geometry.get("insert"))
            text_attrs["height"] = float(geometry.get("height", 0.0) or 0.0)
            if geometry.get("rotation") is not None:
                text_attrs["rotation"] = float(geometry.get("rotation", 0.0))
            target.add_text(str(record.get("text") or ""), dxfattribs=text_attrs)
            return True, None
        if kind == "MTEXT":
            text_attrs = _safe_attrs(record, {"style", "char_height", "rotation", "width", "attachment_point", "extrusion", "insert"})
            text_attrs["insert"] = _vec(geometry.get("insert"))
            text_attrs["char_height"] = float(geometry.get("height", 0.0) or 0.0)
            target.add_mtext(str(record.get("text") or ""), dxfattribs=text_attrs)
            return True, None
        if kind == "POINT":
            target.add_point(_vec(geometry.get("location")), dxfattribs=attrs)
            return True, None
        if kind == "RAY":
            target.add_ray(_vec(geometry.get("start")), _vec(geometry.get("unitvector")), dxfattribs=attrs)
            return True, None
        if kind == "XLINE":
            target.add_xline(_vec(geometry.get("start")), _vec(geometry.get("unitvector")), dxfattribs=attrs)
            return True, None
        if kind in {"3DFACE", "SOLID", "TRACE"}:
            target.add_3dface([_vec(item) for item in geometry.get("points") or []], dxfattribs=attrs)
            return True, None
        if kind == "HATCH":
            hatch = target.add_hatch(dxfattribs=attrs)
            hatch.dxf.solid_fill = int(bool(geometry.get("solid_fill")))
            if geometry.get("pattern_name"):
                hatch.dxf.pattern_name = str(geometry["pattern_name"])
            if geometry.get("pattern_scale") is not None:
                hatch.dxf.pattern_scale = float(geometry["pattern_scale"])
            if geometry.get("pattern_angle") is not None:
                hatch.dxf.pattern_angle = float(geometry["pattern_angle"])
            return True, "HATCH boundary paths were not retained; canonical DXF is required for exact hatch replay."
        if kind == "ATTRIB":
            text_attrs = _safe_attrs(record, {"style", "height", "rotation", "width", "insert"})
            text_attrs["insert"] = _vec(geometry.get("insert"))
            text_attrs["height"] = float(geometry.get("height", 0.0) or 0.0)
            target.add_text(str(record.get("text") or ""), dxfattribs=text_attrs)
            return True, "ATTRIB was replayed as TEXT outside its owning INSERT; canonical DXF preserves the attribute relationship."
        return False, f"Unsupported structured replay entity type: {kind}"
    except Exception as exc:
        return False, f"{kind} replay failed: {exc}"


def _restore_tables(doc: Any, ir: dict[str, Any], warnings: list[str]) -> None:
    layers = ir.get("layers") or []
    for layer in layers:
        name = str(layer.get("name") or "0")
        if name == "0" or name in doc.layers:
            continue
        try:
            raw = layer.get("raw_attributes") or {}
            color = int(raw.get("color", layer.get("color", 256)) or 256)
            doc.layers.add(
                name,
                color=abs(color),
                true_color=raw.get("true_color"),
                linetype=str(raw.get("linetype", layer.get("linetype", "Continuous"))),
                lineweight=int(raw.get("lineweight", -1) or -1),
                plot=bool(raw.get("plot", 1)),
                transparency=raw.get("transparency"),
            )
        except Exception as exc:
            warnings.append(f"layer {name}: {exc}")

    for style in (ir.get("tables") or {}).get("text_styles", []):
        name = str(style.get("name") or "")
        if not name or name in doc.styles:
            continue
        try:
            doc.styles.add(name, font=str(style.get("font", "txt.shx")))
        except Exception as exc:
            warnings.append(f"text style {name}: {exc}")

    # Linetype pattern tags are not represented by dxfattribs alone.  Keep the
    # name and description, and use a neutral dashed pattern when a custom
    # pattern cannot be recreated from the compact IR snapshot.
    for ltype in (ir.get("tables") or {}).get("linetypes", []):
        name = str(ltype.get("name") or "")
        if not name or name in doc.linetypes:
            continue
        try:
            doc.linetypes.add(name, [1.0, -1.0], description=str(ltype.get("description", "")))
        except Exception as exc:
            warnings.append(f"linetype {name}: {exc}")


def _layout_for(doc: Any, name: str) -> Any:
    if name.lower() in {"model", "modelspace"}:
        return doc.modelspace()
    layout = doc.layouts.get(name)
    if layout is None:
        layout = doc.layouts.new(name)
    return layout


def _restore_header(doc: Any, ir: dict[str, Any], warnings: list[str]) -> None:
    for key, value in (ir.get("header") or {}).items():
        try:
            if isinstance(value, dict) and "x" in value and "y" in value:
                value = _vec(value)
            doc.header[key] = value
        except Exception as exc:
            warnings.append(f"header {key}: {exc}")


def reconstruct_dxf(ir: dict[str, Any], output_path: Path, *, mode: str = "structured") -> dict[str, Any]:
    """Recreate a DXF from IR or copy its canonical source for exact replay."""

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "source":
        source = Path(str((ir.get("roundtrip") or {}).get("source_dxf", ""))).expanduser()
        if not source.is_file():
            raise ValueError(f"Canonical source DXF is not available: {source}")
        shutil.copyfile(source, output_path)
        return {"status": "source_copy", "output_path": str(output_path), "warnings": []}
    if mode != "structured":
        raise ValueError("mode must be 'structured' or 'source'")

    import ezdxf

    doc = ezdxf.new("R2018")
    warnings: list[str] = []
    _restore_header(doc, ir, warnings)
    _restore_tables(doc, ir, warnings)

    # Blocks must exist before INSERTs are replayed.
    block_layouts: dict[str, Any] = {}
    for block in ir.get("blocks") or []:
        name = str(block.get("name") or "")
        if not name or name.startswith("*"):
            continue
        try:
            if name in doc.blocks.block_names():
                block_layouts[name] = doc.blocks.get(name)
            else:
                block_layouts[name] = doc.blocks.new(name, base_point=_vec(block.get("base_point") or [0, 0, 0]))
        except Exception as exc:
            warnings.append(f"block {name}: {exc}")

    replayed = 0
    unsupported: list[str] = []
    replay_block_entities = {id(item): item for item in (ir.get("block_entities") or [])}
    for block in ir.get("blocks") or []:
        name = str(block.get("name") or "")
        target = block_layouts.get(name)
        if target is None:
            continue
        for record in block.get("entities") or []:
            success, warning = _add_entity(target, record)
            if success:
                replayed += 1
            if warning:
                warnings.append(f"{name}: {warning}")
            if not success:
                unsupported.append(str(record.get("type")))

    for record in ir.get("entities") or []:
        target = _layout_for(doc, str(record.get("layout") or "Model"))
        success, warning = _add_entity(target, record)
        if success:
            replayed += 1
        if warning:
            warnings.append(warning)
        if not success:
            unsupported.append(str(record.get("type")))

    doc.saveas(output_path)
    return {
        "status": "pass" if not unsupported else "review_required",
        "mode": "structured",
        "output_path": str(output_path),
        "replayed_entities": replayed,
        "source_entities": len(ir.get("entities") or []) + len(replay_block_entities),
        "unsupported_types": sorted(set(unsupported)),
        "warnings": warnings,
    }


def _entity_signature(record: dict[str, Any]) -> tuple[Any, ...]:
    geometry = record.get("geometry") or {}
    exact = {
        key: value
        for key, value in geometry.items()
        if key not in {"sampled_points", "points"}
    }
    return (
        record.get("scope"),
        record.get("block_name"),
        record.get("layout"),
        record.get("ordinal"),
        record.get("type"),
        record.get("layer"),
        record.get("text"),
        json.dumps(exact, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def roundtrip_report(source_ir: dict[str, Any], reconstructed_ir: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    source_entities = list(source_ir.get("entities") or []) + list(source_ir.get("block_entities") or [])
    rebuilt_entities = list(reconstructed_ir.get("entities") or []) + list(reconstructed_ir.get("block_entities") or [])
    source_types = Counter(str(item.get("type")) for item in source_entities)
    rebuilt_types = Counter(str(item.get("type")) for item in rebuilt_entities)
    source_signatures = Counter(_entity_signature(item) for item in source_entities)
    rebuilt_signatures = Counter(_entity_signature(item) for item in rebuilt_entities)
    exact_matches = sum((source_signatures & rebuilt_signatures).values())
    source_bbox = source_ir.get("document", {}).get("bbox") or {}
    rebuilt_bbox = reconstructed_ir.get("document", {}).get("bbox") or {}
    bbox_delta = {
        key: abs(float(source_bbox.get(key, 0.0)) - float(rebuilt_bbox.get(key, 0.0)))
        for key in {"min_x", "min_y", "min_z", "max_x", "max_y", "max_z", "width", "height", "depth"}
        if key in source_bbox and key in rebuilt_bbox
    }
    source_layers = {str(item.get("name")) for item in source_ir.get("layers") or []}
    rebuilt_layers = {str(item.get("name")) for item in reconstructed_ir.get("layers") or []}
    source_blocks = {str(item.get("name")) for item in source_ir.get("blocks") or []}
    rebuilt_blocks = {str(item.get("name")) for item in reconstructed_ir.get("blocks") or []}
    denominator = max(1, len(source_entities))
    status = "pass" if not replay.get("unsupported_types") and exact_matches / denominator >= 0.98 else "review_required"
    return {
        "schema": "crab-cad-parser-roundtrip-report-v1",
        "status": status,
        "source_schema": source_ir.get("schema"),
        "reconstructed_schema": reconstructed_ir.get("schema"),
        "source_entity_count": len(source_entities),
        "reconstructed_entity_count": len(rebuilt_entities),
        "exact_structured_signature_matches": exact_matches,
        "structured_signature_match_ratio": round(exact_matches / denominator, 6),
        "source_type_counts": dict(sorted(source_types.items())),
        "reconstructed_type_counts": dict(sorted(rebuilt_types.items())),
        "type_counts_match": source_types == rebuilt_types,
        "layers_match": source_layers == rebuilt_layers,
        "blocks_match": source_blocks == rebuilt_blocks,
        "text_count_match": sum(1 for item in source_entities if item.get("text")) == sum(1 for item in rebuilt_entities if item.get("text")),
        "bbox_delta": bbox_delta,
        "replay": replay,
    }


def reconstruct_from_ir_file(ir_path: Path, output_path: Path, *, mode: str = "structured", report_path: Path | None = None) -> dict[str, Any]:
    ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    if mode == "source":
        canonical_relative = (ir.get("roundtrip") or {}).get("canonical_dxf_file")
        if canonical_relative:
            canonical_path = Path(ir_path).resolve().parent.parent / str(canonical_relative)
            if canonical_path.is_file():
                ir.setdefault("roundtrip", {})["source_dxf"] = str(canonical_path)
    replay = reconstruct_dxf(ir, output_path, mode=mode)
    result: dict[str, Any] = {"replay": replay}
    if mode == "structured" and replay.get("status") != "source_copy":
        rebuilt = parse_dxf(Path(output_path), source_path=Path(output_path), source_format="dxf")
        result["roundtrip"] = roundtrip_report(ir, rebuilt, replay)
    if report_path:
        write_json(Path(report_path), result)
    return result
