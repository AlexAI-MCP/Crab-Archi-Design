"""Live SVG canvas document for the Crab Archi Design CAD studio.

Holds one mutable SVG document in memory. Every graphic element gets a stable
``data-cid`` handle so the browser UI and MCP agents address the same objects.
All mutations go through :meth:`CanvasDocument.apply`, which snapshots the tree
for undo and bumps a revision counter that the browser polls.
"""

from __future__ import annotations

import copy
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from defusedxml.ElementTree import fromstring as safe_fromstring

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

GRAPHIC_TAGS = {
    "path", "line", "polyline", "polygon", "rect", "circle", "ellipse",
    "text", "image", "use", "g",
}
STYLE_ATTRS = [
    "style", "fill", "stroke", "stroke-width", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "fill-opacity", "stroke-opacity",
    "opacity", "font-size", "font-family", "font-weight",
]
ZONE_MODES = {
    "community_shell", "no_go_zone", "lock_boundary",
    "protect_zone", "mutable_zone", "projectable_zone",
}
ZONE_COLORS = {
    "community_shell": "#1f77b4",
    "no_go_zone": "#d62728",
    "lock_boundary": "#8c564b",
    "protect_zone": "#ff7f0e",
    "mutable_zone": "#2ca02c",
    "projectable_zone": "#17becf",
}
ZONE_LAYER_ID = "crab_zones"
DRAW_LAYER_ID = "crab_drawn"
MAX_UNDO = 50
_NUM_RE = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


def q(tag: str) -> str:
    return f"{{{SVG_NS}}}{tag}"


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def fmt(value: float) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def points_attr(points: list[list[float]]) -> str:
    return " ".join(f"{fmt(p[0])},{fmt(p[1])}" for p in points)


def element_bbox(element: ET.Element) -> list[float] | None:
    """Approximate bbox from geometry attributes (transforms ignored)."""
    tag = local_name(element)
    try:
        if tag == "line":
            xs = [float(element.get("x1", 0)), float(element.get("x2", 0))]
            ys = [float(element.get("y1", 0)), float(element.get("y2", 0))]
        elif tag in {"polyline", "polygon"}:
            nums = [float(v) for v in _NUM_RE.findall(element.get("points", ""))]
            xs, ys = nums[0::2], nums[1::2]
        elif tag == "rect":
            x, y = float(element.get("x", 0)), float(element.get("y", 0))
            xs = [x, x + float(element.get("width", 0))]
            ys = [y, y + float(element.get("height", 0))]
        elif tag == "circle":
            cx, cy, r = (float(element.get(k, 0)) for k in ("cx", "cy", "r"))
            xs, ys = [cx - r, cx + r], [cy - r, cy + r]
        elif tag == "ellipse":
            cx, cy = float(element.get("cx", 0)), float(element.get("cy", 0))
            rx, ry = float(element.get("rx", 0)), float(element.get("ry", 0))
            xs, ys = [cx - rx, cx + rx], [cy - ry, cy + ry]
        elif tag == "path":
            nums = [float(v) for v in _NUM_RE.findall(element.get("d", ""))]
            xs, ys = nums[0::2], nums[1::2]
        elif tag == "text":
            x, y = float(element.get("x", 0)), float(element.get("y", 0))
            xs, ys = [x], [y]
        elif tag in {"g", "use", "image"}:
            boxes = [element_bbox(child) for child in element]
            boxes = [b for b in boxes if b]
            if not boxes:
                return None
            return [min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)]
        else:
            return None
        if not xs or not ys:
            return None
        return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
    except (ValueError, TypeError):
        return None


class CanvasError(ValueError):
    pass


class CanvasDocument:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.root: ET.Element | None = None
        self.source_path: Path | None = None
        self.revision = 0
        self._undo: list[str] = []
        self._cid_seq = 0

    # ---- lifecycle -------------------------------------------------------

    def load(self, path: str) -> dict[str, Any]:
        svg_path = Path(path).expanduser()
        if not svg_path.is_file() or svg_path.suffix.lower() != ".svg":
            raise CanvasError(f"SVG file not found: {path}")
        return self.load_text(svg_path.read_text(encoding="utf-8", errors="replace"), source=svg_path)

    def load_text(self, text: str, source: Path | None = None) -> dict[str, Any]:
        root = safe_fromstring(text)
        if local_name(root) != "svg":
            raise CanvasError("root element is not <svg>")
        with self.lock:
            self.root = root
            self.source_path = source
            self._undo.clear()
            self._cid_seq = 0
            self._assign_cids()
            self.revision += 1
            return self.status()

    def require_root(self) -> ET.Element:
        if self.root is None:
            raise CanvasError("no SVG loaded — call open_svg first")
        return self.root

    def _assign_cids(self) -> None:
        root = self.require_root()
        seen: set[str] = set()
        for element in root.iter():
            if local_name(element) not in GRAPHIC_TAGS:
                continue
            cid = element.get("data-cid")
            if not cid or cid in seen:
                self._cid_seq += 1
                cid = f"e{self._cid_seq:04d}"
                element.set("data-cid", cid)
            seen.add(cid)
            self._cid_seq = max(self._cid_seq, int(m.group(1)) if (m := re.fullmatch(r"e(\d+)", cid)) else 0)

    def _new_cid(self) -> str:
        self._cid_seq += 1
        return f"e{self._cid_seq:04d}"

    def to_string(self) -> str:
        return ET.tostring(self.require_root(), encoding="unicode")

    def save(self, path: str | None = None) -> str:
        target = Path(path).expanduser() if path else self.source_path
        if target is None:
            raise CanvasError("no target path — pass path or load from a file first")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<?xml version='1.0' encoding='utf-8'?>\n" + self.to_string(), encoding="utf-8")
        return str(target.resolve())

    # ---- queries ---------------------------------------------------------

    def viewbox(self) -> list[float] | None:
        raw = self.require_root().get("viewBox")
        if raw:
            try:
                return [float(v) for v in raw.replace(",", " ").split()]
            except ValueError:
                return None
        try:
            return [0.0, 0.0, float(self.require_root().get("width", 0)), float(self.require_root().get("height", 0))]
        except ValueError:
            return None

    def status(self) -> dict[str, Any]:
        with self.lock:
            if self.root is None:
                return {"loaded": False, "revision": self.revision}
            return {
                "loaded": True,
                "revision": self.revision,
                "source_path": str(self.source_path) if self.source_path else None,
                "viewBox": self.viewbox(),
                "element_count": sum(1 for _ in self.root.iter() if local_name(_) in GRAPHIC_TAGS),
                "zone_count": len(self.list_zones()),
                "undo_depth": len(self._undo),
            }

    def find(self, cid: str) -> ET.Element:
        for element in self.require_root().iter():
            if element.get("data-cid") == cid:
                return element
        raise CanvasError(f"element not found: {cid}")

    def parent_of(self, target: ET.Element) -> ET.Element | None:
        for element in self.require_root().iter():
            for child in element:
                if child is target:
                    return element
        return None

    def describe(self, element: ET.Element) -> dict[str, Any]:
        text = "".join(element.itertext()).strip()
        info: dict[str, Any] = {
            "cid": element.get("data-cid"),
            "tag": local_name(element),
            "id": element.get("id"),
            "bbox": element_bbox(element),
            "style": {k: element.get(k) for k in STYLE_ATTRS if element.get(k)},
        }
        if text:
            info["text"] = text[:120]
        if local_name(element) == "g":
            info["children"] = sum(1 for c in element.iter() if c is not element and local_name(c) in GRAPHIC_TAGS)
        return info

    def list_elements(self, tag: str | None = None, max_results: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            results = []
            for element in self.require_root().iter():
                name = local_name(element)
                if name not in GRAPHIC_TAGS or (tag and name != tag):
                    continue
                results.append(self.describe(element))
                if len(results) >= max_results:
                    break
            return results

    def list_zones(self) -> list[dict[str, Any]]:
        layer = self._zone_layer(create=False)
        if layer is None:
            return []
        zones = []
        for poly in layer:
            zones.append({
                "cid": poly.get("data-cid"),
                "mode": poly.get("data-zone-mode"),
                "name": poly.get("data-zone-name"),
                "points": [[float(v) for v in pair.split(",")] for pair in poly.get("points", "").split()],
            })
        return zones

    # ---- mutation core ---------------------------------------------------

    def _snapshot(self) -> None:
        self._undo.append(self.to_string())
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)

    def apply(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"op_{op}", None)
        if handler is None:
            raise CanvasError(f"unknown op: {op}")
        with self.lock:
            self.require_root()
            self._snapshot()
            try:
                result = handler(**params)
            except TypeError as exc:
                self._undo.pop()
                raise CanvasError(f"bad parameters for {op}: {exc}") from exc
            except CanvasError:
                self._undo.pop()
                raise
            self.revision += 1
            return {"op": op, "revision": self.revision, **(result or {})}

    def undo(self) -> dict[str, Any]:
        with self.lock:
            if not self._undo:
                raise CanvasError("nothing to undo")
            self.root = safe_fromstring(self._undo.pop())
            self.revision += 1
            return self.status()

    # ---- layers ----------------------------------------------------------

    def _layer(self, layer_id: str, create: bool = True) -> ET.Element | None:
        root = self.require_root()
        for element in root:
            if local_name(element) == "g" and element.get("id") == layer_id:
                return element
        if not create:
            return None
        layer = ET.SubElement(root, q("g"), {"id": layer_id, "data-cid": self._new_cid()})
        return layer

    def _zone_layer(self, create: bool = True) -> ET.Element | None:
        return self._layer(ZONE_LAYER_ID, create)

    def _apply_style(self, element: ET.Element, style: dict[str, Any] | None) -> None:
        for key, value in (style or {}).items():
            if key in STYLE_ATTRS and value is not None:
                element.set(key, str(value))

    def _add_shape(self, tag: str, attrs: dict[str, str], style: dict[str, Any] | None,
                   defaults: dict[str, str]) -> dict[str, Any]:
        layer = self._layer(DRAW_LAYER_ID)
        merged = {**defaults, **attrs, "data-cid": self._new_cid()}
        element = ET.SubElement(layer, q(tag), merged)
        self._apply_style(element, style)
        return {"cid": element.get("data-cid"), "element": self.describe(element)}

    # ---- ops: drawing ----------------------------------------------------

    def op_draw_line(self, x1: float, y1: float, x2: float, y2: float,
                     style: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._add_shape("line", {"x1": fmt(x1), "y1": fmt(y1), "x2": fmt(x2), "y2": fmt(y2)},
                               style, {"stroke": "#111111", "stroke-width": "2"})

    def op_draw_polyline(self, points: list[list[float]], closed: bool = False,
                         style: dict[str, Any] | None = None) -> dict[str, Any]:
        if len(points) < 2:
            raise CanvasError("polyline needs at least 2 points")
        tag = "polygon" if closed else "polyline"
        return self._add_shape(tag, {"points": points_attr(points)}, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"})

    def op_draw_path(self, d: str, style: dict[str, Any] | None = None) -> dict[str, Any]:
        if not d.strip() or d.strip()[0].upper() != "M":
            raise CanvasError("path d must start with a moveto (M)")
        return self._add_shape("path", {"d": d.strip()}, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"})

    def op_draw_curve(self, points: list[list[float]], style: dict[str, Any] | None = None) -> dict[str, Any]:
        """Smooth open curve through the given points (Catmull-Rom → cubic Bezier)."""
        if len(points) < 3:
            raise CanvasError("curve needs at least 3 points")
        p = [[float(a), float(b)] for a, b in points]
        d = f"M {fmt(p[0][0])} {fmt(p[0][1])}"
        for i in range(len(p) - 1):
            p0 = p[i - 1] if i > 0 else p[i]
            p3 = p[i + 2] if i + 2 < len(p) else p[i + 1]
            c1 = [p[i][0] + (p[i + 1][0] - p0[0]) / 6, p[i][1] + (p[i + 1][1] - p0[1]) / 6]
            c2 = [p[i + 1][0] - (p3[0] - p[i][0]) / 6, p[i + 1][1] - (p3[1] - p[i][1]) / 6]
            d += (f" C {fmt(c1[0])} {fmt(c1[1])}, {fmt(c2[0])} {fmt(c2[1])},"
                  f" {fmt(p[i + 1][0])} {fmt(p[i + 1][1])}")
        return self._add_shape("path", {"d": d}, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"})

    def op_draw_rect(self, x: float, y: float, width: float, height: float,
                     rx: float | None = None, style: dict[str, Any] | None = None) -> dict[str, Any]:
        attrs = {"x": fmt(x), "y": fmt(y), "width": fmt(width), "height": fmt(height)}
        if rx is not None:
            attrs["rx"] = fmt(rx)
        return self._add_shape("rect", attrs, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"})

    def op_draw_ellipse(self, cx: float, cy: float, rx: float, ry: float | None = None,
                        style: dict[str, Any] | None = None) -> dict[str, Any]:
        if ry is None or abs(float(ry) - float(rx)) < 1e-9:
            return self._add_shape("circle", {"cx": fmt(cx), "cy": fmt(cy), "r": fmt(rx)}, style,
                                   {"stroke": "#111111", "stroke-width": "2", "fill": "none"})
        return self._add_shape("ellipse", {"cx": fmt(cx), "cy": fmt(cy), "rx": fmt(rx), "ry": fmt(ry)},
                               style, {"stroke": "#111111", "stroke-width": "2", "fill": "none"})

    def op_add_text(self, x: float, y: float, text: str,
                    style: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._add_shape("text", {"x": fmt(x), "y": fmt(y)}, style,
                                 {"fill": "#111111", "font-size": "16"})
        self.find(result["cid"]).text = str(text)
        return result

    # ---- ops: editing ----------------------------------------------------

    def op_delete_elements(self, cids: list[str]) -> dict[str, Any]:
        deleted = []
        for cid in cids:
            element = self.find(cid)
            parent = self.parent_of(element)
            if parent is None:
                raise CanvasError(f"cannot delete root-level element: {cid}")
            parent.remove(element)
            deleted.append(cid)
        return {"deleted": deleted}

    def op_move_element(self, cid: str, dx: float, dy: float) -> dict[str, Any]:
        element = self.find(cid)
        existing = element.get("transform", "")
        element.set("transform", f"translate({fmt(dx)},{fmt(dy)}) {existing}".strip())
        return {"cid": cid, "element": self.describe(element)}

    def op_copy_element(self, cid: str, dx: float = 0.0, dy: float = 0.0) -> dict[str, Any]:
        source = self.find(cid)
        parent = self.parent_of(source)
        if parent is None:
            parent = self.require_root()
        clone = copy.deepcopy(source)
        for node in clone.iter():
            if node.get("data-cid"):
                node.set("data-cid", self._new_cid())
            node.attrib.pop("id", None)
        if dx or dy:
            existing = clone.get("transform", "")
            clone.set("transform", f"translate({fmt(dx)},{fmt(dy)}) {existing}".strip())
        parent.append(clone)
        return {"cid": clone.get("data-cid"), "element": self.describe(clone)}

    def op_copy_style(self, source_cid: str, target_cids: list[str]) -> dict[str, Any]:
        source = self.find(source_cid)
        style = {k: source.get(k) for k in STYLE_ATTRS if source.get(k)}
        if not style:
            raise CanvasError(f"source element has no style attributes: {source_cid}")
        for cid in target_cids:
            target = self.find(cid)
            for key in STYLE_ATTRS:
                target.attrib.pop(key, None)
            for key, value in style.items():
                target.set(key, value)
        return {"style": style, "targets": target_cids}

    def op_set_style(self, cids: list[str], style: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in style.items() if k in STYLE_ATTRS}
        if not clean:
            raise CanvasError(f"no valid style keys; allowed: {', '.join(STYLE_ATTRS)}")
        for cid in cids:
            self._apply_style(self.find(cid), clean)
        return {"style": clean, "targets": cids}

    def op_set_attrs(self, cid: str, attrs: dict[str, Any]) -> dict[str, Any]:
        element = self.find(cid)
        for key, value in attrs.items():
            if key in {"data-cid"}:
                continue
            if value is None:
                element.attrib.pop(key, None)
            else:
                element.set(key, str(value))
        return {"cid": cid, "element": self.describe(element)}

    # ---- ops: zones ------------------------------------------------------

    def op_set_zone(self, mode: str, points: list[list[float]],
                    name: str | None = None) -> dict[str, Any]:
        if mode not in ZONE_MODES:
            raise CanvasError(f"unknown zone mode {mode!r}; allowed: {', '.join(sorted(ZONE_MODES))}")
        if len(points) < 3:
            raise CanvasError("zone needs at least 3 points")
        layer = self._zone_layer()
        color = ZONE_COLORS[mode]
        poly = ET.SubElement(layer, q("polygon"), {
            "points": points_attr(points),
            "data-cid": self._new_cid(),
            "data-zone-mode": mode,
            "data-zone-name": name or mode,
            "fill": color, "fill-opacity": "0.12",
            "stroke": color, "stroke-width": "2", "stroke-dasharray": "8 4",
        })
        return {"cid": poly.get("data-cid"), "zone": {"mode": mode, "name": name or mode}}

    def op_clear_zones(self, mode: str | None = None) -> dict[str, Any]:
        layer = self._zone_layer(create=False)
        if layer is None:
            return {"cleared": 0}
        victims = [p for p in list(layer) if mode is None or p.get("data-zone-mode") == mode]
        for poly in victims:
            layer.remove(poly)
        return {"cleared": len(victims)}

    def zones_as_strokes(self) -> list[dict[str, Any]]:
        """Zones in the studio constraint-sketch stroke format (for regeneration)."""
        strokes = []
        for index, zone in enumerate(self.list_zones(), start=1):
            points = zone["points"]
            if points and points[0] != points[-1]:
                points = points + [points[0]]
            strokes.append({
                "stroke_id": f"canvas_zone_{index:03d}",
                "mode": zone["mode"],
                "target_hint": zone["name"] or zone["mode"],
                "points": points,
            })
        return strokes
