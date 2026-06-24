from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from crab_archi_design.recognition.ir import empty_recognition_ir
from crab_archi_design.recognition.classify import classify_node
from crab_archi_design.svg.defs import collect_id_index, referenced_element, use_instance_matrix
from crab_archi_design.svg.namespace import local_name
from crab_archi_design.svg.safe_load import SvgLoadError, safe_load_svg
from crab_archi_design.svg.shapes import build_shape_node
from crab_archi_design.svg.style import collect_css_rules, inherited_style
from crab_archi_design.svg.transform import Matrix, identity_matrix, multiply_matrix, parse_transform
from crab_archi_design.svg.units import parse_viewbox, unit_scale_mm

SHAPE_TAGS = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text"}


def build_recognition_ir_v2(path: Path) -> dict[str, Any]:
    ir = empty_recognition_ir(str(path))
    try:
        root = safe_load_svg(path)
    except SvgLoadError as exc:
        ir["status"] = "review_required"
        ir["warnings"].append({"code": "safe_load_failed", "detail": str(exc)})
        return ir

    viewbox = parse_viewbox(root.attrib.get("viewBox"), root.attrib.get("width"), root.attrib.get("height"))
    ir["status"] = "active"
    ir["document"].update(
        {
            "viewBox": {"minX": viewbox.min_x, "minY": viewbox.min_y, "w": viewbox.width, "h": viewbox.height},
            "width_raw": root.attrib.get("width"),
            "height_raw": root.attrib.get("height"),
            "unit_scale_mm": unit_scale_mm(root.attrib.get("width"), viewbox),
        }
    )
    nodes: list[dict[str, Any]] = []
    raster_nodes: list[dict[str, Any]] = []
    id_index = collect_id_index(root)
    css_rules = collect_css_rules(root)
    document_indices = {id(element): index for index, element in enumerate(root.iter(), start=1)}
    walk_svg(root, identity_matrix(), [], {}, nodes, raster_nodes, ir["warnings"], id_index, css_rules, document_indices)
    drawing_area = max(1.0, viewbox.width * viewbox.height)
    for node in nodes:
        role_hint, confidence = classify_node(node, drawing_area)
        node["role_hint"] = role_hint
        node["role_confidence"] = confidence
    ir["nodes"] = nodes
    ir["raster_nodes"] = raster_nodes
    ir["summary"] = summarize_nodes(nodes, raster_nodes)
    return ir


def walk_svg(
    element: Element,
    parent_matrix: Matrix,
    group_path: list[str],
    parent_style: dict[str, str],
    nodes: list[dict[str, Any]],
    raster_nodes: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    id_index: dict[str, Element],
    css_rules: list[dict[str, Any]],
    document_indices: dict[int, int],
    use_stack: tuple[str, ...] = (),
    use_instance_index: int | None = None,
    use_instance_id: str | None = None,
) -> None:
    tag = local_name(element.tag)
    style = inherited_style(parent_style, element, css_rules)
    matrix = multiply_matrix(parent_matrix, parse_transform(element.attrib.get("transform")))
    next_group_path = group_path
    if tag in {"g", "svg", "symbol"}:
        group_id = element.attrib.get("id")
        next_group_path = [*group_path, group_id] if group_id else group_path
    if tag == "defs":
        return
    if tag == "use":
        referenced, error = referenced_element(element, id_index)
        use_id = element.attrib.get("id") or f"use_{len(nodes) + 1:04d}"
        if error or referenced is None:
            warnings.append({"code": error or "use_reference_failed", "node": use_id, "source_id": element.attrib.get("id")})
            return
        reference_id = referenced.attrib.get("id")
        if reference_id in use_stack:
            warnings.append({"code": "use_reference_cycle", "node": use_id, "source_id": element.attrib.get("id"), "reference_id": reference_id})
            return
        use_matrix = multiply_matrix(matrix, use_instance_matrix(element, referenced))
        use_group_path = [*group_path, use_id]
        walk_svg(
            referenced,
            use_matrix,
            use_group_path,
            style,
            nodes,
            raster_nodes,
            warnings,
            id_index,
            css_rules,
            document_indices,
            (*use_stack, reference_id or ""),
            document_indices.get(id(element)),
            use_id,
        )
        return
    if tag == "image":
        raster_nodes.append({"source_id": element.attrib.get("id"), "group_path": group_path})
    elif tag in SHAPE_TAGS:
        node = build_shape_node(
            len(nodes) + 1,
            element,
            matrix,
            group_path,
            style,
            source_document_index=document_indices.get(id(element)),
            instance_document_index=use_instance_index,
            instance_source_id=use_instance_id,
        )
        if node:
            analytic = node.get("analytic")
            if isinstance(analytic, dict):
                for warning_code in analytic.pop("warnings", []):
                    warnings.append({"code": warning_code, "node": node["id"], "source_id": element.attrib.get("id")})
            node["transform_chain"] = [[round(item, 6) for item in matrix]]
            nodes.append(node)
    for child in list(element):
        walk_svg(child, matrix, next_group_path, style, nodes, raster_nodes, warnings, id_index, css_rules, document_indices, use_stack, use_instance_index, use_instance_id)


def summarize_nodes(nodes: list[dict[str, Any]], raster_nodes: list[dict[str, Any]]) -> dict[str, int]:
    label_count = sum(1 for node in nodes if node.get("tag") == "text")
    return {
        "primitive_count": len(nodes),
        "column_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "column"),
        "wall_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "wall"),
        "room_envelope_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "room_envelope"),
        "label_count": label_count,
        "protected_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "column"),
        "raster_count": len(raster_nodes),
    }
