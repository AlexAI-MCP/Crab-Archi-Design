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
    multiply_matrix,
    parse_numbers,
    parse_transform,
)


def local_tag(element: Element) -> str:
    return element.tag.split("}")[-1]


def count_images(root: Element) -> int:
    return sum(1 for element in root.iter() if local_tag(element) == "image")


def existing_elements_by_document_index(root: Element) -> dict[int, Element]:
    return {index: element for index, element in enumerate(root.iter(), start=1)}


def element_matrices_by_document_index(root: Element) -> dict[int, Matrix]:
    matrices: dict[int, Matrix] = {}
    index = 1

    def walk(element: Element, parent_matrix: Matrix) -> None:
        nonlocal index
        current_index = index
        index += 1
        matrix = multiply_matrix(parent_matrix, parse_transform(element.attrib.get("transform")))
        matrices[current_index] = matrix
        for child in list(element):
            walk(child, matrix)

    walk(root, identity_matrix())
    return matrices


def matrix_is_identity(matrix: Matrix | None) -> bool:
    if matrix is None:
        return True
    return all(abs(left - right) < 1e-9 for left, right in zip(matrix, identity_matrix()))


def parents_by_document_index(root: Element) -> dict[int, Element | None]:
    parents: dict[int, Element | None] = {}
    index = 1

    def walk(element: Element, parent: Element | None) -> None:
        nonlocal index
        current_index = index
        index += 1
        parents[current_index] = parent
        for child in list(element):
            walk(child, element)

    walk(root, None)
    return parents


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


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, int]:
    bbox = candidate.get("bbox") or {}
    area = float(bbox.get("width") or 0.0) * float(bbox.get("height") or 0.0)
    return (float(candidate.get("patch_priority") or 0.0), area, int(candidate.get("element_index") or 0))


def locked_candidate_indices(plan: dict[str, Any]) -> set[int]:
    indices: set[int] = set()
    for candidate in plan.get("locked_candidates", []):
        element_index = selected_candidate_index(candidate)
        if element_index is not None:
            indices.add(element_index)
    return indices


def locked_target_skip(candidate: dict[str, Any], element_index: int | None, operation: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "element_index": element_index,
        "target_element_index": element_index,
        "tag": candidate.get("tag"),
        "role_hint": candidate.get("role_hint"),
        "program_cluster_id": candidate.get("program_cluster_id"),
        "program_role": candidate.get("program_role"),
        "reason": "target element is locked/protected by patch plan",
    }


def select_candidates(plan: dict[str, Any], max_mutations: int, locked_indices: set[int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    locked_indices = locked_indices or set()
    candidates = [
        candidate
        for candidate in plan.get("same_layer_mutable_candidates", [])
        if candidate.get("mutation_policy") == "modify_or_remove_existing_element_only"
    ]
    candidates = sorted(candidates, key=candidate_sort_key, reverse=True)

    wall_candidates = [candidate for candidate in candidates if candidate.get("role_hint") == "wall_candidate"]
    clustered = [candidate for candidate in wall_candidates if candidate.get("program_cluster_id")]
    selected_pool = clustered or wall_candidates or candidates
    selected: list[dict[str, Any]] = []
    locked_skips: list[dict[str, Any]] = []
    for candidate in selected_pool:
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            locked_skips.append(locked_target_skip(candidate, element_index, "remove_or_patch_existing_element"))
            continue
        selected.append(candidate)
        if len(selected) >= max_mutations:
            break
    return selected, locked_skips


def selected_candidate_index(candidate: dict[str, Any]) -> int | None:
    raw_index = candidate.get("element_index", candidate.get("target_element_index"))
    try:
        index = int(raw_index or 0)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def element_state(element: Element) -> dict[str, Any]:
    return {
        "tag": element.tag,
        "attrib": dict(sorted(element.attrib.items())),
        "text": element.text,
        "tail": element.tail,
        "children": [element_state(child) for child in list(element)],
    }


def capture_locked_element_states(root: Element, plan: dict[str, Any]) -> list[dict[str, Any]]:
    element_map = existing_elements_by_document_index(root)
    captured: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in plan.get("locked_candidates", []):
        element_index = selected_candidate_index(candidate)
        if element_index is None or element_index in seen:
            continue
        seen.add(element_index)
        element = element_map.get(element_index)
        if element is None:
            captured.append(
                {
                    "element_index": element_index,
                    "tag": candidate.get("tag"),
                    "role_hint": candidate.get("role_hint"),
                    "reason": "locked candidate element not found",
                    "element": None,
                    "state": None,
                }
            )
            continue
        captured.append(
            {
                "element_index": element_index,
                "tag": local_tag(element),
                "role_hint": candidate.get("role_hint"),
                "reason": candidate.get("reason"),
                "element": element,
                "state": element_state(element),
            }
        )
    return captured


def locked_preservation_report(captured: list[dict[str, Any]]) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    mutated: list[dict[str, Any]] = []
    preserved_count = 0
    for item in captured:
        element = item.get("element")
        if element is None:
            missing.append(
                {
                    "element_index": item.get("element_index"),
                    "tag": item.get("tag"),
                    "role_hint": item.get("role_hint"),
                    "reason": item.get("reason"),
                }
            )
            continue
        if element_state(element) == item.get("state"):
            preserved_count += 1
            continue
        mutated.append(
            {
                "element_index": item.get("element_index"),
                "tag": item.get("tag"),
                "role_hint": item.get("role_hint"),
                "reason": item.get("reason"),
            }
        )
    return {
        "locked_candidate_count": len(captured),
        "locked_preserved_count": preserved_count,
        "locked_missing_count": len(missing),
        "locked_mutated_count": len(mutated),
        "locked_missing": missing,
        "locked_mutations": mutated,
        "locked_geometry_unchanged": len(captured) == preserved_count and not missing and not mutated,
    }


def collapse_line_to_zero_length(element: Element) -> bool:
    if not {"x1", "y1", "x2", "y2"} <= set(element.attrib):
        return False
    preserve_original_attrs(element, ["x1", "y1", "x2", "y2"])
    element.set("x2", element.attrib["x1"])
    element.set("y2", element.attrib["y1"])
    return True


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


def opening_sort_key(candidate: dict[str, Any]) -> tuple[float, int]:
    return (float(candidate.get("opening_priority", candidate.get("patch_priority") or 0.0)), int(candidate.get("target_element_index") or 0))


def endpoint_move_sort_key(candidate: dict[str, Any]) -> tuple[float, int]:
    return (float(candidate.get("endpoint_move_priority", candidate.get("patch_priority") or 0.0)), int(candidate.get("target_element_index") or 0))


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


def apply_endpoint_move_candidates(
    root: Element,
    plan: dict[str, Any],
    max_endpoint_moves: int,
    locked_indices: set[int] | None = None,
    excluded_indices: set[int] | None = None,
    element_map: dict[int, Element] | None = None,
    transform_map: dict[int, Matrix] | None = None,
) -> dict[str, Any]:
    locked_indices = locked_indices or set()
    excluded_indices = excluded_indices or set()
    element_map = element_map or existing_elements_by_document_index(root)
    transform_map = transform_map or element_matrices_by_document_index(root)
    candidates = sorted(plan.get("same_layer_endpoint_move_candidates", []), key=endpoint_move_sort_key, reverse=True)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    locked_skips: list[dict[str, Any]] = []
    moved_indices: set[int] = set()
    for candidate in candidates:
        if len(applied) >= max_endpoint_moves:
            break
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            skip = locked_target_skip(candidate, element_index, "move_line_endpoint")
            skipped.append(skip)
            locked_skips.append(skip)
            continue
        if element_index in excluded_indices:
            skipped.append({"target_element_index": element_index, "operation": "move_line_endpoint", "reason": "target element already mutated by earlier operation"})
            continue
        element = element_map.get(element_index or -1)
        if element_index is None or element is None:
            skipped.append({"target_element_index": element_index, "operation": "move_line_endpoint", "reason": "target element not found"})
            continue
        result = move_line_endpoint(
            element,
            str(candidate.get("endpoint") or "end"),
            candidate,
            operation_id=str(candidate.get("operation_id") or f"endpoint_move_{len(applied) + 1:03d}"),
            transform_matrix=transform_map.get(element_index),
        )
        result["target_element_index"] = element_index
        result["program_cluster_id"] = candidate.get("program_cluster_id")
        result["program_role"] = candidate.get("program_role")
        result["endpoint_move_priority"] = candidate.get("endpoint_move_priority")
        if result.get("status") == "applied":
            applied.append(result)
            moved_indices.add(element_index)
        else:
            skipped.append(result)
    return {
        "endpoint_move_candidate_count": len(candidates),
        "same_layer_endpoint_move_count": len(applied),
        "endpoint_moved_element_indices": sorted(moved_indices),
        "endpoint_move_mutations": applied,
        "endpoint_move_skips": skipped,
        "locked_target_skips": locked_skips,
    }


def apply_opening_candidates(
    root: Element,
    plan: dict[str, Any],
    max_openings: int,
    locked_indices: set[int] | None = None,
    element_map: dict[int, Element] | None = None,
    parent_map: dict[int, Element | None] | None = None,
    transform_map: dict[int, Matrix] | None = None,
) -> dict[str, Any]:
    locked_indices = locked_indices or set()
    element_map = element_map or existing_elements_by_document_index(root)
    parent_map = parent_map or parents_by_document_index(root)
    transform_map = transform_map or element_matrices_by_document_index(root)
    candidates = sorted(plan.get("same_layer_opening_candidates", []), key=opening_sort_key, reverse=True)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    locked_skips: list[dict[str, Any]] = []
    opened_indices: set[int] = set()
    for candidate in candidates:
        if len(applied) >= max_openings:
            break
        element_index = selected_candidate_index(candidate)
        if element_index in locked_indices:
            skip = locked_target_skip(candidate, element_index, "split_line_for_opening")
            skipped.append(skip)
            locked_skips.append(skip)
            continue
        element = element_map.get(element_index or -1)
        parent = parent_map.get(element_index or -1)
        if element_index is None or element is None or parent is None:
            skipped.append({"target_element_index": element_index, "reason": "target element or parent not found"})
            continue
        if local_tag(element) == "path":
            result = split_path_for_opening(
                parent,
                element,
                svg_float(candidate.get("opening_start_ratio", 0.42)),
                svg_float(candidate.get("opening_end_ratio", 0.58)),
                operation_id=str(candidate.get("operation_id") or f"opening_{len(applied) + 1:03d}"),
                transform_matrix=transform_map.get(element_index),
            )
        else:
            result = split_line_for_opening(
                parent,
                element,
                svg_float(candidate.get("opening_start_ratio", 0.42)),
                svg_float(candidate.get("opening_end_ratio", 0.58)),
                operation_id=str(candidate.get("operation_id") or f"opening_{len(applied) + 1:03d}"),
            )
        result["target_element_index"] = element_index
        result["program_cluster_id"] = candidate.get("program_cluster_id")
        result["program_role"] = candidate.get("program_role")
        result["connects_to_role"] = candidate.get("connects_to_role")
        result["connects_to_cluster_id"] = candidate.get("connects_to_cluster_id")
        result["adjacency_edge_id"] = candidate.get("adjacency_edge_id")
        result["adjacency_rationale"] = candidate.get("adjacency_rationale")
        result["topology_evidence"] = candidate.get("topology_evidence")
        result["opening_priority"] = candidate.get("opening_priority")
        if result.get("status") == "applied":
            applied.append(result)
            opened_indices.add(element_index)
        else:
            skipped.append(result)
    return {
        "opening_candidate_count": len(candidates),
        "same_layer_opening_split_count": len(applied),
        "same_layer_segment_added_count": sum(1 for item in applied if item.get("same_layer_segment_added")),
        "opened_element_indices": sorted(opened_indices),
        "opening_mutations": applied,
        "opening_skips": skipped,
        "locked_target_skips": locked_skips,
    }


def remove_mutable_partition_element(element: Element) -> dict[str, Any]:
    tag = local_tag(element)
    preserve_original_attrs(
        element,
        [
            "display",
            "visibility",
            "opacity",
            "stroke",
            "stroke-width",
            "stroke-opacity",
            "stroke-dasharray",
            "fill",
            "fill-opacity",
            "style",
            "points",
            "d",
        ],
    )
    geometry_mutated = collapse_line_to_zero_length(element) if tag == "line" else False
    element.set("display", "none")
    element.set("data-crab-action", "remove_internal_partition")
    element.set("data-crab-same-layer-removal", "true")
    element.set("data-crab-geometry-mutated", "true" if geometry_mutated else "visibility_removed")
    return {
        "action": element.attrib["data-crab-action"],
        "geometry_mutated": geometry_mutated,
        "same_layer_removed": True,
    }


def patch_existing_element(element: Element, candidate: dict[str, Any], mutation_index: int) -> dict[str, Any]:
    element.set("data-crab-same-layer-mutation", "same_layer_geometry_patch")
    element.set("data-crab-mutation-index", str(mutation_index))
    element.set("data-crab-source-element-index", str(candidate.get("element_index")))
    element.set("data-crab-addressing", str(candidate.get("addressing") or "source_svg_element_index"))
    element.set("data-crab-mutation-policy", str(candidate.get("mutation_policy") or "modify_or_remove_existing_element_only"))
    if candidate.get("program_cluster_id"):
        element.set("data-crab-program-cluster", str(candidate["program_cluster_id"]))
    if candidate.get("program_role"):
        element.set("data-crab-program-role", str(candidate["program_role"]))

    tag = local_tag(element)
    if tag in {"line", "polyline", "path"}:
        mutation = remove_mutable_partition_element(element)
    elif tag in {"rect", "polygon", "circle", "ellipse"}:
        preserve_original_attrs(element, ["stroke", "stroke-opacity", "fill-opacity", "style"])
        element.set("stroke", "#d04a02")
        element.set("stroke-opacity", "0.28")
        element.set("fill-opacity", "0.08")
        element.set("data-crab-action", "candidate_envelope_to_reconcile")
        element.set("data-crab-geometry-mutated", "false")
        mutation = {"action": element.attrib["data-crab-action"], "geometry_mutated": False, "same_layer_removed": False}
    else:
        element.set("data-crab-action", "candidate_attribute_patch")
        element.set("data-crab-geometry-mutated", "false")
        mutation = {"action": element.attrib["data-crab-action"], "geometry_mutated": False, "same_layer_removed": False}

    return {
        "element_index": candidate.get("element_index"),
        "tag": tag,
        "id": element.attrib.get("id"),
        "program_cluster_id": candidate.get("program_cluster_id"),
        "program_role": candidate.get("program_role"),
        "action": mutation["action"],
        "geometry_mutated": mutation["geometry_mutated"],
        "same_layer_removed": mutation["same_layer_removed"],
    }


def apply_same_layer_geometry_patch(
    root: Element,
    plan: dict[str, Any],
    max_mutations: int,
    apply_openings: bool = False,
    max_openings: int = 4,
    apply_endpoint_moves: bool = False,
    max_endpoint_moves: int = 4,
) -> dict[str, Any]:
    root.set("data-crab-candidate", "crab_archi_design_same_layer_engine_candidate")
    root.set("data-crab-engine", "same-layer-svg-engine")
    root.set("data-crab-mutation-strategy", "same_layer_geometry_patch")
    root.set("data-crab-overlay-elements-added", "0")

    element_map = existing_elements_by_document_index(root)
    parent_map = parents_by_document_index(root)
    transform_map = element_matrices_by_document_index(root)
    locked_indices = locked_candidate_indices(plan)
    locked_before = capture_locked_element_states(root, plan)
    opening_summary = apply_opening_candidates(
        root,
        plan,
        max_openings,
        locked_indices=locked_indices,
        element_map=element_map,
        parent_map=parent_map,
        transform_map=transform_map,
    ) if apply_openings else {
        "opening_candidate_count": len(plan.get("same_layer_opening_candidates", [])),
        "same_layer_opening_split_count": 0,
        "same_layer_segment_added_count": 0,
        "opened_element_indices": [],
        "opening_mutations": [],
        "opening_skips": [],
        "locked_target_skips": [],
    }
    opened_indices = set(opening_summary["opened_element_indices"])
    endpoint_summary = apply_endpoint_move_candidates(
        root,
        plan,
        max_endpoint_moves,
        locked_indices=locked_indices,
        excluded_indices=opened_indices,
        element_map=element_map,
        transform_map=transform_map,
    ) if apply_endpoint_moves else {
        "endpoint_move_candidate_count": len(plan.get("same_layer_endpoint_move_candidates", [])),
        "same_layer_endpoint_move_count": 0,
        "endpoint_moved_element_indices": [],
        "endpoint_move_mutations": [],
        "endpoint_move_skips": [],
        "locked_target_skips": [],
    }
    endpoint_moved_indices = set(endpoint_summary["endpoint_moved_element_indices"])
    selected, mutable_locked_skips = select_candidates(plan, max_mutations, locked_indices=locked_indices)
    selected = [candidate for candidate in selected if selected_candidate_index(candidate) not in opened_indices and selected_candidate_index(candidate) not in endpoint_moved_indices]
    mutated: list[dict[str, Any]] = []
    missing: list[int] = []
    for candidate in selected:
        element_index = selected_candidate_index(candidate) or 0
        element = element_map.get(element_index)
        if element is None:
            missing.append(element_index)
            continue
        mutated.append(patch_existing_element(element, candidate, len(mutated) + 1))

    locked_report = locked_preservation_report(locked_before)
    locked_target_skips = [*opening_summary["locked_target_skips"], *endpoint_summary["locked_target_skips"], *mutable_locked_skips]
    removal_geometry_mutation_count = sum(1 for item in mutated if item.get("geometry_mutated"))
    opening_program_cluster_count = sum(1 for item in opening_summary["opening_mutations"] if item.get("program_cluster_id"))
    endpoint_program_cluster_count = sum(1 for item in endpoint_summary["endpoint_move_mutations"] if item.get("program_cluster_id"))
    return {
        "mutation_strategy": "same_layer_geometry_patch",
        "patch_plan_status": plan.get("status"),
        "patch_plan_candidate_count": len(plan.get("same_layer_mutable_candidates", [])),
        "selected_candidate_count": len(selected),
        "same_layer_mutation_count": len(mutated),
        "same_layer_removal_count": sum(1 for item in mutated if item.get("same_layer_removed")),
        "same_layer_geometry_mutation_count": removal_geometry_mutation_count + opening_summary["same_layer_opening_split_count"] + endpoint_summary["same_layer_endpoint_move_count"],
        "same_layer_opening_split_count": opening_summary["same_layer_opening_split_count"],
        "same_layer_segment_added_count": opening_summary["same_layer_segment_added_count"],
        "same_layer_endpoint_move_count": endpoint_summary["same_layer_endpoint_move_count"],
        "program_cluster_mutation_count": sum(1 for item in mutated if item.get("program_cluster_id")) + opening_program_cluster_count + endpoint_program_cluster_count,
        "missing_element_indices": missing,
        "mutations": mutated,
        "opening_mutations": opening_summary["opening_mutations"],
        "opening_skips": opening_summary["opening_skips"],
        "endpoint_move_mutations": endpoint_summary["endpoint_move_mutations"],
        "endpoint_move_skips": endpoint_summary["endpoint_move_skips"],
        "locked_preservation": locked_report,
        "locked_target_skip_count": len(locked_target_skips),
        "locked_target_skips": locked_target_skips,
        "locked_targets_not_selected": not locked_target_skips,
    }
