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


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_TRANSFORM_RE = re.compile(r"(\w+)\s*\(([^)]*)\)")


def compose(outer: tuple[float, ...], inner: tuple[float, ...]) -> tuple[float, ...]:
    """Combined matrix C such that C(p) == outer(inner(p))."""
    pa, pb, pc, pd, pe, pf = outer
    ma, mb, mc, md, me, mf = inner
    return (
        pa * ma + pc * mb, pb * ma + pd * mb,
        pa * mc + pc * md, pb * mc + pd * md,
        pa * me + pc * mf + pe, pb * me + pd * mf + pf,
    )


def apply_matrix(m: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(raw: str | None) -> tuple[float, ...]:
    """Parse an SVG transform attribute into a single (a,b,c,d,e,f) matrix."""
    if not raw:
        return IDENTITY
    combined = IDENTITY
    for name, args in _TRANSFORM_RE.findall(raw):
        try:
            nums = [float(v) for v in re.split(r"[,\s]+", args.strip()) if v]
        except ValueError:
            continue
        if name == "translate" and nums:
            tx = nums[0]
            ty = nums[1] if len(nums) > 1 else 0.0
            step = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale" and nums:
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else sx
            step = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and nums:
            import math
            theta = math.radians(nums[0])
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                step = compose((1.0, 0.0, 0.0, 1.0, cx, cy),
                               compose((cos_t, sin_t, -sin_t, cos_t, 0.0, 0.0),
                                       (1.0, 0.0, 0.0, 1.0, -cx, -cy)))
            else:
                step = (cos_t, sin_t, -sin_t, cos_t, 0.0, 0.0)
        elif name == "matrix" and len(nums) == 6:
            step = tuple(nums)
        elif name == "skewX" and nums:
            import math
            step = (1.0, 0.0, math.tan(math.radians(nums[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and nums:
            import math
            step = (1.0, math.tan(math.radians(nums[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        combined = compose(combined, step)
    return combined


_PATH_CMD_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")
# coordinate values consumed per repetition, and which indices are (x, y) pairs
_PATH_ARITY = {
    "M": 2, "L": 2, "T": 2, "C": 6, "S": 4, "Q": 4, "H": 1, "V": 1, "A": 7, "Z": 0,
}


def parse_path_segments(d: str) -> list[list[Any]]:
    """Parse path data into absolute segments [cmd, [nums…]].

    All coordinates come out absolute; H/V are normalized to L so every
    consumer sees full (x, y) pairs. Arc (A) keeps [rx, ry, rot, laf, sf, x, y]
    with only the endpoint absolutized. Implicit lineto after M is honored.
    """
    segs: list[list[Any]] = []
    cx = cy = sx = sy = 0.0
    for cmd, args in _PATH_CMD_RE.findall(d or ""):
        upper = cmd.upper()
        relative = cmd.islower()
        nums = [float(v) for v in _NUM_RE.findall(args)]
        if upper == "Z":
            segs.append(["Z", []])
            cx, cy = sx, sy
            continue
        arity = _PATH_ARITY[upper]
        if len(nums) < arity:
            continue
        first = True
        for i in range(0, len(nums) - arity + 1, arity):
            chunk = nums[i:i + arity]
            if upper == "H":
                cx = cx + chunk[0] if relative else chunk[0]
                segs.append(["L", [cx, cy]])
            elif upper == "V":
                cy = cy + chunk[0] if relative else chunk[0]
                segs.append(["L", [cx, cy]])
            elif upper == "A":
                ex, ey = chunk[5], chunk[6]
                if relative:
                    ex += cx
                    ey += cy
                segs.append(["A", chunk[:5] + [ex, ey]])
                cx, cy = ex, ey
            else:
                out = list(chunk)
                if relative:
                    for j in range(0, arity, 2):
                        out[j] += cx
                        out[j + 1] += cy
                op = "L" if upper == "M" and not first else upper
                segs.append([op, out])
                cx, cy = out[-2], out[-1]
                if op == "M":
                    sx, sy = cx, cy
            first = False
    return segs


def path_from_segments(segs: list[list[Any]]) -> str:
    return " ".join(op + " ".join(fmt(v) for v in nums) for op, nums in segs)


def transform_path(d: str, fn) -> tuple[str, bool]:
    """Apply fn(x, y) → (x, y) to every coordinate pair of a path.

    Returns (new_d, changed). The output is normalized to absolute commands.
    Arc radii/rotation/flags are preserved (exact for pure translation).
    """
    segs = parse_path_segments(d)
    changed = False
    for op, nums in segs:
        if op == "Z":
            continue
        pairs = [(5, 6)] if op == "A" else [(j, j + 1) for j in range(0, len(nums), 2)]
        for jx, jy in pairs:
            nx, ny = fn(nums[jx], nums[jy])
            if (nx, ny) != (nums[jx], nums[jy]):
                nums[jx], nums[jy] = nx, ny
                changed = True
    return path_from_segments(segs), changed


def path_points(d: str) -> list[tuple[float, float]] | None:
    """On-curve points of a path (control points included for C/S/Q as bbox hull).

    Arc (A) segments contribute only their endpoints — flags and radii are
    consumed but never misread as coordinates.
    """
    points: list[tuple[float, float]] = []
    for op, nums in parse_path_segments(d):
        if op == "A":
            points.append((nums[5], nums[6]))
        elif op != "Z":
            points.extend(zip(nums[0::2], nums[1::2]))
    return points or None


def invert_matrix(m: tuple[float, ...]) -> tuple[float, ...] | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-9:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def parse_style_attr(raw: str | None) -> dict[str, str]:
    """Parse an inline style="a:b;c:d" attribute into an ordered dict."""
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def flatten_path(d: str, samples: int = 12) -> list[list[tuple[float, float]]]:
    """Flatten path data to polyline subpaths (curves sampled, arcs chorded)."""
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    prev_ctrl: tuple[float, float] | None = None  # last cubic/quadratic control, for S/T reflection
    prev_op = ""
    for op, nums in parse_path_segments(d):
        if op == "M":
            if len(cur) > 1:
                subpaths.append(cur)
            cur = [(nums[0], nums[1])]
            prev_ctrl = None
        elif op == "Z":
            if len(cur) > 1 and cur[0] != cur[-1]:
                cur.append(cur[0])
            prev_ctrl = None
        elif op in {"L", "A"}:
            if not cur:
                cur = [(0.0, 0.0)]
            cur.append((nums[-2], nums[-1]))
            prev_ctrl = None
        elif op in {"C", "S", "Q", "T"}:
            if not cur:
                cur = [(0.0, 0.0)]
            x0, y0 = cur[-1]
            reflected = ((2 * x0 - prev_ctrl[0], 2 * y0 - prev_ctrl[1])
                         if prev_ctrl is not None else (x0, y0))
            if op == "C":
                c1, c2 = (nums[0], nums[1]), (nums[2], nums[3])
            elif op == "S":
                c1 = reflected if prev_op in {"C", "S"} else (x0, y0)
                c2 = (nums[0], nums[1])
            elif op == "Q":
                c1 = c2 = (nums[0], nums[1])
            else:  # T
                c1 = c2 = reflected if prev_op in {"Q", "T"} else (x0, y0)
            quad_ctrl = c1  # remember before quadratic→cubic conversion
            ex, ey = nums[-2], nums[-1]
            if op in {"Q", "T"}:
                qx, qy = quad_ctrl
                c1 = (x0 + 2.0 / 3.0 * (qx - x0), y0 + 2.0 / 3.0 * (qy - y0))
                c2 = (ex + 2.0 / 3.0 * (qx - ex), ey + 2.0 / 3.0 * (qy - ey))
            for k in range(1, samples + 1):
                t = k / samples
                mt = 1.0 - t
                cur.append((mt**3 * x0 + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * ex,
                            mt**3 * y0 + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * ey))
            prev_ctrl = c2 if op in {"C", "S"} else quad_ctrl
        prev_op = op
    if len(cur) > 1:
        subpaths.append(cur)
    return subpaths


def _attr_num(element: ET.Element, name: str, default: float = 0.0) -> float:
    """Numeric attribute value, tolerating unit suffixes like '0.5px' or '2mm'."""
    raw = element.get(name)
    if raw is None:
        return default
    match = _NUM_RE.search(raw)
    if match is None:
        raise ValueError(f"non-numeric {name}={raw!r}")
    return float(match.group(0))


def local_points(element: ET.Element) -> list[tuple[float, float]] | None:
    """Own-local-space defining points of a leaf element's geometry (pre-transform)."""
    tag = local_name(element)
    try:
        if tag == "line":
            return [(_attr_num(element, "x1"), _attr_num(element, "y1")),
                    (_attr_num(element, "x2"), _attr_num(element, "y2"))]
        if tag in {"polyline", "polygon"}:
            nums = [float(v) for v in _NUM_RE.findall(element.get("points", ""))]
            return list(zip(nums[0::2], nums[1::2])) or None
        if tag == "rect":
            x, y = _attr_num(element, "x"), _attr_num(element, "y")
            w, h = _attr_num(element, "width"), _attr_num(element, "height")
            return [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
        if tag == "circle":
            cx, cy, r = (_attr_num(element, k) for k in ("cx", "cy", "r"))
            return [(cx - r, cy - r), (cx + r, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
        if tag == "ellipse":
            cx, cy = _attr_num(element, "cx"), _attr_num(element, "cy")
            rx, ry = _attr_num(element, "rx"), _attr_num(element, "ry")
            return [(cx - rx, cy - ry), (cx + rx, cy - ry), (cx - rx, cy + ry), (cx + rx, cy + ry)]
        if tag == "path":
            return path_points(element.get("d", ""))
        if tag == "text":
            return [(_attr_num(element, "x"), _attr_num(element, "y"))]
        return None
    except (ValueError, TypeError):
        return None


class CanvasError(ValueError):
    pass


SCALE_ATTR = "data-crab-mm-per-unit"
PYEONG_M2 = 3.305785


class CanvasDocument:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.root: ET.Element | None = None
        self.source_path: Path | None = None
        self.revision = 0
        self._undo: list[str] = []
        self._cid_seq = 0
        self.autosave_path: Path | None = None
        self._autosave_at = 0.0
        self._draft_base: str | None = None
        self._parent_map: dict[int, ET.Element] | None = None
        self._parent_map_rev = -1
        self._bbox_cache: dict[int, list[float] | None] = {}
        self._bbox_cache_rev = -1

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
                "mm_per_unit": self.mm_per_unit(),
                "draft_open": self._draft_base is not None,
            }

    def find(self, cid: str) -> ET.Element:
        for element in self.require_root().iter():
            if element.get("data-cid") == cid:
                return element
        raise CanvasError(f"element not found: {cid}")

    def _ensure_parent_map(self) -> dict[int, ET.Element]:
        if self._parent_map is None or self._parent_map_rev != self.revision:
            root = self.require_root()
            parent_map: dict[int, ET.Element] = {}
            for element in root.iter():
                for child in element:
                    parent_map[id(child)] = element
            self._parent_map = parent_map
            self._parent_map_rev = self.revision
        return self._parent_map

    def parent_of(self, target: ET.Element) -> ET.Element | None:
        return self._ensure_parent_map().get(id(target))

    def _accumulated_transform(self, element: ET.Element) -> tuple[float, ...]:
        root = self.require_root()
        parent_map = self._ensure_parent_map()
        combined = IDENTITY
        node: ET.Element | None = element
        while node is not None and node is not root:
            combined = compose(parse_transform(node.get("transform")), combined)
            node = parent_map.get(id(node))
        return combined

    def world_bbox(self, element: ET.Element) -> list[float] | None:
        """Bounding box in the root SVG's coordinate space, honoring ancestor transforms."""
        if self._bbox_cache_rev != self.revision:
            self._bbox_cache = {}
            self._bbox_cache_rev = self.revision
        key = id(element)
        if key in self._bbox_cache:
            return self._bbox_cache[key]
        points = local_points(element)
        if points is not None:
            matrix = self._accumulated_transform(element)
            world = [apply_matrix(matrix, x, y) for x, y in points]
            xs = [p[0] for p in world]
            ys = [p[1] for p in world]
            result = [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
        else:
            boxes = [self.world_bbox(child) for child in element if local_name(child) in GRAPHIC_TAGS]
            boxes = [b for b in boxes if b]
            result = ([min(b[0] for b in boxes), min(b[1] for b in boxes),
                      max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None)
        self._bbox_cache[key] = result
        return result

    def describe(self, element: ET.Element) -> dict[str, Any]:
        text = "".join(element.itertext()).strip()
        info: dict[str, Any] = {
            "cid": element.get("data-cid"),
            "tag": local_name(element),
            "id": element.get("id"),
            "bbox": self.world_bbox(element),
            "style": {k: element.get(k) for k in STYLE_ATTRS if element.get(k)},
        }
        if text:
            info["text"] = text[:120]
        if local_name(element) == "g":
            info["children"] = sum(1 for c in element.iter() if c is not element and local_name(c) in GRAPHIC_TAGS)
        return info

    def list_elements(self, tag: str | None = None, max_results: int = 200,
                      drawn_only: bool = False, layer: str | None = None) -> list[dict[str, Any]]:
        """List graphic elements; drawn_only restricts to all canvas-session layers
        (crab_drawn / crab_zones / crab_layer_*); layer restricts to one named layer."""
        with self.lock:
            roots: list[ET.Element] = []
            if layer:
                target = self._layer(self.layer_id(layer), create=False)
                if target is not None:
                    roots.append(target)
            elif drawn_only:
                for element in self.require_root():
                    gid = element.get("id") or ""
                    if local_name(element) == "g" and (
                            gid in (DRAW_LAYER_ID, ZONE_LAYER_ID) or gid.startswith("crab_layer_")):
                        roots.append(element)
            else:
                roots.append(self.require_root())
            results = []
            for root in roots:
                for element in root.iter():
                    name = local_name(element)
                    if name not in GRAPHIC_TAGS or (tag and name != tag):
                        continue
                    if (drawn_only or layer) and element in roots:
                        continue  # skip the layer <g> itself
                    results.append(self.describe(element))
                    if len(results) >= max_results:
                        return results
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
            self._op_force = bool(params.pop("force", False)) if isinstance(params, dict) else False
            try:
                result = handler(**params)
            except TypeError as exc:
                self._undo.pop()
                raise CanvasError(f"bad parameters for {op}: {exc}") from exc
            except CanvasError:
                self._undo.pop()
                raise
            self.revision += 1
            self._autosave()
            return {"op": op, "revision": self.revision, **(result or {})}

    def _autosave(self) -> None:
        """Journal the document to disk at most every few seconds (crash recovery)."""
        import time
        if self.autosave_path is None:
            return
        now = time.monotonic()
        if now - self._autosave_at < 3.0:
            return
        self._autosave_at = now
        try:
            self.autosave_path.parent.mkdir(parents=True, exist_ok=True)
            self.autosave_path.write_text(self.to_string(), encoding="utf-8")
        except OSError:
            pass

    # ---- draft transaction ------------------------------------------------

    def draft_begin(self) -> dict[str, Any]:
        with self.lock:
            if self._draft_base is not None:
                raise CanvasError("a draft is already open — commit or rollback first")
            self._draft_base = self.to_string()
            return {"draft": "open", "revision": self.revision}

    def draft_commit(self) -> dict[str, Any]:
        with self.lock:
            if self._draft_base is None:
                raise CanvasError("no open draft")
            self._draft_base = None
            return {"draft": "committed", "revision": self.revision}

    def draft_rollback(self) -> dict[str, Any]:
        with self.lock:
            if self._draft_base is None:
                raise CanvasError("no open draft")
            self.root = safe_fromstring(self._draft_base)
            self._draft_base = None
            self.revision += 1
            return {"draft": "rolled_back", "revision": self.revision}

    # ---- scale -------------------------------------------------------------

    def mm_per_unit(self) -> float | None:
        raw = self.require_root().get(SCALE_ATTR)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def area_info(self, area_units2: float) -> dict[str, Any]:
        info: dict[str, Any] = {"area_units2": round(area_units2, 1)}
        mm = self.mm_per_unit()
        if mm:
            m2 = area_units2 * (mm / 1000.0) ** 2
            info["area_m2"] = round(m2, 1)
            info["area_pyeong"] = round(m2 / PYEONG_M2, 1)
        return info

    def op_set_scale(self, mm_per_unit: float | None = None,
                     known_mm: float | None = None,
                     p1: list[float] | None = None, p2: list[float] | None = None) -> dict[str, Any]:
        """Set drawing scale directly or by calibrating a known real distance
        between two points on the drawing."""
        if mm_per_unit is None:
            if not (known_mm and p1 and p2):
                raise CanvasError("pass mm_per_unit, or known_mm with p1/p2")
            import math
            dist = math.dist((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))
            if dist < 1e-6:
                raise CanvasError("p1 and p2 are the same point")
            mm_per_unit = float(known_mm) / dist
        self.require_root().set(SCALE_ATTR, f"{float(mm_per_unit):.6f}")
        return {"mm_per_unit": round(float(mm_per_unit), 4),
                "units_per_meter": round(1000.0 / float(mm_per_unit), 4)}

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
        clean = {k: v for k, v in (style or {}).items() if k in STYLE_ATTRS and v is not None}
        if not clean:
            return
        if "style" in clean:
            element.set("style", str(clean.pop("style")))
        if not clean:
            return
        # inline style="" CSS overrides presentation attributes — drop the
        # properties we are about to set so the new values actually take effect
        inline = parse_style_attr(element.get("style"))
        if inline:
            kept = {k: v for k, v in inline.items() if k not in clean}
            if kept:
                element.set("style", ";".join(f"{k}:{v}" for k, v in kept.items()))
            else:
                element.attrib.pop("style", None)
        for key, value in clean.items():
            element.set(key, str(value))

    @staticmethod
    def layer_id(layer: str | None) -> str:
        """Sanitized layer group id. None → default draw layer; names map to
        crab_layer_<name> (분야별: arch/landscape/electrical/mechanical/fire/civil …)."""
        if not layer:
            return DRAW_LAYER_ID
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", str(layer))[:40]
        return f"crab_layer_{clean}"

    def _add_shape(self, tag: str, attrs: dict[str, str], style: dict[str, Any] | None,
                   defaults: dict[str, str], layer: str | None = None) -> dict[str, Any]:
        layer = self._layer(self.layer_id(layer))
        merged = {**defaults, **attrs, "data-cid": self._new_cid()}
        element = ET.SubElement(layer, q(tag), merged)
        self._apply_style(element, style)
        bbox = self.world_bbox(element)
        if bbox:
            try:
                self._check_no_go(bbox, f"draw {tag}")
            except CanvasError:
                layer.remove(element)
                raise
        return {"cid": element.get("data-cid"), "element": self.describe(element)}

    # ---- ops: drawing ----------------------------------------------------

    def op_draw_line(self, x1: float, y1: float, x2: float, y2: float,
                     style: dict[str, Any] | None = None, layer: str | None = None) -> dict[str, Any]:
        return self._add_shape("line", {"x1": fmt(x1), "y1": fmt(y1), "x2": fmt(x2), "y2": fmt(y2)},
                               style, {"stroke": "#111111", "stroke-width": "2"}, layer=layer)

    def op_draw_polyline(self, points: list[list[float]], closed: bool = False,
                         style: dict[str, Any] | None = None, layer: str | None = None) -> dict[str, Any]:
        if len(points) < 2:
            raise CanvasError("polyline needs at least 2 points")
        tag = "polygon" if closed else "polyline"
        return self._add_shape(tag, {"points": points_attr(points)}, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"}, layer=layer)

    def op_draw_path(self, d: str, style: dict[str, Any] | None = None, layer: str | None = None) -> dict[str, Any]:
        if not d.strip() or d.strip()[0].upper() != "M":
            raise CanvasError("path d must start with a moveto (M)")
        return self._add_shape("path", {"d": d.strip()}, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"}, layer=layer)

    def op_draw_curve(self, points: list[list[float]], style: dict[str, Any] | None = None, layer: str | None = None) -> dict[str, Any]:
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
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"}, layer=layer)

    def op_draw_rect(self, x: float, y: float, width: float, height: float,
                     rx: float | None = None, style: dict[str, Any] | None = None,
                     layer: str | None = None) -> dict[str, Any]:
        attrs = {"x": fmt(x), "y": fmt(y), "width": fmt(width), "height": fmt(height)}
        if rx is not None:
            attrs["rx"] = fmt(rx)
        return self._add_shape("rect", attrs, style,
                               {"stroke": "#111111", "stroke-width": "2", "fill": "none"}, layer=layer)

    def op_draw_ellipse(self, cx: float, cy: float, rx: float, ry: float | None = None,
                        style: dict[str, Any] | None = None, layer: str | None = None) -> dict[str, Any]:
        if ry is None or abs(float(ry) - float(rx)) < 1e-9:
            return self._add_shape("circle", {"cx": fmt(cx), "cy": fmt(cy), "r": fmt(rx)}, style,
                                   {"stroke": "#111111", "stroke-width": "2", "fill": "none"}, layer=layer)
        return self._add_shape("ellipse", {"cx": fmt(cx), "cy": fmt(cy), "rx": fmt(rx), "ry": fmt(ry)},
                               style, {"stroke": "#111111", "stroke-width": "2", "fill": "none"}, layer=layer)

    def op_add_text(self, x: float, y: float, text: str,
                    style: dict[str, Any] | None = None, layer: str | None = None) -> dict[str, Any]:
        result = self._add_shape("text", {"x": fmt(x), "y": fmt(y)}, style,
                                 {"fill": "#111111", "font-size": "16"}, layer=layer)
        self.find(result["cid"]).text = str(text)
        return result

    # ---- ops: editing ----------------------------------------------------

    def op_delete_elements(self, cids: list[str]) -> dict[str, Any]:
        index: dict[str, ET.Element] = {}
        for element in self.require_root().iter():
            cid = element.get("data-cid")
            if cid:
                index[cid] = element
        targets: list[tuple[str, ET.Element, ET.Element]] = []
        skipped: list[str] = []
        for cid in cids:
            element = index.get(cid)
            parent = self.parent_of(element) if element is not None else None
            if element is None or parent is None:
                skipped.append(cid)
            else:
                targets.append((cid, element, parent))
        if not targets:
            raise CanvasError(f"no deletable elements among {len(cids)} cid(s)")
        for cid, element, parent in targets:
            parent.remove(element)
        result: dict[str, Any] = {"deleted": [cid for cid, _, _ in targets]}
        if skipped:
            result["skipped"] = skipped
        return result

    def op_move_element(self, cid: str, dx: float, dy: float) -> dict[str, Any]:
        element = self.find(cid)
        bbox = self.world_bbox(element)
        if bbox:
            self._check_no_go([bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy], "move")
        # 좌표에 직접 반영(bake)하면 SVG가 깨끗하게 유지되고 이후 stretch 편집이 가능
        if not element.get("transform"):
            parent = self.parent_of(element)
            ancestor = self._accumulated_transform(parent) if parent is not None else IDENTITY
            inv = invert_matrix(ancestor)
            if inv is not None:
                ldx, ldy = inv[0] * dx + inv[2] * dy, inv[1] * dx + inv[3] * dy
                if self._bake_translate(element, ldx, ldy):
                    return {"cid": cid, "element": self.describe(element), "baked": True}
        existing = element.get("transform", "")
        element.set("transform", f"translate({fmt(dx)},{fmt(dy)}) {existing}".strip())
        return {"cid": cid, "element": self.describe(element)}

    def _bake_translate(self, element: ET.Element, ldx: float, ldy: float) -> bool:
        """Shift a leaf element's own geometry by (ldx, ldy) in its local space."""
        tag = local_name(element)
        try:
            if tag == "line":
                for ax, ay in (("x1", "y1"), ("x2", "y2")):
                    px, py = _attr_num(element, ax), _attr_num(element, ay)
                    element.set(ax, fmt(px + ldx))
                    element.set(ay, fmt(py + ldy))
            elif tag in {"polyline", "polygon"}:
                pts = local_points(element)
                if not pts:
                    return False
                element.set("points", points_attr([[x + ldx, y + ldy] for x, y in pts]))
            elif tag in {"rect", "use", "image"}:
                element.set("x", fmt(_attr_num(element, "x") + ldx))
                element.set("y", fmt(_attr_num(element, "y") + ldy))
            elif tag in {"circle", "ellipse"}:
                element.set("cx", fmt(_attr_num(element, "cx") + ldx))
                element.set("cy", fmt(_attr_num(element, "cy") + ldy))
            elif tag == "text":
                # tspan children with their own x/y would not follow a baked shift
                if any(child.get("x") or child.get("y") for child in element):
                    return False
                element.set("x", fmt(_attr_num(element, "x") + ldx))
                element.set("y", fmt(_attr_num(element, "y") + ldy))
            elif tag == "path":
                new_d, changed = transform_path(element.get("d", ""),
                                                lambda px, py: (px + ldx, py + ldy))
                if not changed:
                    return False
                element.set("d", new_d)
            else:
                return False
            return True
        except ValueError:
            return False

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

    def op_stretch(self, box: list[float], dx: float, dy: float,
                   cids: list[str] | None = None) -> dict[str, Any]:
        """CAD-style stretch: geometry points inside box move by (dx,dy); points
        outside stay — lines/walls crossing the box boundary stretch instead of moving."""
        x0, y0, x1, y1 = (float(v) for v in box)
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        inside = lambda px, py: x0 <= px <= x1 and y0 <= py <= y1  # noqa: E731
        self._check_no_go([x0 + dx, y0 + dy, x1 + dx, y1 + dy], "stretch")
        wanted = set(cids) if cids else None
        changed: list[str] = []
        skipped: list[str] = []
        for element in list(self.require_root().iter()):
            cid = element.get("data-cid")
            tag = local_name(element)
            if not cid or tag not in GRAPHIC_TAGS or tag == "g":
                continue
            if wanted is not None and cid not in wanted:
                continue
            b = self.world_bbox(element)
            if not b or b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1:
                continue
            # local geometry is tested through its world transform; the world
            # delta is mapped back into local space so transformed elements work
            matrix = self._accumulated_transform(element)
            if matrix == IDENTITY:
                inside_pt, ldx, ldy = inside, dx, dy
            else:
                inv = invert_matrix(matrix)
                if inv is None:  # degenerate transform
                    if wanted is not None:
                        skipped.append(cid)
                    continue
                inside_pt = lambda px, py, _m=matrix: inside(*apply_matrix(_m, px, py))  # noqa: E731
                ldx, ldy = inv[0] * dx + inv[2] * dy, inv[1] * dx + inv[3] * dy
            moved = False
            if tag == "line":
                for sx, sy in (("x1", "y1"), ("x2", "y2")):
                    px, py = _attr_num(element, sx), _attr_num(element, sy)
                    if inside_pt(px, py):
                        element.set(sx, fmt(px + ldx)); element.set(sy, fmt(py + ldy)); moved = True
            elif tag in {"polyline", "polygon"}:
                pts = [list(p) for p in (local_points(element) or [])]
                for p in pts:
                    if inside_pt(p[0], p[1]):
                        p[0] += ldx; p[1] += ldy; moved = True
                if moved:
                    element.set("points", points_attr(pts))
            elif tag == "rect":
                rx, ry = _attr_num(element, "x"), _attr_num(element, "y")
                rw, rh = _attr_num(element, "width"), _attr_num(element, "height")
                corners_in = [inside_pt(px, py) for px, py in
                              ((rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh))]
                if all(corners_in):
                    element.set("x", fmt(rx + ldx)); element.set("y", fmt(ry + ldy)); moved = True
                elif corners_in[1] and corners_in[3] and not corners_in[0]:   # right edge
                    element.set("width", fmt(max(1.0, rw + ldx))); moved = True
                elif corners_in[0] and corners_in[2] and not corners_in[1]:   # left edge
                    element.set("x", fmt(rx + ldx)); element.set("width", fmt(max(1.0, rw - ldx))); moved = True
                elif corners_in[2] and corners_in[3] and not corners_in[0]:   # bottom edge
                    element.set("height", fmt(max(1.0, rh + ldy))); moved = True
                elif corners_in[0] and corners_in[1] and not corners_in[2]:   # top edge
                    element.set("y", fmt(ry + ldy)); element.set("height", fmt(max(1.0, rh - ldy))); moved = True
            elif tag == "path":
                new_d, moved = transform_path(
                    element.get("d", ""),
                    lambda px, py: (px + ldx, py + ldy) if inside_pt(px, py) else (px, py))
                if moved:
                    element.set("d", new_d)
            elif tag in {"circle", "ellipse"}:
                pcx, pcy = _attr_num(element, "cx"), _attr_num(element, "cy")
                if inside_pt(pcx, pcy):
                    element.set("cx", fmt(pcx + ldx)); element.set("cy", fmt(pcy + ldy)); moved = True
            elif tag in {"text", "use", "image"}:
                px, py = _attr_num(element, "x"), _attr_num(element, "y")
                if inside_pt(px, py):
                    element.set("x", fmt(px + ldx)); element.set("y", fmt(py + ldy)); moved = True
            else:
                if wanted is not None:
                    skipped.append(cid)
                continue
            if moved:
                changed.append(cid)
        if not changed:
            raise CanvasError("stretch matched no editable geometry in the box")
        result: dict[str, Any] = {"stretched": changed[:200], "stretched_count": len(changed)}
        if skipped:
            result["unsupported"] = skipped[:50]
        return result

    # ---- measurement -------------------------------------------------------

    def measure(self, cids: list[str]) -> list[dict[str, Any]]:
        """World-space length/area of elements (curves flattened, transforms honored)."""
        import math
        mm = self.mm_per_unit()
        results: list[dict[str, Any]] = []
        for cid in cids:
            element = self.find(cid)
            tag = local_name(element)
            matrix = self._accumulated_transform(element)
            polylines: list[tuple[list[tuple[float, float]], bool]] = []  # (points, closed)
            try:
                if tag == "line":
                    polylines = [([(_attr_num(element, "x1"), _attr_num(element, "y1")),
                                   (_attr_num(element, "x2"), _attr_num(element, "y2"))], False)]
                elif tag in {"polyline", "polygon"}:
                    pts = local_points(element) or []
                    polylines = [(list(pts), tag == "polygon")] if len(pts) >= 2 else []
                elif tag == "rect":
                    pts = local_points(element)
                    if pts:
                        (p00, p10, p01, p11) = pts
                        polylines = [([p00, p10, p11, p01], True)]
                elif tag in {"circle", "ellipse"}:
                    ecx, ecy = _attr_num(element, "cx"), _attr_num(element, "cy")
                    rx = _attr_num(element, "r") if tag == "circle" else _attr_num(element, "rx")
                    ry = rx if tag == "circle" else _attr_num(element, "ry")
                    ring = [(ecx + rx * math.cos(2 * math.pi * k / 64),
                             ecy + ry * math.sin(2 * math.pi * k / 64)) for k in range(64)]
                    polylines = [(ring, True)]
                elif tag == "path":
                    polylines = [(sub, len(sub) > 2 and sub[0] == sub[-1])
                                 for sub in flatten_path(element.get("d", ""))]
            except ValueError:
                polylines = []
            info: dict[str, Any] = {"cid": cid, "tag": tag}
            if not polylines:
                info["error"] = "unsupported geometry (text/use/image/empty)"
                results.append(info)
                continue
            length = 0.0
            area = 0.0
            any_closed = False
            for pts, closed in polylines:
                world = [apply_matrix(matrix, x, y) for x, y in pts]
                ring = world + [world[0]] if closed and world[0] != world[-1] else world
                length += sum(math.dist(ring[i], ring[i + 1]) for i in range(len(ring) - 1))
                if closed:
                    any_closed = True
                    area += abs(sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
                                    for i in range(len(ring) - 1))) / 2.0
            info["length_units"] = round(length, 2)
            info["closed"] = any_closed
            if mm:
                info["length_m"] = round(length * mm / 1000.0, 3)
            if any_closed:
                info.update(self.area_info(area))
            results.append(info)
        return results

    # ---- no-go enforcement -------------------------------------------------

    def _no_go_zones(self) -> list[tuple[str, list[list[float]], list[float]]]:
        zones = []
        layer = self._zone_layer(create=False)
        if layer is None:
            return zones
        for poly in layer:
            if poly.get("data-zone-mode") in {"no_go_zone", "lock_boundary"}:
                pts = [[float(v) for v in pair.split(",")] for pair in poly.get("points", "").split()]
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                zones.append((poly.get("data-zone-name") or "no_go",
                              pts, [min(xs), min(ys), max(xs), max(ys)]))
        return zones

    @staticmethod
    def _point_in_polygon(px: float, py: float, pts: list[list[float]]) -> bool:
        hit = False
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]; xj, yj = pts[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
        return hit

    def _check_no_go(self, bbox: list[float], action: str) -> None:
        if getattr(self, "_op_force", False):
            return
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        probes = [(bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[3]), (cx, cy)]
        for name, pts, zb in self._no_go_zones():
            if bbox[2] < zb[0] or bbox[0] > zb[2] or bbox[3] < zb[1] or bbox[1] > zb[3]:
                continue
            if any(self._point_in_polygon(px, py, pts) for px, py in probes):
                raise CanvasError(
                    f"{action} blocked: geometry enters protected zone '{name}' "
                    "(no_go/lock). Pass force=true only if this is intentional.")

    # ---- room recognition ----------------------------------------------------

    def recognize_room(self, x: float, y: float, cell: float = 3.0,
                       max_span: float = 500.0, door_close: float = 12.0) -> dict[str, Any]:
        """Flood-fill from a seed point against wall geometry to find the enclosing
        room. Returns bbox and area (m²/평 when a scale is set)."""
        x, y = float(x), float(y)
        wx0, wy0 = x - max_span, y - max_span
        cols = rows = int((2 * max_span) / cell)
        grid = bytearray(cols * rows)

        def mark(px: float, py: float) -> None:
            ci, ri = int((px - wx0) / cell), int((py - wy0) / cell)
            if 0 <= ci < cols and 0 <= ri < rows:
                grid[ri * cols + ci] = 1

        gray = {"#bababa", "#767676", "#545454", "#989898", "gray", "grey", "white", "none"}
        for element in self.require_root().iter():
            tag = local_name(element)
            if tag not in GRAPHIC_TAGS or tag in {"g", "text", "image", "use"}:
                continue
            if element.get("data-zone-mode"):
                continue  # annotation zones are not walls
            # 벽만 배리어로: 굵은 스트로크(격자·주석의 폭1 선 제외) 또는 면 채움 도형
            stroke = (element.get("stroke") or "").lower()
            try:
                sw = _attr_num(element, "stroke-width", 1.0)
            except ValueError:
                sw = 1.0
            fill = (element.get("fill") or "none").lower()
            solid = tag in {"polygon", "rect", "circle", "ellipse"} and fill not in {"none", "white", "#ffffff"}
            if not solid and (sw < 1.5 or stroke in gray):
                continue
            b = self.world_bbox(element)
            if not b or b[2] < wx0 or b[0] > x + max_span or b[3] < wy0 or b[1] > y + max_span:
                continue
            pts = local_points(element)
            if not pts:
                continue
            matrix = self._accumulated_transform(element)
            pts = [apply_matrix(matrix, px, py) for px, py in pts]
            closed = tag in {"polygon", "rect", "circle", "ellipse"}
            seq = list(pts) + ([pts[0]] if closed and len(pts) > 2 else [])
            for (ax, ay), (bx2, by2) in zip(seq, seq[1:]):
                seg = max(abs(bx2 - ax), abs(by2 - ay))
                steps = max(1, int(seg / (cell / 2)))
                for i in range(steps + 1):
                    t = i / steps
                    mark(ax + (bx2 - ax) * t, ay + (by2 - ay) * t)

        # 문 개구부 봉합: door_close 이하의 틈을 모폴로지 클로징으로 닫는다
        radius = max(1, int(round(door_close / cell / 2)))
        if radius:
            dilated = bytearray(cols * rows)
            for ri in range(rows):
                base = ri * cols
                for ci in range(cols):
                    if grid[base + ci]:
                        for dr in range(-radius, radius + 1):
                            rr = ri + dr
                            if not (0 <= rr < rows):
                                continue
                            row_base = rr * cols
                            for dc in range(-radius, radius + 1):
                                cc = ci + dc
                                if 0 <= cc < cols:
                                    dilated[row_base + cc] = 1
            closed_grid = bytearray(cols * rows)
            for ri in range(rows):
                base = ri * cols
                for ci in range(cols):
                    if not dilated[base + ci]:
                        continue
                    keep = True
                    for dr in range(-radius, radius + 1):
                        rr = ri + dr
                        if not (0 <= rr < rows):
                            continue
                        row_base = rr * cols
                        for dc in range(-radius, radius + 1):
                            cc = ci + dc
                            if 0 <= cc < cols and not dilated[row_base + cc]:
                                keep = False
                                break
                        if not keep:
                            break
                    if keep:
                        closed_grid[base + ci] = 1
            grid = closed_grid

        seed = (int((x - wx0) / cell), int((y - wy0) / cell))
        if grid[seed[1] * cols + seed[0]]:
            raise CanvasError("seed point sits on wall geometry — nudge it into the room")
        from collections import deque
        queue = deque([seed])
        seen = {seed}
        filled: list[tuple[int, int]] = []
        leaked = False
        while queue:
            ci, ri = queue.popleft()
            filled.append((ci, ri))
            for nc, nr in ((ci+1, ri), (ci-1, ri), (ci, ri+1), (ci, ri-1)):
                if not (0 <= nc < cols and 0 <= nr < rows):
                    leaked = True
                    continue
                if (nc, nr) in seen or grid[nr * cols + nc]:
                    continue
                seen.add((nc, nr))
                queue.append((nc, nr))
        area = len(filled) * cell * cell
        xs = [c for c, _ in filled]; ys = [r for _, r in filled]
        bbox = [round(wx0 + min(xs) * cell, 1), round(wy0 + min(ys) * cell, 1),
                round(wx0 + (max(xs) + 1) * cell, 1), round(wy0 + (max(ys) + 1) * cell, 1)]
        return {"seed": [x, y], "enclosed": not leaked, "bbox": bbox,
                "cell": cell, **self.area_info(area),
                **({"note": "영역이 탐색 창 밖으로 새어나감 — 폐합되지 않았거나 max_span을 늘려야 함"} if leaked else {})}

    def recognize_rooms(self, max_rooms: int = 40, **kwargs: Any) -> list[dict[str, Any]]:
        """Recognize the room around every text label (best effort)."""
        results = []
        with self.lock:
            labels = [e for e in self.list_elements(tag="text", max_results=max_rooms * 3)
                      if e.get("text") and e.get("bbox")]
            for label in labels[:max_rooms]:
                lx, ly = label["bbox"][0], label["bbox"][1]
                try:
                    room = self.recognize_room(lx, ly - 4, **kwargs)
                except CanvasError as exc:
                    room = {"error": str(exc)}
                results.append({"label": label["text"], "label_cid": label["cid"], **room})
        return results

    def op_place_symbol(self, name: str, x: float, y: float, rotation: float = 0.0,
                        scale: float = 1.0, layer: str | None = None,
                        label: str | None = None) -> dict[str, Any]:
        """Place a discipline symbol (조경 수목, 전기 조명·콘센트, 기계 디퓨저·밸브,
        소방 스프링클러, 토목 맨홀 등) at (x, y). See canvas_symbols.catalog()."""
        from crab_archi_design.canvas_symbols import build_symbol
        try:
            symbol, discipline = build_symbol(str(name))
        except KeyError:
            from crab_archi_design.canvas_symbols import SYMBOLS
            raise CanvasError(f"unknown symbol {name!r}; available: {', '.join(sorted(SYMBOLS))}") from None
        parent = self._layer(self.layer_id(layer or discipline))
        transform = f"translate({fmt(x)},{fmt(y)})"
        if rotation:
            transform += f" rotate({fmt(rotation)})"
        if scale and abs(float(scale) - 1.0) > 1e-9:
            transform += f" scale({fmt(scale)})"
        symbol.set("transform", transform)
        symbol.set("data-cid", self._new_cid())
        symbol.set("data-symbol", str(name))
        parent.append(symbol)
        bbox = self.world_bbox(symbol)
        if bbox:
            try:
                self._check_no_go(bbox, f"place {name}")
            except CanvasError:
                parent.remove(symbol)
                raise
        result = {"cid": symbol.get("data-cid"), "symbol": name,
                  "discipline": discipline, "layer": parent.get("id")}
        if label:
            text = ET.SubElement(parent, q("text"),
                                 {"x": fmt(x + 8 * float(scale)), "y": fmt(y + 3),
                                  "data-cid": self._new_cid(), "fill": "#111111",
                                  "font-size": fmt(6 * float(scale))})
            text.text = str(label)
            result["label_cid"] = text.get("data-cid")
        return result

    def list_layers(self) -> list[dict[str, Any]]:
        layers = []
        for element in self.require_root():
            gid = element.get("id") or ""
            if local_name(element) == "g" and (gid.startswith("crab_layer_") or gid in (DRAW_LAYER_ID, ZONE_LAYER_ID)):
                count = sum(1 for c in element.iter()
                            if c is not element and local_name(c) in GRAPHIC_TAGS)
                layers.append({"id": gid, "elements": count})
        return layers

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
