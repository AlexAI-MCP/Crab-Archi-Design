"""SVG parsing and geometry primitives for recognition IR v2."""

from crab_archi_design.svg.geometry import BBox, Point, bbox_center, point_in_polygon
from crab_archi_design.svg.transform import Matrix, apply_matrix, identity_matrix, multiply_matrix

__all__ = [
    "BBox",
    "Matrix",
    "Point",
    "apply_matrix",
    "bbox_center",
    "identity_matrix",
    "multiply_matrix",
    "point_in_polygon",
]
