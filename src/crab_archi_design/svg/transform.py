from __future__ import annotations

import math
import re

from crab_archi_design.svg.geometry import Point

Matrix = tuple[float, float, float, float, float, float]
SVG_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def identity_matrix() -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply_matrix(left: Matrix, right: Matrix) -> Matrix:
    la, lb, lc, ld, le, lf = left
    ra, rb, rc, rd, re_, rf = right
    return (
        la * ra + lc * rb,
        lb * ra + ld * rb,
        la * rc + lc * rd,
        lb * rc + ld * rd,
        la * re_ + lc * rf + le,
        lb * re_ + ld * rf + lf,
    )


def apply_matrix(matrix: Matrix, point: Point) -> Point:
    a, b, c, d, e, f = matrix
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def parse_numbers(value: str) -> list[float]:
    return [float(item) for item in re.findall(SVG_NUMBER_RE, value.replace(",", " "))]


def parse_transform(raw: str | None) -> Matrix:
    if not raw:
        return identity_matrix()
    matrix = identity_matrix()
    for name, args in re.findall(r"([A-Za-z]+)\(([^)]*)\)", raw):
        numbers = parse_numbers(args)
        op = identity_matrix()
        if name == "matrix" and len(numbers) >= 6:
            op = tuple(numbers[:6])  # type: ignore[assignment]
        elif name == "translate" and numbers:
            op = (1.0, 0.0, 0.0, 1.0, numbers[0], numbers[1] if len(numbers) > 1 else 0.0)
        elif name == "scale" and numbers:
            sx = numbers[0]
            sy = numbers[1] if len(numbers) > 1 else sx
            op = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and numbers:
            angle = math.radians(numbers[0])
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            rotate = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(numbers) >= 3:
                cx, cy = numbers[1], numbers[2]
                op = multiply_matrix(multiply_matrix((1.0, 0.0, 0.0, 1.0, cx, cy), rotate), (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                op = rotate
        matrix = multiply_matrix(matrix, op)
    return matrix
