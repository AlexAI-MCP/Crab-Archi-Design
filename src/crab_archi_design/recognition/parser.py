from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from crab_archi_design.recognition.ir import empty_recognition_ir
from crab_archi_design.recognition.classify import classify_node
from crab_archi_design.svg.defs import collect_id_index, referenced_element, use_instance_matrix
from crab_archi_design.svg.geometry import Point, scaled_polyline_length
from crab_archi_design.svg.namespace import local_name
from crab_archi_design.svg.safe_load import SvgLoadError, safe_load_svg
from crab_archi_design.svg.shapes import build_shape_node
from crab_archi_design.svg.style import collect_css_rules, inherited_style
from crab_archi_design.svg.transform import Matrix, identity_matrix, multiply_matrix, parse_transform
from crab_archi_design.svg.units import document_unit_scale, parse_viewbox, unit_scale_mm

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
    unit_scale = document_unit_scale(root.attrib.get("width"), root.attrib.get("height"), viewbox)
    ir["status"] = "active"
    ir["document"].update(
        {
            "viewBox": {"minX": viewbox.min_x, "minY": viewbox.min_y, "w": viewbox.width, "h": viewbox.height},
            "width_raw": root.attrib.get("width"),
            "height_raw": root.attrib.get("height"),
            "unit_scale_mm": unit_scale_mm(root.attrib.get("width"), viewbox),
            **unit_scale,
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
    attach_physical_metrics(nodes, ir["document"])
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


def summarize_nodes(nodes: list[dict[str, Any]], raster_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    label_count = sum(1 for node in nodes if node.get("tag") == "text")
    source_document_indexed_count = sum(1 for node in nodes if isinstance(node.get("source_document_index"), int))
    source_document_index_missing_count = len(nodes) - source_document_indexed_count
    return {
        "primitive_count": len(nodes),
        "column_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "column"),
        "wall_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "wall"),
        "room_envelope_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "room_envelope"),
        "label_count": label_count,
        "protected_candidate_count": sum(1 for node in nodes if node.get("role_hint") == "column"),
        "raster_count": len(raster_nodes),
        "physical_metric_node_count": sum(1 for node in nodes if node.get("physical_metrics_source")),
        "source_document_indexed_count": source_document_indexed_count,
        "source_document_index_missing_count": source_document_index_missing_count,
        "editable_source_count": sum(1 for node in nodes if node.get("editable_source")),
        "use_instance_count": sum(1 for node in nodes if node.get("from_use_instance")),
        "source_document_indexes_present": bool(nodes) and source_document_index_missing_count == 0,
    }


def attach_physical_metrics(nodes: list[dict[str, Any]], document: dict[str, Any]) -> None:
    scale_x = coerce_float(document.get("unit_scale_x_mm") or document.get("unit_scale_mm"))
    scale_y = coerce_float(document.get("unit_scale_y_mm") or document.get("unit_scale_mm"))
    document["physical_metrics_available"] = scale_x is not None and scale_y is not None
    if scale_x is None or scale_y is None:
        return
    for node in nodes:
        bbox = node.get("bbox")
        if isinstance(bbox, dict):
            node["bbox_mm"] = {
                "x": round(float(bbox.get("x") or 0.0) * scale_x, 3),
                "y": round(float(bbox.get("y") or 0.0) * scale_y, 3),
                "w": round(float(bbox.get("w") or 0.0) * scale_x, 3),
                "h": round(float(bbox.get("h") or 0.0) * scale_y, 3),
            }
        centroid = point_from_sequence(node.get("centroid"))
        if centroid:
            node["centroid_mm"] = [round(centroid[0] * scale_x, 3), round(centroid[1] * scale_y, 3)]
        area = coerce_float(node.get("area")) or 0.0
        area_mm2 = area * scale_x * scale_y if bool(node.get("is_closed")) and area > 0.0 else 0.0
        node["area_mm2"] = round(area_mm2, 3)
        node["area_m2"] = round(area_mm2 / 1_000_000.0, 6)
        points = points_from_polygon(node.get("polygon"))
        if points:
            node["perimeter_mm"] = round(scaled_polyline_length(points, scale_x, scale_y, bool(node.get("is_closed"))), 3)
        else:
            perimeter = coerce_float(node.get("perimeter")) or 0.0
            node["perimeter_mm"] = round(perimeter * max(scale_x, scale_y), 3)
        node["physical_metrics_source"] = "document_unit_scale"


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def point_from_sequence(value: Any) -> Point | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x = coerce_float(value[0])
    y = coerce_float(value[1])
    if x is None or y is None:
        return None
    return (x, y)


def points_from_polygon(value: Any) -> list[Point]:
    if not isinstance(value, list):
        return []
    points: list[Point] = []
    for item in value:
        point = point_from_sequence(item)
        if point:
            points.append(point)
    return points
