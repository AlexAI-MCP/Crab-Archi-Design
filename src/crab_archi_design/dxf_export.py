"""Export the live canvas document to DXF (and DWG via ODA File Converter when installed).

Coordinates are emitted in real millimeters when the document has a scale
(set_scale), with the Y axis flipped from SVG (y-down) to CAD (y-up) about the
viewBox top so the drawing lands in positive coordinates. Layers follow the
canvas layer groups (crab_layer_*, crab_drawn, crab_zones, other top-level
group ids); native entities are used where the geometry survives the element's
accumulated transform, everything else is flattened to LWPOLYLINEs.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from crab_archi_design.canvas_document import (
    GRAPHIC_TAGS,
    IDENTITY,
    CanvasDocument,
    CanvasError,
    _attr_num,
    _matrix_scale_factor,
    apply_matrix,
    compose,
    flatten_path,
    local_name,
    local_points,
    parse_style_attr,
    parse_transform,
)

# DXF standard lineweights in 1/100 mm
_LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70,
                80, 90, 100, 106, 120, 140, 158, 200, 211]
_COLOR_NAMES = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "orange": (255, 165, 0), "gray": (128, 128, 128), "grey": (128, 128, 128),
    "magenta": (255, 0, 255), "cyan": (0, 255, 255), "brown": (165, 42, 42),
}
_SKIP_CONTAINERS = {"defs", "clipPath", "mask", "pattern", "marker", "symbol", "metadata", "style"}


def _parse_color(raw: str | None) -> tuple[int, int, int] | None:
    if not raw:
        return None
    value = raw.strip().lower()
    if value in {"none", "transparent", "inherit", "currentcolor"}:
        return None
    if value in _COLOR_NAMES:
        return _COLOR_NAMES[value]
    match = re.fullmatch(r"#([0-9a-f]{3})", value)
    if match:
        r, g, b = (int(c * 2, 16) for c in match.group(1))
        return (r, g, b)
    match = re.fullmatch(r"#([0-9a-f]{6})", value)
    if match:
        raw6 = match.group(1)
        return tuple(int(raw6[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    match = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    if match:
        return tuple(min(255, int(v)) for v in match.groups())  # type: ignore[return-value]
    return None


def _layer_name(group_id: str | None) -> str:
    if not group_id:
        return "0"
    name = re.sub(r"^crab_layer_", "", group_id)
    name = re.sub(r"[^A-Za-z0-9_\-가-힣]", "_", name)[:60]
    return name.upper() or "0"


def _nearest_lineweight(width_mm: float) -> int:
    target = width_mm * 100.0
    return min(_LINEWEIGHTS, key=lambda lw: abs(lw - target))


class _Exporter:
    def __init__(self, doc: CanvasDocument) -> None:
        import ezdxf

        self.doc = doc
        self.mm = doc.mm_per_unit()
        self.factor = self.mm if self.mm else 1.0
        viewbox = doc.viewbox() or [0.0, 0.0, 1000.0, 1000.0]
        self.x0 = viewbox[0]
        self.ymax = viewbox[1] + viewbox[3]
        self.dxf = ezdxf.new("R2010", setup=True)
        self.dxf.header["$INSUNITS"] = 4 if self.mm else 0
        self.msp = self.dxf.modelspace()
        self.counts: dict[str, int] = {}
        self.skipped = 0
        self.layers: set[str] = set()

    # SVG world coordinates → DXF (mm when scaled, y-up)
    def pt(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.x0) * self.factor, (self.ymax - y) * self.factor)

    def _ensure_layer(self, name: str) -> None:
        if name not in self.layers and name != "0":
            if name not in self.dxf.layers:
                self.dxf.layers.add(name)
        self.layers.add(name)

    def _style_of(self, element: Any) -> dict[str, str]:
        style = {k: element.get(k) for k in ("stroke", "fill", "stroke-width", "font-size") if element.get(k)}
        style.update(parse_style_attr(element.get("style")))
        return style

    def _attribs(self, element: Any, matrix: tuple[float, ...], layer: str) -> tuple[dict[str, Any], tuple[int, int, int] | None]:
        style = self._style_of(element)
        attribs: dict[str, Any] = {"layer": layer}
        rgb = _parse_color(style.get("stroke")) or _parse_color(style.get("fill"))
        raw_width = style.get("stroke-width")
        if raw_width and self.mm:
            match = re.search(r"-?\d+\.?\d*", raw_width)
            if match:
                width_mm = float(match.group(0)) * _matrix_scale_factor(matrix) * self.mm
                attribs["lineweight"] = _nearest_lineweight(width_mm)
        return attribs, rgb

    def _add(self, kind: str, entity: Any, rgb: tuple[int, int, int] | None) -> None:
        if rgb:
            entity.rgb = rgb
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def _polyline(self, points: list[tuple[float, float]], closed: bool,
                  attribs: dict[str, Any], rgb: tuple[int, int, int] | None) -> None:
        if len(points) < 2:
            return
        entity = self.msp.add_lwpolyline(points, close=closed, dxfattribs=attribs)
        self._add("LWPOLYLINE", entity, rgb)

    def _emit(self, element: Any, matrix: tuple[float, ...], layer: str) -> None:
        tag = local_name(element)
        attribs, rgb = self._attribs(element, matrix, layer)
        conformal = (abs(matrix[0] - matrix[3]) < 1e-9 and abs(matrix[1] + matrix[2]) < 1e-9)
        axis_aligned = abs(matrix[1]) < 1e-9 and abs(matrix[2]) < 1e-9

        if tag == "line":
            p1 = self.pt(*apply_matrix(matrix, _attr_num(element, "x1"), _attr_num(element, "y1")))
            p2 = self.pt(*apply_matrix(matrix, _attr_num(element, "x2"), _attr_num(element, "y2")))
            self._add("LINE", self.msp.add_line(p1, p2, dxfattribs=attribs), rgb)
        elif tag in {"polyline", "polygon", "rect"}:
            pts = local_points(element)
            if not pts:
                self.skipped += 1
                return
            if tag == "rect":  # corner order → perimeter order
                pts = [pts[0], pts[1], pts[3], pts[2]]
            world = [self.pt(*apply_matrix(matrix, x, y)) for x, y in pts]
            self._polyline(world, tag != "polyline", attribs, rgb)
        elif tag == "circle" and conformal:
            r = _attr_num(element, "r") * _matrix_scale_factor(matrix) * self.factor
            center = self.pt(*apply_matrix(matrix, _attr_num(element, "cx"), _attr_num(element, "cy")))
            self._add("CIRCLE", self.msp.add_circle(center, r, dxfattribs=attribs), rgb)
        elif tag == "ellipse" and axis_aligned:
            rx = abs(_attr_num(element, "rx") * matrix[0]) * self.factor
            ry = abs(_attr_num(element, "ry") * matrix[3]) * self.factor
            if rx < 1e-9 or ry < 1e-9:
                self.skipped += 1
                return
            center = self.pt(*apply_matrix(matrix, _attr_num(element, "cx"), _attr_num(element, "cy")))
            major, ratio = ((rx, 0.0), ry / rx) if rx >= ry else ((0.0, ry), rx / ry)
            entity = self.msp.add_ellipse(center, major_axis=major, ratio=min(1.0, ratio), dxfattribs=attribs)
            self._add("ELLIPSE", entity, rgb)
        elif tag in {"circle", "ellipse"}:  # non-conformal → flatten
            for pts, closed in self.doc._element_polylines(element):
                world = [self.pt(*apply_matrix(matrix, x, y)) for x, y in pts]
                self._polyline(world, closed, attribs, rgb)
        elif tag == "path":
            for sub in flatten_path(element.get("d", ""), samples=16):
                closed = len(sub) > 2 and sub[0] == sub[-1]
                world = [self.pt(*apply_matrix(matrix, x, y)) for x, y in (sub[:-1] if closed else sub)]
                self._polyline(world, closed, attribs, rgb)
        elif tag == "text":
            content = "".join(element.itertext()).strip()
            if not content:
                self.skipped += 1
                return
            style = self._style_of(element)
            match = re.search(r"-?\d+\.?\d*", style.get("font-size") or "12")
            height = float(match.group(0)) * _matrix_scale_factor(matrix) * self.factor
            insert = self.pt(*apply_matrix(matrix, _attr_num(element, "x"), _attr_num(element, "y")))
            angle = math.degrees(math.atan2(-matrix[1], matrix[0]))  # y-flip negates rotation
            text_attribs = {**attribs, "height": max(height, 1e-6), "rotation": angle}
            rgb_text = _parse_color(style.get("fill")) or _parse_color(style.get("stroke")) or rgb
            self._add("TEXT", self.msp.add_text(content, dxfattribs={**text_attribs, "insert": insert}), rgb_text)
        else:  # use/image and anything unmapped
            self.skipped += 1

    def walk(self, element: Any, matrix: tuple[float, ...], layer: str) -> None:
        for child in element:
            tag = local_name(child)
            if tag in _SKIP_CONTAINERS:
                continue
            child_matrix = compose(matrix, parse_transform(child.get("transform")))
            if tag == "g":
                child_layer = layer
                if element is self.doc.require_root() or layer == "0":
                    child_layer = _layer_name(child.get("id")) if child.get("id") else layer
                self._ensure_layer(child_layer)
                self.walk(child, child_matrix, child_layer)
            elif tag in GRAPHIC_TAGS:
                self._ensure_layer(layer)
                try:
                    self._emit(child, child_matrix, layer)
                except (ValueError, KeyError):
                    self.skipped += 1


def find_oda_converter() -> str | None:
    candidates = [shutil.which("ODAFileConverter"),
                  "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"]
    return next((c for c in candidates if c and Path(c).exists()), None)


def export_cad(doc: CanvasDocument, path: str | None = None,
               fmt: str = "dxf") -> dict[str, Any]:
    """Write the document as DXF; fmt='dwg' additionally converts via ODA File Converter."""
    fmt = (fmt or "dxf").lower()
    if fmt not in {"dxf", "dwg"}:
        raise CanvasError("format must be dxf or dwg")
    with doc.lock:
        doc.require_root()
        if path:
            target = Path(path).expanduser()
        elif doc.source_path is not None:
            target = doc.source_path.with_suffix(".dxf")
        else:
            raise CanvasError("no target path — pass path or load from a file first")
        if target.suffix.lower() != ".dxf":
            target = target.with_suffix(".dxf")
        exporter = _Exporter(doc)
        exporter.walk(doc.require_root(), IDENTITY, "0")
        target.parent.mkdir(parents=True, exist_ok=True)
        exporter.dxf.saveas(target)
    result: dict[str, Any] = {
        "dxf": str(target.resolve()),
        "entities": exporter.counts,
        "layers": sorted(exporter.layers - {"0"}),
        "units": "mm" if exporter.mm else "drawing units (set_scale 미보정 — mm 좌표를 원하면 보정 후 재내보내기)",
        "skipped": exporter.skipped,
    }
    if fmt == "dwg":
        converter = find_oda_converter()
        if converter is None:
            result["dwg"] = None
            result["note"] = ("DWG 변환기는 설치되어 있지 않습니다. DXF는 AutoCAD/캐드 대부분에서 그대로 열립니다. "
                              "DWG 파일이 꼭 필요하면 무료 ODA File Converter(opendesign.com)를 설치하면 자동 변환됩니다.")
        else:
            with tempfile.TemporaryDirectory() as out_dir:
                completed = subprocess.run(
                    [converter, str(target.parent), out_dir, "ACAD2018", "DWG", "0", "1", target.name],
                    capture_output=True, text=True, timeout=120)
                produced = Path(out_dir) / (target.stem + ".dwg")
                if completed.returncode == 0 and produced.exists():
                    dwg_target = target.with_suffix(".dwg")
                    shutil.copyfile(produced, dwg_target)
                    result["dwg"] = str(dwg_target.resolve())
                else:
                    result["dwg"] = None
                    result["note"] = f"ODA 변환 실패: {(completed.stderr or completed.stdout or 'unknown').strip()[:200]}"
    return result
