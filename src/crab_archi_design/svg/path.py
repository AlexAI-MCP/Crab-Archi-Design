from __future__ import annotations

from dataclasses import dataclass
import math
import re

from crab_archi_design.svg.geometry import Point

TOKEN_RE = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class PathSubpath:
    points: list[Point]
    is_closed: bool


@dataclass(frozen=True)
class ParsedPath:
    subpaths: list[PathSubpath]
    warnings: list[str]


def parse_path(raw: str | None, curve_steps: int = 8) -> ParsedPath:
    if not raw:
        return ParsedPath([], [])
    tokens = TOKEN_RE.findall(raw)
    index = 0
    command = ""
    current = (0.0, 0.0)
    subpath_start = (0.0, 0.0)
    active_points: list[Point] = []
    subpaths: list[PathSubpath] = []
    warnings: list[str] = []
    last_cubic_control: Point | None = None
    last_quad_control: Point | None = None

    def flush(is_closed: bool = False) -> None:
        nonlocal active_points
        if active_points:
            subpaths.append(PathSubpath(active_points, is_closed))
        active_points = []

    def is_command(value: str) -> bool:
        return len(value) == 1 and value.isalpha()

    def read_number() -> float | None:
        nonlocal index
        if index >= len(tokens) or is_command(tokens[index]):
            return None
        value = float(tokens[index])
        index += 1
        return value

    def read_point(relative: bool) -> Point | None:
        x = read_number()
        y = read_number()
        if x is None or y is None:
            return None
        if relative:
            return current[0] + x, current[1] + y
        return x, y

    def append_point(point: Point) -> None:
        if not active_points:
            active_points.append(current)
        active_points.append(point)

    while index < len(tokens):
        if is_command(tokens[index]):
            command = tokens[index]
            index += 1
        if not command:
            warnings.append("path_missing_initial_command")
            break

        op = command.upper()
        relative = command.islower()
        if op == "Z":
            if active_points:
                if active_points[-1] != subpath_start:
                    active_points.append(subpath_start)
                current = subpath_start
                flush(True)
            last_cubic_control = None
            last_quad_control = None
            command = ""
            continue

        if op == "M":
            first = True
            while index < len(tokens) and not is_command(tokens[index]):
                point = read_point(relative)
                if point is None:
                    warnings.append("path_move_incomplete")
                    break
                if first:
                    flush(False)
                    current = point
                    subpath_start = point
                    active_points = [point]
                    first = False
                else:
                    append_point(point)
                    current = point
            command = "l" if relative else "L"
            last_cubic_control = None
            last_quad_control = None
            continue

        if op == "L":
            while index < len(tokens) and not is_command(tokens[index]):
                point = read_point(relative)
                if point is None:
                    warnings.append("path_line_incomplete")
                    break
                append_point(point)
                current = point
            last_cubic_control = None
            last_quad_control = None
            continue

        if op == "H":
            while index < len(tokens) and not is_command(tokens[index]):
                x = read_number()
                if x is None:
                    warnings.append("path_horizontal_incomplete")
                    break
                point = ((current[0] + x) if relative else x, current[1])
                append_point(point)
                current = point
            last_cubic_control = None
            last_quad_control = None
            continue

        if op == "V":
            while index < len(tokens) and not is_command(tokens[index]):
                y = read_number()
                if y is None:
                    warnings.append("path_vertical_incomplete")
                    break
                point = (current[0], (current[1] + y) if relative else y)
                append_point(point)
                current = point
            last_cubic_control = None
            last_quad_control = None
            continue

        if op == "C":
            while index < len(tokens) and not is_command(tokens[index]):
                c1 = read_point(relative)
                c2 = read_point(relative)
                end = read_point(relative)
                if c1 is None or c2 is None or end is None:
                    warnings.append("path_cubic_incomplete")
                    break
                for point in flatten_cubic(current, c1, c2, end, curve_steps):
                    append_point(point)
                current = end
                last_cubic_control = c2
                last_quad_control = None
            continue

        if op == "S":
            while index < len(tokens) and not is_command(tokens[index]):
                c1 = reflect_point(last_cubic_control, current) if last_cubic_control else current
                c2 = read_point(relative)
                end = read_point(relative)
                if c2 is None or end is None:
                    warnings.append("path_smooth_cubic_incomplete")
                    break
                for point in flatten_cubic(current, c1, c2, end, curve_steps):
                    append_point(point)
                current = end
                last_cubic_control = c2
                last_quad_control = None
            continue

        if op == "Q":
            while index < len(tokens) and not is_command(tokens[index]):
                control = read_point(relative)
                end = read_point(relative)
                if control is None or end is None:
                    warnings.append("path_quadratic_incomplete")
                    break
                for point in flatten_quadratic(current, control, end, curve_steps):
                    append_point(point)
                current = end
                last_quad_control = control
                last_cubic_control = None
            continue

        if op == "T":
            while index < len(tokens) and not is_command(tokens[index]):
                control = reflect_point(last_quad_control, current) if last_quad_control else current
                end = read_point(relative)
                if end is None:
                    warnings.append("path_smooth_quadratic_incomplete")
                    break
                for point in flatten_quadratic(current, control, end, curve_steps):
                    append_point(point)
                current = end
                last_quad_control = control
                last_cubic_control = None
            continue

        if op == "A":
            while index < len(tokens) and not is_command(tokens[index]):
                args = [read_number() for _ in range(7)]
                if any(item is None for item in args):
                    warnings.append("path_arc_incomplete")
                    break
                rx = float(args[0])  # type: ignore[arg-type]
                ry = float(args[1])  # type: ignore[arg-type]
                x_axis_rotation = float(args[2])  # type: ignore[arg-type]
                large_arc_flag = bool(int(float(args[3])))  # type: ignore[arg-type]
                sweep_flag = bool(int(float(args[4])))  # type: ignore[arg-type]
                end = (float(args[5]), float(args[6]))  # type: ignore[arg-type]
                if relative:
                    end = current[0] + end[0], current[1] + end[1]
                arc_points, arc_warning = flatten_arc(current, rx, ry, x_axis_rotation, large_arc_flag, sweep_flag, end, curve_steps)
                for point in arc_points:
                    append_point(point)
                current = end
                if arc_warning:
                    warnings.append(arc_warning)
            last_cubic_control = None
            last_quad_control = None
            continue

        warnings.append(f"path_unsupported_command:{command}")
        while index < len(tokens) and not is_command(tokens[index]):
            index += 1

    flush(False)
    return ParsedPath(subpaths, warnings)


def flatten_path_points(parsed: ParsedPath) -> list[Point]:
    points: list[Point] = []
    for subpath in parsed.subpaths:
        points.extend(subpath.points)
    return points


def path_is_closed(parsed: ParsedPath) -> bool:
    return bool(parsed.subpaths) and all(subpath.is_closed for subpath in parsed.subpaths)


def reflect_point(control: Point | None, around: Point) -> Point:
    if control is None:
        return around
    return (around[0] * 2.0 - control[0], around[1] * 2.0 - control[1])


def flatten_cubic(start: Point, c1: Point, c2: Point, end: Point, steps: int) -> list[Point]:
    return [cubic_point(start, c1, c2, end, step / steps) for step in range(1, steps + 1)]


def cubic_point(start: Point, c1: Point, c2: Point, end: Point, t: float) -> Point:
    inv = 1.0 - t
    x = inv**3 * start[0] + 3 * inv**2 * t * c1[0] + 3 * inv * t**2 * c2[0] + t**3 * end[0]
    y = inv**3 * start[1] + 3 * inv**2 * t * c1[1] + 3 * inv * t**2 * c2[1] + t**3 * end[1]
    return x, y


def flatten_quadratic(start: Point, control: Point, end: Point, steps: int) -> list[Point]:
    return [quadratic_point(start, control, end, step / steps) for step in range(1, steps + 1)]


def quadratic_point(start: Point, control: Point, end: Point, t: float) -> Point:
    inv = 1.0 - t
    x = inv**2 * start[0] + 2 * inv * t * control[0] + t**2 * end[0]
    y = inv**2 * start[1] + 2 * inv * t * control[1] + t**2 * end[1]
    return x, y


def flatten_arc(
    start: Point,
    rx: float,
    ry: float,
    x_axis_rotation: float,
    large_arc_flag: bool,
    sweep_flag: bool,
    end: Point,
    curve_steps: int,
) -> tuple[list[Point], str | None]:
    rx = abs(rx)
    ry = abs(ry)
    if points_equal(start, end):
        return [], "path_arc_same_point"
    if rx < 1e-12 or ry < 1e-12:
        return [end], "path_arc_degenerate_as_line"

    phi = math.radians(x_axis_rotation % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    x1, y1 = start
    x2, y2 = end
    dx2 = (x1 - x2) / 2.0
    dy2 = (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    radius_scale = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if radius_scale > 1.0:
        scale = math.sqrt(radius_scale)
        rx *= scale
        ry *= scale

    rx_sq = rx**2
    ry_sq = ry**2
    x1p_sq = x1p**2
    y1p_sq = y1p**2
    denominator = rx_sq * y1p_sq + ry_sq * x1p_sq
    if denominator < 1e-12:
        return [end], "path_arc_degenerate_as_line"
    sign = -1.0 if large_arc_flag == sweep_flag else 1.0
    numerator = max(0.0, rx_sq * ry_sq - rx_sq * y1p_sq - ry_sq * x1p_sq)
    factor = sign * math.sqrt(numerator / denominator)
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    start_vector = ((x1p - cxp) / rx, (y1p - cyp) / ry)
    end_vector = ((-x1p - cxp) / rx, (-y1p - cyp) / ry)
    theta1 = vector_angle((1.0, 0.0), start_vector)
    delta_theta = vector_angle(start_vector, end_vector)
    if not sweep_flag and delta_theta > 0:
        delta_theta -= math.tau
    elif sweep_flag and delta_theta < 0:
        delta_theta += math.tau

    steps = max(1, int(math.ceil(abs(delta_theta) / math.pi * max(1, curve_steps))))
    points: list[Point] = []
    for step in range(1, steps + 1):
        theta = theta1 + delta_theta * (step / steps)
        cos_theta = math.cos(theta)
        sin_theta = math.sin(theta)
        x = cx + rx * cos_phi * cos_theta - ry * sin_phi * sin_theta
        y = cy + rx * sin_phi * cos_theta + ry * cos_phi * sin_theta
        points.append((x, y))
    points[-1] = end
    return points, None


def vector_angle(left: Point, right: Point) -> float:
    dot = left[0] * right[0] + left[1] * right[1]
    determinant = left[0] * right[1] - left[1] * right[0]
    return math.atan2(determinant, dot)


def points_equal(left: Point, right: Point, tolerance: float = 1e-12) -> bool:
    return abs(left[0] - right[0]) <= tolerance and abs(left[1] - right[1]) <= tolerance
