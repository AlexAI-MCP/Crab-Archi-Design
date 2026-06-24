from __future__ import annotations

from copy import deepcopy
from typing import Any
from xml.etree.ElementTree import Element

from crab_archi_design.svg.path import TOKEN_RE, flatten_path_points, parse_path, path_is_closed
from crab_archi_design.svg.transform import (
    Matrix,
    apply_inverse_linear,
    apply_inverse_matrix,
    apply_matrix,
    identity_matrix,
    inverse_matrix,
    parse_numbers,
)


def local_tag(element: Element) -> str:
    return element.tag.split("}")[-1]


def count_images(root: Element) -> int:
    return sum(1 for element in root.iter() if local_tag(element) == "image")


def original_attr_name(name: str) -> str:
    return f"data-crab-original-{name}"


def preserve_original_attr(element: Element, name: str) -> None:
    original_name = original_attr_name(name)
    if original_name not in element.attrib and name in element.attrib:
        element.set(original_name, str(element.attrib[name]))


def preserve_original_attrs(element: Element, names: list[str]) -> None:
    for name in names:
        preserve_original_attr(element, name)


def svg_float(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def format_svg_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def matrix_is_identity(matrix: Matrix | None) -> bool:
    if matrix is None:
        return True
    return all(abs(left - right) < 1e-9 for left, right in zip(matrix, identity_matrix()))


def line_points(element: Element) -> tuple[float, float, float, float] | None:
    if local_tag(element) != "line" or not {"x1", "y1", "x2", "y2"} <= set(element.attrib):
        return None
    return (
        svg_float(element.attrib["x1"]),
        svg_float(element.attrib["y1"]),
        svg_float(element.attrib["x2"]),
        svg_float(element.attrib["y2"]),
    )


def polyline_points(element: Element) -> list[tuple[float, float]]:
    if local_tag(element) != "polyline" or "points" not in element.attrib:
        return []
    values = parse_numbers(element.attrib.get("points", ""))
    return list(zip(values[0::2], values[1::2]))


def editable_path_points(element: Element) -> list[tuple[float, float]]:
    if local_tag(element) != "path" or "d" not in element.attrib:
        return []
    raw = element.attrib.get("d", "")
    commands = {token.upper() for token in TOKEN_RE.findall(raw) if len(token) == 1 and token.isalpha()}
    if not commands or commands - {"M", "L", "H", "V"}:
        return []
    parsed = parse_path(raw)
    if path_is_closed(parsed) or len(parsed.subpaths) != 1:
        return []
    points = flatten_path_points(parsed)
    return points if len(points) >= 2 else []


def path_editability_review_reason(element: Element) -> str | None:
    if local_tag(element) != "path":
        return "requires path target"
    raw = element.attrib.get("d")
    if raw is None:
        return "path is missing d attribute"
    commands = {token.upper() for token in TOKEN_RE.findall(raw) if len(token) == 1 and token.isalpha()}
    if not commands:
        return "path has no drawable commands"
    unsupported = sorted(commands - {"M", "L", "H", "V"})
    if unsupported:
        return f"unsupported path commands for CAD-like edit: {','.join(unsupported)}"
    parsed = parse_path(raw)
    if path_is_closed(parsed):
        return "closed path requires room/shell mutator review"
    if len(parsed.subpaths) != 1:
        return "multi-subpath path requires decomposition review"
    if len(flatten_path_points(parsed)) < 2:
        return "path needs at least two editable points"
    return None


def linear_endpoint_coords(element: Element) -> tuple[float, float, float, float] | None:
    line = line_points(element)
    if line is not None:
        return line
    points = polyline_points(element)
    if len(points) < 2:
        points = editable_path_points(element)
    if len(points) < 2:
        return None
    start = points[0]
    end = points[-1]
    return (start[0], start[1], end[0], end[1])


def operation_status(supported: bool, reason: str | None = None) -> dict[str, str]:
    if supported:
        return {"status": "supported", "reason": "native same-layer edit available"}
    return {"status": "review_required", "reason": reason or "unsupported SVG primitive for this edit"}


def transform_review_reason(matrix: Matrix | None) -> str | None:
    if matrix is None or matrix_is_identity(matrix):
        return None
    if inverse_matrix(matrix) is None:
        return "requires invertible accumulated transform"
    return None


def edit_capability_report(
    element: Element,
    parent: Element | None = None,
    transform_matrix: Matrix | None = None,
) -> dict[str, Any]:
    tag = local_tag(element)
    parent_reason = None if parent is not None and element in list(parent) else "requires source element as direct child of parent"
    transform_reason = transform_review_reason(transform_matrix)
    path_reason = path_editability_review_reason(element) if tag == "path" else None

    if tag == "line":
        opening_reason = None if line_points(element) is not None else "line requires x1/y1/x2/y2 attributes"
    elif tag == "polyline":
        opening_reason = None if len(polyline_points(element)) >= 2 else "polyline requires at least two points"
    elif tag == "path":
        opening_reason = path_reason
    else:
        opening_reason = "opening split requires line, polyline, or open single-subpath M/L/H/V path"
    if opening_reason is None:
        opening_reason = parent_reason
    if opening_reason is None and tag in {"polyline", "path"}:
        opening_reason = transform_reason

    if linear_endpoint_coords(element) is not None:
        endpoint_reason = None
    elif tag == "path":
        endpoint_reason = path_reason
    else:
        endpoint_reason = "endpoint move requires line/polyline or open single-subpath M/L/H/V path with at least two points"
    if endpoint_reason is None:
        endpoint_reason = transform_reason

    if tag == "line":
        partition_reason = None if line_points(element) is not None else "line requires x1/y1/x2/y2 attributes"
    elif tag == "polyline":
        partition_reason = None if len(polyline_points(element)) >= 2 else "polyline requires at least two points"
    elif tag == "path":
        partition_reason = path_reason
    else:
        partition_reason = "partition removal requires line/polyline/path source geometry"

    operations = {
        "opening_split": operation_status(opening_reason is None, opening_reason),
        "endpoint_move": operation_status(endpoint_reason is None, endpoint_reason),
        "partition_remove": operation_status(partition_reason is None, partition_reason),
    }
    supported_operations = [operation for operation, item in operations.items() if item["status"] == "supported"]
    review_required_operations = [operation for operation, item in operations.items() if item["status"] == "review_required"]
    return {
        "tag": tag,
        "id": element.attrib.get("id"),
        "supported_operations": supported_operations,
        "review_required_operations": review_required_operations,
        "operations": operations,
    }


def format_polyline_points(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{format_svg_number(x)},{format_svg_number(y)}" for x, y in points)


def format_path_points(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    start = points[0]
    segments = [f"M {format_svg_number(start[0])},{format_svg_number(start[1])}"]
    segments.extend(f"L {format_svg_number(x)},{format_svg_number(y)}" for x, y in points[1:])
    return " ".join(segments)


def point_distance(start: tuple[float, float], end: tuple[float, float]) -> float:
    return ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5


def polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(point_distance(points[index], points[index + 1]) for index in range(len(points) - 1))


def interpolate_point(start: tuple[float, float], end: tuple[float, float], ratio: float) -> tuple[float, float]:
    return (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)


def point_at_polyline_distance(points: list[tuple[float, float]], target_distance: float) -> tuple[tuple[float, float], int] | None:
    if len(points) < 2:
        return None
    if target_distance <= 0.0:
        return points[0], 0
    traversed = 0.0
    last_segment_index = len(points) - 2
    for index in range(len(points) - 1):
        segment_length = point_distance(points[index], points[index + 1])
        if segment_length <= 1e-9:
            continue
        if traversed + segment_length >= target_distance - 1e-9:
            ratio = min(1.0, max(0.0, (target_distance - traversed) / segment_length))
            return interpolate_point(points[index], points[index + 1], ratio), index
        traversed += segment_length
    return points[-1], last_segment_index


def compact_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    compacted: list[tuple[float, float]] = []
    for point in points:
        if not compacted or point_distance(compacted[-1], point) > 1e-9:
            compacted.append(point)
    return compacted


def split_polyline_points_for_opening(
    points: list[tuple[float, float]],
    opening_start_ratio: float,
    opening_end_ratio: float,
) -> dict[str, Any] | None:
    if len(points) < 2 or not (0.0 < opening_start_ratio < opening_end_ratio < 1.0):
        return None
    length = polyline_length(points)
    if length <= 1e-9:
        return None
    start = point_at_polyline_distance(points, length * opening_start_ratio)
    end = point_at_polyline_distance(points, length * opening_end_ratio)
    if start is None or end is None:
        return None
    start_point, start_index = start
    end_point, end_index = end
    before_points = compact_points([*points[: start_index + 1], start_point])
    after_points = compact_points([end_point, *points[end_index + 1 :]])
    if len(before_points) < 2 or len(after_points) < 2:
        return None
    return {
        "before_points": before_points,
        "after_points": after_points,
        "opening": {
            "x1": start_point[0],
            "y1": start_point[1],
            "x2": end_point[0],
            "y2": end_point[1],
        },
    }


def inverse_points(matrix: Matrix, points: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    inverted: list[tuple[float, float]] = []
    for point in points:
        local_point = apply_inverse_matrix(matrix, point)
        if local_point is None:
            return None
        inverted.append(local_point)
    return inverted


def opening_dict_from_points(start: tuple[float, float], end: tuple[float, float]) -> dict[str, float]:
    return {
        "x1": start[0],
        "y1": start[1],
        "x2": end[0],
        "y2": end[1],
    }


def interpolate_line_point(coords: tuple[float, float, float, float], ratio: float) -> tuple[float, float]:
    x1, y1, x2, y2 = coords
    return (x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio)


def collapse_line_to_zero_length(element: Element) -> bool:
    if not {"x1", "y1", "x2", "y2"} <= set(element.attrib):
        return False
    preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
    element.set("x2", element.attrib["x1"])
    element.set("y2", element.attrib["y1"])
    return True


def collapse_polyline_to_zero_length(element: Element) -> bool:
    points = polyline_points(element)
    if len(points) < 2:
        return False
    preserve_original_attr(element, "points")
    element.set("points", format_polyline_points([points[0], points[0]]))
    return True


def collapse_path_to_zero_length(element: Element) -> bool:
    points = editable_path_points(element)
    if len(points) < 2:
        return False
    preserve_original_attr(element, "d")
    element.set("d", format_path_points([points[0], points[0]]))
    return True


def collapse_linear_element_to_zero_length(element: Element) -> bool:
    tag = local_tag(element)
    if tag == "line":
        return collapse_line_to_zero_length(element)
    if tag == "polyline":
        return collapse_polyline_to_zero_length(element)
    if tag == "path":
        return collapse_path_to_zero_length(element)
    return False


def split_line_for_opening(
    parent: Element,
    element: Element,
    opening_start_ratio: float,
    opening_end_ratio: float,
    operation_id: str = "door_opening",
) -> dict[str, Any]:
    coords = line_points(element)
    children = list(parent)
    if coords is None or element not in children or not (0.0 < opening_start_ratio < opening_end_ratio < 1.0):
        return {
            "action": "split_line_for_opening",
            "status": "skipped",
            "reason": "requires direct child line and 0 < start < end < 1",
            "geometry_mutated": False,
            "same_layer_segment_added": False,
        }

    preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
    start_x, start_y = interpolate_line_point(coords, opening_start_ratio)
    end_x, end_y = interpolate_line_point(coords, opening_end_ratio)

    after_segment = deepcopy(element)
    source_id = element.attrib.get("id")
    if source_id:
        after_segment.set("id", f"{source_id}__crab_{operation_id}_after")
        after_segment.set("data-crab-derived-from", source_id)

    element.set("x2", format_svg_number(start_x))
    element.set("y2", format_svg_number(start_y))
    element.set("data-crab-action", "split_line_for_opening")
    element.set("data-crab-same-layer-mutation", "same_layer_line_opening_split")
    element.set("data-crab-opening-operation", operation_id)
    element.set("data-crab-opening-segment", "before")
    element.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    element.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    element.set("data-crab-geometry-mutated", "true")

    after_segment.set("x1", format_svg_number(end_x))
    after_segment.set("y1", format_svg_number(end_y))
    after_segment.set("x2", format_svg_number(coords[2]))
    after_segment.set("y2", format_svg_number(coords[3]))
    after_segment.set("data-crab-action", "split_line_for_opening")
    after_segment.set("data-crab-same-layer-mutation", "same_layer_line_opening_split")
    after_segment.set("data-crab-opening-operation", operation_id)
    after_segment.set("data-crab-opening-segment", "after")
    after_segment.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    after_segment.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    after_segment.set("data-crab-generated-same-layer-segment", "true")
    after_segment.set("data-crab-geometry-mutated", "true")

    parent.insert(children.index(element) + 1, after_segment)
    return {
        "action": "split_line_for_opening",
        "status": "applied",
        "operation_id": operation_id,
        "source_id": source_id,
        "before_segment": {"x1": coords[0], "y1": coords[1], "x2": start_x, "y2": start_y},
        "opening": {"x1": start_x, "y1": start_y, "x2": end_x, "y2": end_y},
        "after_segment": {"x1": end_x, "y1": end_y, "x2": coords[2], "y2": coords[3]},
        "geometry_mutated": True,
        "same_layer_segment_added": True,
    }


def split_polyline_for_opening(
    parent: Element,
    element: Element,
    opening_start_ratio: float,
    opening_end_ratio: float,
    operation_id: str = "door_opening",
    transform_matrix: Matrix | None = None,
) -> dict[str, Any]:
    local_points = polyline_points(element)
    children = list(parent)
    matrix = transform_matrix or identity_matrix()
    split_points = [apply_matrix(matrix, point) for point in local_points] if not matrix_is_identity(matrix) else local_points
    split = split_polyline_points_for_opening(split_points, opening_start_ratio, opening_end_ratio)
    if not local_points or element not in children or split is None:
        return {
            "action": "split_polyline_for_opening",
            "status": "skipped",
            "reason": "requires direct child polyline with at least two points and 0 < start < end < 1",
            "geometry_mutated": False,
            "same_layer_segment_added": False,
        }
    world_opening = split["opening"] if not matrix_is_identity(matrix) else None
    if not matrix_is_identity(matrix):
        before_points = inverse_points(matrix, split["before_points"])
        after_points = inverse_points(matrix, split["after_points"])
        opening_start = apply_inverse_matrix(matrix, (split["opening"]["x1"], split["opening"]["y1"]))
        opening_end = apply_inverse_matrix(matrix, (split["opening"]["x2"], split["opening"]["y2"]))
        if before_points is None or after_points is None or opening_start is None or opening_end is None:
            return {
                "action": "split_polyline_for_opening",
                "status": "skipped",
                "reason": "requires invertible transform for polyline opening split",
                "geometry_mutated": False,
                "same_layer_segment_added": False,
            }
        local_opening = opening_dict_from_points(opening_start, opening_end)
    else:
        before_points = split["before_points"]
        after_points = split["after_points"]
        local_opening = split["opening"]

    preserve_original_attr(element, "points")
    after_segment = deepcopy(element)
    source_id = element.attrib.get("id")
    if source_id:
        after_segment.set("id", f"{source_id}__crab_{operation_id}_after")
        after_segment.set("data-crab-derived-from", source_id)

    before_attr = format_polyline_points(before_points)
    after_attr = format_polyline_points(after_points)

    element.set("points", before_attr)
    element.set("data-crab-action", "split_polyline_for_opening")
    element.set("data-crab-same-layer-mutation", "same_layer_polyline_opening_split")
    element.set("data-crab-opening-operation", operation_id)
    element.set("data-crab-opening-segment", "before")
    element.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    element.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    element.set("data-crab-geometry-mutated", "true")
    if not matrix_is_identity(matrix):
        element.set("data-crab-transform-aware", "true")

    after_segment.set("points", after_attr)
    after_segment.set("data-crab-action", "split_polyline_for_opening")
    after_segment.set("data-crab-same-layer-mutation", "same_layer_polyline_opening_split")
    after_segment.set("data-crab-opening-operation", operation_id)
    after_segment.set("data-crab-opening-segment", "after")
    after_segment.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    after_segment.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    after_segment.set("data-crab-generated-same-layer-segment", "true")
    after_segment.set("data-crab-geometry-mutated", "true")
    if not matrix_is_identity(matrix):
        after_segment.set("data-crab-transform-aware", "true")

    parent.insert(children.index(element) + 1, after_segment)
    result = {
        "action": "split_polyline_for_opening",
        "status": "applied",
        "operation_id": operation_id,
        "source_id": source_id,
        "before_segment": {"points": before_attr},
        "opening": local_opening,
        "after_segment": {"points": after_attr},
        "transform_aware": not matrix_is_identity(matrix),
        "geometry_mutated": True,
        "same_layer_segment_added": True,
    }
    if world_opening is not None:
        result["world_opening"] = world_opening
    return result


def split_path_for_opening(
    parent: Element,
    element: Element,
    opening_start_ratio: float,
    opening_end_ratio: float,
    operation_id: str = "door_opening",
    transform_matrix: Matrix | None = None,
) -> dict[str, Any]:
    local_points = editable_path_points(element)
    children = list(parent)
    matrix = transform_matrix or identity_matrix()
    split_points = [apply_matrix(matrix, point) for point in local_points] if not matrix_is_identity(matrix) else local_points
    split = split_polyline_points_for_opening(split_points, opening_start_ratio, opening_end_ratio)
    if not local_points or element not in children or split is None:
        return {
            "action": "split_path_for_opening",
            "status": "skipped",
            "reason": "requires direct child open single-subpath M/L/H/V path and 0 < start < end < 1",
            "geometry_mutated": False,
            "same_layer_segment_added": False,
        }
    world_opening = split["opening"] if not matrix_is_identity(matrix) else None
    if not matrix_is_identity(matrix):
        before_points = inverse_points(matrix, split["before_points"])
        after_points = inverse_points(matrix, split["after_points"])
        opening_start = apply_inverse_matrix(matrix, (split["opening"]["x1"], split["opening"]["y1"]))
        opening_end = apply_inverse_matrix(matrix, (split["opening"]["x2"], split["opening"]["y2"]))
        if before_points is None or after_points is None or opening_start is None or opening_end is None:
            return {
                "action": "split_path_for_opening",
                "status": "skipped",
                "reason": "requires invertible transform for path opening split",
                "geometry_mutated": False,
                "same_layer_segment_added": False,
            }
        local_opening = opening_dict_from_points(opening_start, opening_end)
    else:
        before_points = split["before_points"]
        after_points = split["after_points"]
        local_opening = split["opening"]

    preserve_original_attr(element, "d")
    after_segment = deepcopy(element)
    source_id = element.attrib.get("id")
    if source_id:
        after_segment.set("id", f"{source_id}__crab_{operation_id}_after")
        after_segment.set("data-crab-derived-from", source_id)

    before_d = format_path_points(before_points)
    after_d = format_path_points(after_points)

    element.set("d", before_d)
    element.set("data-crab-action", "split_path_for_opening")
    element.set("data-crab-same-layer-mutation", "same_layer_path_opening_split")
    element.set("data-crab-opening-operation", operation_id)
    element.set("data-crab-opening-segment", "before")
    element.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    element.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    element.set("data-crab-geometry-mutated", "true")
    if not matrix_is_identity(matrix):
        element.set("data-crab-transform-aware", "true")

    after_segment.set("d", after_d)
    after_segment.set("data-crab-action", "split_path_for_opening")
    after_segment.set("data-crab-same-layer-mutation", "same_layer_path_opening_split")
    after_segment.set("data-crab-opening-operation", operation_id)
    after_segment.set("data-crab-opening-segment", "after")
    after_segment.set("data-crab-opening-start-ratio", format_svg_number(opening_start_ratio))
    after_segment.set("data-crab-opening-end-ratio", format_svg_number(opening_end_ratio))
    after_segment.set("data-crab-generated-same-layer-segment", "true")
    after_segment.set("data-crab-geometry-mutated", "true")
    if not matrix_is_identity(matrix):
        after_segment.set("data-crab-transform-aware", "true")

    parent.insert(children.index(element) + 1, after_segment)
    result = {
        "action": "split_path_for_opening",
        "status": "applied",
        "operation_id": operation_id,
        "source_id": source_id,
        "before_segment": {"d": before_d},
        "opening": local_opening,
        "after_segment": {"d": after_d},
        "transform_aware": not matrix_is_identity(matrix),
        "geometry_mutated": True,
        "same_layer_segment_added": True,
    }
    if world_opening is not None:
        result["world_opening"] = world_opening
    return result


def endpoint_target(coords: tuple[float, float, float, float], endpoint: str, candidate: dict[str, Any], transform_matrix: Matrix | None = None) -> tuple[float, float] | None:
    x1, y1, x2, y2 = coords
    base_x, base_y = (x1, y1) if endpoint == "start" else (x2, y2)
    matrix = transform_matrix or identity_matrix()
    has_absolute = candidate.get("x") is not None or candidate.get("y") is not None
    if has_absolute:
        if matrix_is_identity(matrix):
            return (
                svg_float(candidate["x"]) if candidate.get("x") is not None else base_x,
                svg_float(candidate["y"]) if candidate.get("y") is not None else base_y,
            )
        base_world_x, base_world_y = apply_matrix(matrix, (base_x, base_y))
        target_world = (
            svg_float(candidate["x"]) if candidate.get("x") is not None else base_world_x,
            svg_float(candidate["y"]) if candidate.get("y") is not None else base_world_y,
        )
        return apply_inverse_matrix(matrix, target_world)
    if candidate.get("dx") is None and candidate.get("dy") is None:
        return None
    delta = (svg_float(candidate.get("dx", 0.0)), svg_float(candidate.get("dy", 0.0)))
    if not matrix_is_identity(matrix):
        local_delta = apply_inverse_linear(matrix, delta)
        if local_delta is None:
            return None
        delta = local_delta
    return (base_x + delta[0], base_y + delta[1])


def move_line_endpoint(
    element: Element,
    endpoint: str,
    candidate: dict[str, Any],
    operation_id: str = "endpoint_move",
    transform_matrix: Matrix | None = None,
) -> dict[str, Any]:
    tag = local_tag(element)
    coords = linear_endpoint_coords(element)
    if coords is None or endpoint not in {"start", "end"}:
        reason = "requires open single-subpath M/L/H/V path with at least two points" if tag == "path" else "requires line/polyline/path and endpoint=start|end"
        return {
            "action": "move_line_endpoint",
            "status": "skipped",
            "reason": reason,
            "geometry_mutated": False,
            "same_layer_endpoint_moved": False,
        }
    matrix = transform_matrix or identity_matrix()
    target = endpoint_target(coords, endpoint, candidate, transform_matrix=matrix)
    if target is None:
        return {
            "action": "move_line_endpoint",
            "status": "skipped",
            "reason": "requires invertible transform and absolute x/y or relative dx/dy target",
            "geometry_mutated": False,
            "same_layer_endpoint_moved": False,
        }

    target_x, target_y = target
    source_x, source_y = (coords[0], coords[1]) if endpoint == "start" else (coords[2], coords[3])
    source_world_x, source_world_y = apply_matrix(matrix, (source_x, source_y))
    target_world_x, target_world_y = apply_matrix(matrix, (target_x, target_y))
    if format_svg_number(source_x) == format_svg_number(target_x) and format_svg_number(source_y) == format_svg_number(target_y):
        return {
            "action": "move_line_endpoint",
            "status": "skipped",
            "reason": "endpoint target equals current coordinate",
            "geometry_mutated": False,
            "same_layer_endpoint_moved": False,
        }

    if tag == "line":
        preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
        if endpoint == "start":
            element.set("x1", format_svg_number(target_x))
            element.set("y1", format_svg_number(target_y))
        else:
            element.set("x2", format_svg_number(target_x))
            element.set("y2", format_svg_number(target_y))
    elif tag == "polyline":
        points = polyline_points(element)
        if len(points) < 2:
            return {
                "action": "move_line_endpoint",
                "status": "skipped",
                "reason": "requires polyline with at least two points",
                "geometry_mutated": False,
                "same_layer_endpoint_moved": False,
            }
        preserve_original_attr(element, "points")
        if endpoint == "start":
            points[0] = (target_x, target_y)
        else:
            points[-1] = (target_x, target_y)
        element.set("points", format_polyline_points(points))
    elif tag == "path":
        points = editable_path_points(element)
        if len(points) < 2:
            return {
                "action": "move_line_endpoint",
                "status": "skipped",
                "reason": "requires open single-subpath M/L/H/V path with at least two points",
                "geometry_mutated": False,
                "same_layer_endpoint_moved": False,
            }
        preserve_original_attr(element, "d")
        if endpoint == "start":
            points[0] = (target_x, target_y)
        else:
            points[-1] = (target_x, target_y)
        element.set("d", format_path_points(points))
    else:
        return {
            "action": "move_line_endpoint",
            "status": "skipped",
            "reason": "requires line/polyline/path target",
            "geometry_mutated": False,
            "same_layer_endpoint_moved": False,
        }

    element.set("data-crab-action", "move_line_endpoint")
    element.set("data-crab-same-layer-mutation", f"same_layer_{tag}_endpoint_move")
    element.set("data-crab-endpoint-move-operation", operation_id)
    element.set("data-crab-endpoint", endpoint)
    element.set("data-crab-geometry-mutated", "true")
    if not matrix_is_identity(matrix):
        element.set("data-crab-transform-aware", "true")
    return {
        "action": "move_line_endpoint",
        "status": "applied",
        "operation_id": operation_id,
        "tag": tag,
        "endpoint": endpoint,
        "before": {"x": source_x, "y": source_y},
        "after": {"x": target_x, "y": target_y},
        "world_before": {"x": source_world_x, "y": source_world_y},
        "world_after": {"x": target_world_x, "y": target_world_y},
        "transform_aware": not matrix_is_identity(matrix),
        "geometry_mutated": True,
        "same_layer_endpoint_moved": True,
    }
