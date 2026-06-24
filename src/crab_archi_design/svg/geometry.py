from __future__ import annotations

from dataclasses import dataclass
import math

Point = tuple[float, float]


@dataclass(frozen=True)
class BBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


def bbox_center(box: BBox) -> Point:
    return (box.x + box.width * 0.5, box.y + box.height * 0.5)


def polygon_area(points: list[Point]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        total += previous_x * current_y - current_x * previous_y
        previous_x, previous_y = current_x, current_y
    return abs(total) * 0.5


def polyline_length(points: list[Point], closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    previous = points[0]
    for current in points[1:]:
        total += math.dist(previous, current)
        previous = current
    if closed and len(points) > 2:
        total += math.dist(points[-1], points[0])
    return total


def scaled_polyline_length(points: list[Point], scale_x: float, scale_y: float, closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    scaled = [(x * scale_x, y * scale_y) for x, y in points]
    return polyline_length(scaled, closed)


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            at_x = (previous_x - current_x) * (y - current_y) / ((previous_y - current_y) or 1e-9) + current_x
            if x < at_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def quantize_point(point: Point, digits: int = 3) -> Point:
    return (round(point[0], digits), round(point[1], digits))
