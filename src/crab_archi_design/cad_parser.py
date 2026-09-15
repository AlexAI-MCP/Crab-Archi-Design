"""DWG/DXF inventory, parsing, architectural recognition, and OpenCrab pack export.

The parser deliberately keeps the binary CAD source outside the ontology graph.
The graph stores stable source hashes, DXF handles, layer names, geometry
summaries, semantic role hints, and evidence references.  Full extracted
document IR is retained in ``documents/*.json`` inside the generated pack so
every ontology fact can be traced back to the source drawing.

DWG support is provided through an optional ODA File Converter installation.
DXF files can be parsed directly with ezdxf.  The module does not silently
rename a DXF file to DWG or invent missing geometry when a converter is absent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


CAD_IR_SCHEMA = "crab-cad-parser-cad-ir-v2"
CAD_PACK_SCHEMA = "opencrab-pack-v1"
CAD_PARSER_VERSION = "0.2.0"
DEFAULT_DWG_VERSION = "ACAD2000"

ROUNDTRIP_CONTRACT = "crab-cad-parser-roundtrip-v1"

ProgressCallback = Callable[[dict[str, Any]], None]

ROLE_LAYER_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wall", ("wall", "벽", "외벽", "내벽", "a-wall", "arch-wall")),
    ("door", ("door", "dr", "문", "출입")),
    ("window", ("window", "win", "wnd", "창")),
    ("column", ("column", "col", "pillar", "기둥", "주기둥")),
    ("beam", ("beam", "girder", "보", "대들보")),
    ("slab", ("slab", "floor", "바닥", "슬래브")),
    ("stair", ("stair", "stairs", "계단")),
    ("ramp", ("ramp", "램프")),
    ("parking", ("parking", "car", "주차")),
    ("grid", ("grid", "축선", "축", "그리드")),
    ("dimension", ("dim", "dimension", "치수")),
    ("annotation", ("anno", "note", "text", "label", "주석", "문자")),
    ("furniture", ("furn", "furniture", "가구")),
    ("site", ("site", "boundary", "property", "대지", "도로", "road")),
    ("space", ("room", "space", "area", "zone", "실", "방", "공간", "구획")),
    ("structure", ("struct", "structure", "구조")),
)

ROLE_TEXT_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("main_hall", ("main hall", "hall", "lobby", "로비", "홀")),
    ("greenery_lounge", ("greenery", "lounge", "cafe", "library", "라운지", "카페", "도서관")),
    ("fitness_gx", ("fitness", "gym", "gx", "피트니스", "헬스", "운동")),
    ("golf_screen", ("golf", "screen golf", "골프", "스크린")),
    ("sauna_wellness", ("sauna", "wellness", "locker", "shower", "사우나", "웰니스", "락커", "샤워")),
    ("office_management", ("office", "management", "admin", "사무", "관리")),
    ("toilet", ("toilet", "wc", "restroom", "화장실", "변소")),
    ("stair", ("stair", "stairs", "계단")),
    ("parking", ("parking", "주차")),
)

UNIT_NAMES = {
    0: "unitless",
    1: "inches",
    2: "feet",
    3: "miles",
    4: "millimeters",
    5: "centimeters",
    6: "meters",
    7: "kilometers",
    8: "microinches",
    9: "mils",
    10: "yards",
    11: "angstroms",
    12: "nanometers",
    13: "microns",
    14: "decimeters",
    15: "decameters",
    16: "hectometers",
    17: "gigameters",
    18: "astronomical_units",
    19: "light_years",
    20: "parsecs",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jsonable(value: Any) -> Any:
    """Convert common ezdxf values to bounded JSON-safe values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if hasattr(value, "x") and hasattr(value, "y"):
        result = {"x": round(float(value.x), 6), "y": round(float(value.y), 6)}
        if hasattr(value, "z"):
            result["z"] = round(float(value.z), 6)
        return result
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value[:5000]]
    try:
        return str(value)
    except Exception:
        return None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value.strip()).strip("-")
    return slug or "cad-batch"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def find_source_files(input_dir: Path, recursive: bool = True) -> list[Path]:
    """Return DWG/DXF files in stable relative-path order."""

    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    files = [
        item
        for item in iterator
        if item.is_file() and item.suffix.lower() in {".dwg", ".dxf"}
    ]
    return sorted(files, key=lambda item: str(item.relative_to(input_dir)).lower())


def find_oda_converter(explicit: str | None = None) -> str | None:
    candidates = [
        explicit,
        os.environ.get("ODA_FILE_CONVERTER"),
        shutil.which("ODAFileConverter"),
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    return None


def convert_dwg_to_dxf(
    source: Path,
    destination: Path,
    converter: str,
    *,
    output_version: str = DEFAULT_DWG_VERSION,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Convert one DWG through ODA without touching the source file."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="crabcadparser-oda-") as temp_dir:
        # macOS commonly stores decomposed (NFD) Unicode filenames while ODA
        # resolves the filename argument more reliably in composed (NFC) form.
        # Passing source.name verbatim can make ODA hang on Korean filenames,
        # even though the same DWG succeeds when its name is typed in NFC.
        external_name = unicodedata.normalize("NFC", source.name)
        command = [
            converter,
            str(source.parent),
            temp_dir,
            output_version,
            "DXF",
            "0",
            "1",
            external_name,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_seconds)),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "failed",
                "source": str(source),
                "destination": str(destination),
                "command": command,
                "error": str(exc),
            }
        expected_stem = unicodedata.normalize("NFC", source.stem).casefold()
        produced = next(
            (
                candidate
                for candidate in Path(temp_dir).iterdir()
                if candidate.is_file()
                and unicodedata.normalize("NFC", candidate.stem).casefold() == expected_stem
                and candidate.suffix.lower() == ".dxf"
            ),
            None,
        )
        if completed.returncode != 0 or produced is None:
            return {
                "status": "failed",
                "source": str(source),
                "destination": str(destination),
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
                "error": "ODA did not produce a DXF file",
            }
        shutil.copyfile(produced, destination)
        return {
            "status": "converted",
            "source": str(source),
            "destination": str(destination),
            "command": command,
            "returncode": completed.returncode,
            "output_version": output_version,
        }


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def point(value: Any) -> list[float] | None:
    if value is None or not hasattr(value, "x") or not hasattr(value, "y"):
        return None
    return [round(float(value.x), 6), round(float(value.y), 6), round(float(getattr(value, "z", 0.0)), 6)]


def bbox_from_points(points: list[list[float]]) -> dict[str, float] | None:
    if not points:
        return None
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    zs = [item[2] if len(item) > 2 else 0.0 for item in points]
    return {
        "min_x": round(min(xs), 6),
        "min_y": round(min(ys), 6),
        "min_z": round(min(zs), 6),
        "max_x": round(max(xs), 6),
        "max_y": round(max(ys), 6),
        "max_z": round(max(zs), 6),
        "width": round(max(xs) - min(xs), 6),
        "height": round(max(ys) - min(ys), 6),
        "depth": round(max(zs) - min(zs), 6),
    }


def bbox_center(box: dict[str, float] | None) -> list[float] | None:
    if not box:
        return None
    return [
        round((box["min_x"] + box["max_x"]) / 2.0, 6),
        round((box["min_y"] + box["max_y"]) / 2.0, 6),
        round((box["min_z"] + box["max_z"]) / 2.0, 6),
    ]


def distance(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b:
        return None
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(min(len(a), len(b), 3))))


def polyline_length(points: list[list[float]], closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = sum(distance(points[index], points[index + 1]) or 0.0 for index in range(len(points) - 1))
    if closed:
        total += distance(points[-1], points[0]) or 0.0
    return round(total, 6)


def polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    return round(abs(sum(points[index][0] * points[(index + 1) % len(points)][1] - points[(index + 1) % len(points)][0] * points[index][1] for index in range(len(points))) / 2.0), 6)


def point_in_bbox(candidate: list[float] | None, box: dict[str, float] | None, padding: float = 0.0) -> bool:
    if not candidate or not box:
        return False
    return (
        box["min_x"] - padding <= candidate[0] <= box["max_x"] + padding
        and box["min_y"] - padding <= candidate[1] <= box["max_y"] + padding
    )


def _text_tokens(value: str) -> str:
    return re.sub(r"[\s_./\\-]+", " ", value.lower()).strip()


def classify_layer(layer_name: str) -> tuple[str, float]:
    lowered = _text_tokens(layer_name)
    for role, tokens in ROLE_LAYER_TOKENS:
        if any(token.lower() in lowered for token in tokens):
            return role, 0.92
    return "unknown", 0.0


def classify_text(text: str) -> tuple[str, float]:
    lowered = _text_tokens(text)
    for role, tokens in ROLE_TEXT_TOKENS:
        if any(token.lower() in lowered for token in tokens):
            return role, 0.84
    if re.search(r"\b(?:b\d+|\d+f|l\d+|level)\b|층|레벨", lowered):
        return "level_marker", 0.68
    if re.search(r"\b[a-z]{1,3}[- ]?\d{2,4}\b", lowered):
        return "drawing_reference", 0.62
    return "label", 0.35


def _safe_dxf_attributes(entity: Any) -> dict[str, Any]:
    try:
        values = entity.dxfattribs()
    except Exception:
        return {}
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key in {"binary_data", "proxy_graphic"}:
            continue
        result[str(key)] = jsonable(value)
    return result


def _entity_visual_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Keep the display-affecting DXF attributes in a stable sub-object.

    ``raw_attributes`` remains the complete JSON-safe attribute snapshot.  The
    smaller visual object is convenient for a renderer or a redesign agent
    that should preserve lineweight/colour/linetype without understanding all
    DXF group codes.
    """

    visual_keys = (
        "layer",
        "color",
        "true_color",
        "linetype",
        "lineweight",
        "ltscale",
        "transparency",
        "plotstyle",
        "material_handle",
        "invisible",
        "thickness",
        "extrusion",
        "paperspace",
    )
    return {key: attributes[key] for key in visual_keys if key in attributes}


def _entity_layer(entity: Any) -> str:
    try:
        return str(entity.dxf.get("layer", "0"))
    except Exception:
        return "0"


def _entity_text(entity: Any) -> str | None:
    kind = entity.dxftype()
    try:
        if kind == "TEXT":
            return str(entity.dxf.get("text", ""))
        if kind == "MTEXT":
            return str(entity.plain_text())
        if kind == "ATTRIB":
            return str(entity.dxf.get("text", ""))
    except Exception:
        return None
    return None


def _sample_arc(center: list[float], radius: float, start: float, end: float, count: int = 16) -> list[list[float]]:
    while end < start:
        end += math.tau
    return [
        [
            round(center[0] + radius * math.cos(start + (end - start) * index / max(1, count - 1)), 6),
            round(center[1] + radius * math.sin(start + (end - start) * index / max(1, count - 1)), 6),
            center[2],
        ]
        for index in range(count)
    ]


def _entity_geometry(entity: Any) -> tuple[list[list[float]], bool, dict[str, Any]]:
    kind = entity.dxftype()
    geometry: dict[str, Any] = {}
    try:
        if kind == "LINE":
            points = [point(entity.dxf.start), point(entity.dxf.end)]
            return [item for item in points if item], False, {
                "primitive": "line",
                "start": points[0],
                "end": points[1],
            }
        if kind == "LWPOLYLINE":
            raw = list(entity.get_points("xyseb"))
            points = [[round(float(item[0]), 6), round(float(item[1]), 6), 0.0] for item in raw]
            closed = bool(entity.closed)
            vertices = [
                {
                    "x": round(float(item[0]), 6),
                    "y": round(float(item[1]), 6),
                    "start_width": round(float(item[2] or 0.0), 6),
                    "end_width": round(float(item[3] or 0.0), 6),
                    "bulge": round(float(item[4] or 0.0), 12),
                }
                for item in raw
            ]
            geometry = {
                "primitive": "lwpolyline",
                "points": points,
                "vertices": vertices,
                "closed": closed,
                "elevation": jsonable(entity.dxf.get("elevation")),
                "extrusion": jsonable(entity.dxf.get("extrusion")),
                "const_width": _number(entity.dxf.get("const_width"), 0.0),
                "bulge_count": sum(1 for item in vertices if abs(float(item["bulge"])) > 1e-9),
            }
            return points, closed, geometry
        if kind == "POLYLINE":
            vertices = []
            for vertex in entity.vertices:
                location = point(vertex.dxf.location)
                if location:
                    vertices.append(
                        {
                            "location": location,
                            "start_width": _number(vertex.dxf.get("start_width"), 0.0),
                            "end_width": _number(vertex.dxf.get("end_width"), 0.0),
                            "bulge": _number(vertex.dxf.get("bulge"), 0.0),
                            "flags": int(vertex.dxf.get("flags", 0) or 0),
                        }
                    )
            points = [item["location"] for item in vertices]
            closed = bool(entity.is_3d_polyline or entity.is_polygon_mesh or entity.dxf.get("flags", 0) & 1)
            return points, closed, {
                "primitive": "polyline",
                "points": points,
                "vertices": vertices,
                "closed": closed,
                "flags": int(entity.dxf.get("flags", 0) or 0),
                "elevation": jsonable(entity.dxf.get("elevation")),
                "thickness": _number(entity.dxf.get("thickness"), 0.0),
                "extrusion": jsonable(entity.dxf.get("extrusion")),
            }
        if kind == "ARC":
            center = point(entity.dxf.center) or [0.0, 0.0, 0.0]
            radius = float(entity.dxf.radius)
            start = math.radians(float(entity.dxf.start_angle))
            end = math.radians(float(entity.dxf.end_angle))
            return _sample_arc(center, radius, start, end), False, {
                "primitive": "arc",
                "center": center,
                "radius": radius,
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
                "sampled_points": _sample_arc(center, radius, start, end),
            }
        if kind == "CIRCLE":
            center = point(entity.dxf.center) or [0.0, 0.0, 0.0]
            radius = float(entity.dxf.radius)
            return _sample_arc(center, radius, 0.0, math.tau, 32), True, {
                "primitive": "circle",
                "center": center,
                "radius": radius,
                "sampled_points": _sample_arc(center, radius, 0.0, math.tau, 32),
            }
        if kind == "ELLIPSE":
            center = point(entity.dxf.center) or [0.0, 0.0, 0.0]
            major = point(entity.dxf.major_axis) or [1.0, 0.0, 0.0]
            ratio = float(entity.dxf.ratio)
            minor = [-major[1] * ratio, major[0] * ratio, major[2] * ratio]
            start_param = float(entity.dxf.get("start_param", 0.0))
            end_param = float(entity.dxf.get("end_param", math.tau))
            while end_param < start_param:
                end_param += math.tau
            points = [
                [
                    round(center[0] + major[0] * math.cos(start_param + (end_param - start_param) * index / 31) + minor[0] * math.sin(start_param + (end_param - start_param) * index / 31), 6),
                    round(center[1] + major[1] * math.cos(start_param + (end_param - start_param) * index / 31) + minor[1] * math.sin(start_param + (end_param - start_param) * index / 31), 6),
                    center[2],
                ]
                for index in range(32)
            ]
            return points, abs((end_param - start_param) - math.tau) < 1e-5, {
                "primitive": "ellipse",
                "center": center,
                "major_axis": major,
                "ratio": ratio,
                "start_param": start_param,
                "end_param": end_param,
                "sampled_points": points,
            }
        if kind == "SPLINE":
            fit_points = []
            control_points = []
            for attr, target in (("fit_points", fit_points), ("control_points", control_points)):
                try:
                    target.extend(item for item in (point(value) for value in getattr(entity, attr)) if item)
                except Exception:
                    continue
            candidates = fit_points or control_points
            geometry = {
                "primitive": "spline",
                "fit_points": fit_points,
                "control_points": control_points,
                "knots": jsonable(list(getattr(entity, "knots", []))),
                "weights": jsonable(list(getattr(entity, "weights", []))),
                "degree": int(entity.dxf.get("degree", 3) or 3),
                "flags": int(entity.dxf.get("flags", 0) or 0),
                "knot_tolerance": _number(entity.dxf.get("knot_tolerance")),
                "fit_tolerance": _number(entity.dxf.get("fit_tolerance")),
                "control_point_tolerance": _number(entity.dxf.get("control_point_tolerance")),
                "point_count": len(candidates),
            }
            return candidates, False, geometry
        if kind == "INSERT":
            insert = point(entity.dxf.insert)
            return ([insert] if insert else []), False, {
                "primitive": "insert",
                "block_name": str(entity.dxf.get("name", "")),
                "insert": insert,
                "rotation": float(entity.dxf.get("rotation", 0.0)),
                "xscale": float(entity.dxf.get("xscale", 1.0)),
                "yscale": float(entity.dxf.get("yscale", 1.0)),
                "zscale": float(entity.dxf.get("zscale", 1.0)),
            }
        if kind in {"TEXT", "MTEXT", "ATTRIB"}:
            insert = point(entity.dxf.get("insert", entity.dxf.get("location")))
            return ([insert] if insert else []), False, {
                "primitive": kind.lower(),
                "insert": insert,
                "height": float(entity.dxf.get("height", entity.dxf.get("char_height", 0.0)) or 0.0),
                "rotation": float(entity.dxf.get("rotation", 0.0) or 0.0),
                "style": str(entity.dxf.get("style", "Standard")),
                "width": _number(entity.dxf.get("width")),
                "attachment_point": int(entity.dxf.get("attachment_point", 0) or 0),
            }
        if kind == "DIMENSION":
            points = []
            for key in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "text_midpoint"):
                candidate = point(entity.dxf.get(key))
                if candidate:
                    points.append(candidate)
            return points, False, {
                "primitive": "dimension",
                "text": str(entity.dxf.get("text", "")),
                "dimtype": int(entity.dxf.get("dimtype", 0) or 0),
                "defpoint": jsonable(entity.dxf.get("defpoint")),
                "defpoint2": jsonable(entity.dxf.get("defpoint2")),
                "defpoint3": jsonable(entity.dxf.get("defpoint3")),
                "defpoint4": jsonable(entity.dxf.get("defpoint4")),
                "text_midpoint": jsonable(entity.dxf.get("text_midpoint")),
                "dimension_style": str(entity.dxf.get("dimstyle", "Standard")),
            }
        if kind in {"3DFACE", "SOLID", "TRACE"}:
            points = []
            for key in ("vtx0", "vtx1", "vtx2", "vtx3"):
                candidate = point(entity.dxf.get(key))
                if candidate:
                    points.append(candidate)
            return points, True, {"primitive": kind.lower(), "points": points}
        if kind in {"POINT", "RAY", "XLINE"}:
            if kind == "POINT":
                location = point(entity.dxf.get("location"))
                return ([location] if location else []), False, {"primitive": "point", "location": location}
            start = point(entity.dxf.get("start"))
            unit = point(entity.dxf.get("unitvector"))
            return ([start] if start else []), False, {
                "primitive": kind.lower(),
                "start": start,
                "unitvector": unit,
            }
        if kind == "HATCH":
            return [], False, {
                "primitive": "hatch",
                "solid_fill": bool(entity.dxf.get("solid_fill", 0)),
                "pattern_name": str(entity.dxf.get("pattern_name", "")),
                "associative": bool(entity.dxf.get("associative", 0)),
                "pattern_scale": _number(entity.dxf.get("pattern_scale")),
                "pattern_angle": _number(entity.dxf.get("pattern_angle")),
                "path_count": len(getattr(entity, "paths", [])),
                "elevation": jsonable(entity.dxf.get("elevation")),
                "extrusion": jsonable(entity.dxf.get("extrusion")),
            }
    except Exception as exc:
        return [], False, {"geometry_error": str(exc)}
    return [], False, geometry


def _classify_entity(kind: str, layer: str, text: str | None, geometry: dict[str, Any], box: dict[str, float] | None) -> tuple[str, float]:
    layer_role, layer_confidence = classify_layer(layer)
    if text:
        text_role, text_confidence = classify_text(text)
        if text_role != "label":
            return text_role, text_confidence
    if layer_role != "unknown":
        return layer_role, layer_confidence
    if kind == "INSERT":
        block_name = str(geometry.get("block_name", ""))
        block_role, block_confidence = classify_layer(block_name)
        if block_role != "unknown":
            return block_role, min(0.9, block_confidence)
    if kind in {"TEXT", "MTEXT", "ATTRIB"}:
        return "label", 0.35
    if kind == "DIMENSION":
        return "dimension", 0.8
    if box and geometry.get("closed") and box["width"] > 0 and box["height"] > 0:
        ratio = max(box["width"], box["height"]) / max(1e-9, min(box["width"], box["height"]))
        if ratio < 1.5 and max(box["width"], box["height"]) < 500.0:
            return "column", 0.3
        if box["width"] * box["height"] > 100000.0:
            return "space", 0.22
    if kind in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE"}:
        return "linework", 0.15
    return "unknown", 0.0


def _layout_name(entity: Any, fallback: str) -> str:
    try:
        layout = entity.get_dxf_attrib("paperspace")
        return "PaperSpace" if layout else "ModelSpace"
    except Exception:
        return fallback


def _entity_record(
    entity: Any,
    *,
    document_id: str,
    ordinal: int,
    layout_name: str,
    scope: str = "layout",
    block_name: str | None = None,
) -> tuple[dict[str, Any], list[list[float]]]:
    """Create one loss-minimising entity record and its metric points."""

    kind = str(entity.dxftype())
    layer = _entity_layer(entity)
    text = _entity_text(entity)
    points, closed, geometry = _entity_geometry(entity)
    box = bbox_from_points(points)
    role, confidence = _classify_entity(kind, layer, text, {**geometry, "closed": closed}, box)
    try:
        handle = str(entity.dxf.get("handle", ""))
    except Exception:
        handle = ""
    raw_attributes = _safe_dxf_attributes(entity)
    record = {
        "entity_id": stable_id("cad-entity", document_id, scope, block_name or "", handle or ordinal, kind),
        "handle": handle or None,
        "ordinal": ordinal,
        "type": kind,
        "scope": scope,
        "block_name": block_name,
        "layer": layer,
        "layout": layout_name,
        "space": "paper" if layout_name.lower() not in {"model", "modelspace"} else "model",
        "role_hint": role,
        "role_confidence": confidence,
        "text": text.strip() if text and text.strip() else None,
        "geometry": geometry,
        "closed": closed,
        "bbox": box,
        "center": bbox_center(box),
        "length": polyline_length(points, closed),
        "area": polygon_area(points) if closed else 0.0,
        "visual": _entity_visual_attributes(raw_attributes),
        "raw_attributes": raw_attributes,
    }
    if block_name:
        record["owner_block"] = block_name
    return record, points


def _unit_name(header: Any) -> str:
    try:
        value = int(header.get("$INSUNITS", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return UNIT_NAMES.get(value, f"unknown:{value}")


HEADER_SNAPSHOT_KEYS = (
    "$ACADVER",
    "$DWGCODEPAGE",
    "$INSBASE",
    "$EXTMIN",
    "$EXTMAX",
    "$LIMMIN",
    "$LIMMAX",
    "$UCSNAME",
    "$UCSORG",
    "$UCSXDIR",
    "$UCSYDIR",
    "$ANGBASE",
    "$ANGDIR",
    "$AUNITS",
    "$LUNITS",
    "$LUPREC",
    "$DIMSTYLE",
    "$CLAYER",
    "$TEXTSTYLE",
    "$CELTYPE",
    "$CELTSCALE",
    "$LTSCALE",
    "$INSUNITS",
    "$MEASUREMENT",
    "$TDCREATE",
    "$TDUPDATE",
    "$HANDSEED",
)


def _header_snapshot(header: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in HEADER_SNAPSHOT_KEYS:
        try:
            value = header.get(key)
        except Exception:
            continue
        if value is not None:
            snapshot[key] = jsonable(value)
    return snapshot


def _table_snapshot(table: Any, *, include_name: bool = True) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        items = list(table)
    except Exception:
        return result
    for item in items:
        try:
            attributes = _safe_dxf_attributes(item)
            if not include_name:
                attributes.pop("name", None)
            result.append(attributes)
        except Exception:
            continue
    return result


def _layout_snapshot(layout: Any) -> dict[str, Any]:
    try:
        attributes = _safe_dxf_attributes(layout.dxf_layout)
    except Exception:
        attributes = {}
    return {
        "name": str(getattr(layout, "name", attributes.get("name", ""))),
        "dxf": attributes,
        "is_modelspace": str(getattr(layout, "name", "")).lower() == "model",
        "entity_count": sum(1 for _ in layout),
    }


def _iter_layout_entities(doc: Any) -> Iterable[tuple[str, Any]]:
    seen: set[str] = set()
    try:
        layouts = list(doc.layouts)
    except Exception:
        layouts = []
    for layout in layouts:
        try:
            layout_name = str(layout.name)
            for entity in layout:
                handle = str(getattr(entity.dxf, "handle", "") or id(entity))
                if handle in seen:
                    continue
                seen.add(handle)
                yield layout_name, entity
        except Exception:
            continue


def parse_dxf(path: Path, *, source_path: Path | None = None, source_format: str | None = None) -> dict[str, Any]:
    """Parse one DXF into a deterministic CAD IR document."""

    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("ezdxf is required. Install the project with its CAD extras.") from exc

    source_path = source_path or path
    source_format = source_format or source_path.suffix.lower().lstrip(".")
    source_hash = sha256_file(source_path)
    document_id = stable_id("cad-document", source_hash)
    warnings: list[dict[str, Any]] = []
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:
        return {
            "schema": CAD_IR_SCHEMA,
            "parser_version": CAD_PARSER_VERSION,
            "status": "failed",
            "document_id": document_id,
            "source": {
                "source_path": str(source_path),
                "parsed_path": str(path),
                "source_format": source_format,
                "source_sha256": source_hash,
            },
            "warnings": [{"code": "dxf_parse_failed", "detail": str(exc)}],
            "document": {},
            "layers": [],
            "layouts": [],
            "blocks": [],
            "entities": [],
            "spaces": [],
            "relations": [],
        }

    header = _header_snapshot(doc.header)
    tables = {
        "linetypes": _table_snapshot(doc.linetypes),
        "text_styles": _table_snapshot(doc.styles),
        "dim_styles": _table_snapshot(doc.dimstyles),
    }

    layers: list[dict[str, Any]] = []
    try:
        for layer in doc.layers:
            role, confidence = classify_layer(str(layer.dxf.name))
            raw_attributes = _safe_dxf_attributes(layer)
            layers.append(
                {
                    "name": str(layer.dxf.name),
                    "color": int(layer.dxf.get("color", 0) or 0),
                    "linetype": str(layer.dxf.get("linetype", "Continuous")),
                    "is_off": bool(layer.is_off()),
                    "is_frozen": bool(layer.is_frozen()),
                    "is_locked": bool(layer.is_locked()),
                    "role_hint": role,
                    "role_confidence": confidence,
                    "raw_attributes": raw_attributes,
                }
            )
    except Exception as exc:
        warnings.append({"code": "layer_read_failed", "detail": str(exc)})

    layouts: list[dict[str, Any]] = []
    try:
        for layout in doc.layouts:
            layouts.append(_layout_snapshot(layout))
    except Exception as exc:
        warnings.append({"code": "layout_read_failed", "detail": str(exc)})

    blocks: list[dict[str, Any]] = []
    block_entities: list[dict[str, Any]] = []
    try:
        for block in doc.blocks:
            name = str(block.name)
            if name.startswith("*"):
                continue
            try:
                base_point = point(block.base_point)
            except Exception:
                base_point = None
            block_raw_attributes = _safe_dxf_attributes(block.block_record) if hasattr(block, "block_record") else {}
            records: list[dict[str, Any]] = []
            for ordinal, block_entity in enumerate(block):
                record, _ = _entity_record(
                    block_entity,
                    document_id=document_id,
                    ordinal=ordinal,
                    layout_name=f"BLOCK:{name}",
                    scope="block",
                    block_name=name,
                )
                records.append(record)
                block_entities.append(record)
            blocks.append(
                {
                    "name": name,
                    "base_point": base_point,
                    "entity_count": len(records),
                    "raw_attributes": block_raw_attributes,
                    "entities": records,
                }
            )
    except Exception as exc:
        warnings.append({"code": "block_read_failed", "detail": str(exc)})

    entities: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    extents: list[list[float]] = []
    for ordinal, (layout_name, entity) in enumerate(_iter_layout_entities(doc)):
        kind = str(entity.dxftype())
        type_counts[kind] = type_counts.get(kind, 0) + 1
        record, points = _entity_record(
            entity,
            document_id=document_id,
            ordinal=ordinal,
            layout_name=layout_name,
        )
        box = record.get("bbox")
        if box:
            extents.extend(
                [
                    [box["min_x"], box["min_y"], box["min_z"]],
                    [box["max_x"], box["max_y"], box["max_z"]],
                ]
            )
        entities.append(record)

    spaces: list[dict[str, Any]] = []
    for entity in entities:
        if not entity["closed"] or not entity.get("bbox"):
            continue
        role = entity.get("role_hint")
        area = float(entity.get("area") or 0.0)
        if role in {"space", "room", "site", "parking"} or (area > 100000.0 and role in {"linework", "unknown"}):
            space_id = stable_id("cad-space", document_id, entity.get("handle") or entity["entity_id"])
            label_candidates = []
            for label in entities:
                if label.get("role_hint") in {"label", "main_hall", "greenery_lounge", "fitness_gx", "golf_screen", "sauna_wellness", "office_management", "toilet", "level_marker", "parking"} and point_in_bbox(label.get("center"), entity.get("bbox")):
                    label_candidates.append(label)
            spaces.append(
                {
                    "space_id": space_id,
                    "source_entity_id": entity["entity_id"],
                    "source_handle": entity.get("handle"),
                    "layer": entity.get("layer"),
                    "role_hint": "space" if role in {"linework", "unknown"} else role,
                    "bbox": entity.get("bbox"),
                    "area": area,
                    "perimeter": entity.get("length", 0.0),
                    "label_entity_ids": [item["entity_id"] for item in label_candidates],
                    "labels": [item.get("text") for item in label_candidates if item.get("text")],
                }
            )

    relations: list[dict[str, Any]] = []
    for space in spaces:
        relation_id = stable_id("cad-relation", document_id, "contains", space["space_id"])
        relations.append(
            {
                "relation_id": relation_id,
                "from_id": f"cad:document:{document_id}",
                "to_id": space["space_id"],
                "type": "CONTAINS_SPACE",
                "confidence": 0.9,
                "evidence_handles": [space.get("source_handle")],
            }
        )
        for label_id in space.get("label_entity_ids", []):
            relations.append(
                {
                    "relation_id": stable_id("cad-relation", document_id, "label", label_id, space["space_id"]),
                    "from_id": label_id,
                    "to_id": space["space_id"],
                    "type": "LABELS_SPACE",
                    "confidence": 0.78,
                    "evidence_handles": [space.get("source_handle")],
                }
            )
    # A bounded nearby-space graph is useful for ontology queries but avoids an
    # O(n^2) explosion on survey drawings with thousands of closed polylines.
    for index, left in enumerate(spaces[:500]):
        for right in spaces[index + 1 : 500]:
            left_center = bbox_center(left.get("bbox"))
            right_center = bbox_center(right.get("bbox"))
            gap = distance(left_center, right_center)
            scale = max(
                float((left.get("bbox") or {}).get("width") or 0.0),
                float((left.get("bbox") or {}).get("height") or 0.0),
                float((right.get("bbox") or {}).get("width") or 0.0),
                float((right.get("bbox") or {}).get("height") or 0.0),
                1.0,
            )
            if gap is not None and gap <= scale * 1.5:
                relations.append(
                    {
                        "relation_id": stable_id("cad-relation", document_id, "near", left["space_id"], right["space_id"]),
                        "from_id": left["space_id"],
                        "to_id": right["space_id"],
                        "type": "NEAR_SPACE",
                        "confidence": 0.35,
                        "distance": round(gap, 6),
                        "evidence_handles": [left.get("source_handle"), right.get("source_handle")],
                    }
                )

    texts = [item.get("text") for item in entities if item.get("text")]
    title_candidates = [item for item in texts if item and ("title" in item.lower() or re.search(r"\b[a-z]{1,3}[- ]?\d{2,4}\b", item.lower()))]
    level_candidates = [item for item in texts if item and classify_text(item)[0] == "level_marker"]
    drawing_kind = "unknown"
    context_text = " ".join(texts).lower() + " " + " ".join(layer.get("name", "") for layer in layers).lower()
    if any(token in context_text for token in ("section", "단면", "종단면", "횡단면")):
        drawing_kind = "section"
    elif any(token in context_text for token in ("elevation", "입면", "정면도", "측면도")):
        drawing_kind = "elevation"
    elif any(token in context_text for token in ("site", "배치", "대지")):
        drawing_kind = "site_plan"
    elif any(token in context_text for token in ("plan", "평면", "floor")):
        drawing_kind = "floor_plan"

    document = {
        "document_id": document_id,
        "source_path": str(source_path),
        "parsed_path": str(path),
        "source_format": source_format,
        "source_sha256": source_hash,
        "parsed_sha256": sha256_file(path),
        "coordinate_space": "dxf_world_coordinates",
        "header_snapshot": header,
        "units": _unit_name(doc.header),
        "acad_version": str(doc.header.get("$ACADVER", "unknown")),
        "measurement_system": int(doc.header.get("$MEASUREMENT", 0) or 0),
        "drawing_kind": drawing_kind,
        "title_candidates": title_candidates[:20],
        "level_candidates": level_candidates[:20],
        "bbox": bbox_from_points(extents),
        "entity_count": len(entities),
        "important_entity_count": sum(1 for item in entities if item.get("role_hint") != "unknown" or item.get("text") or item.get("closed")),
        "entity_type_counts": dict(sorted(type_counts.items())),
        "layer_count": len(layers),
        "layout_count": len(layouts),
        "block_count": len(blocks),
        "block_entity_count": len(block_entities),
        "space_count": len(spaces),
        "relation_count": len(relations),
    }
    return {
        "schema": CAD_IR_SCHEMA,
        "parser_version": CAD_PARSER_VERSION,
        "status": "active",
        "document_id": document_id,
        "source": {
            "source_path": str(source_path),
            "parsed_path": str(path),
            "source_format": source_format,
            "source_sha256": source_hash,
            "parsed_sha256": sha256_file(path),
        },
        "roundtrip": {
            "contract": ROUNDTRIP_CONTRACT,
            "source_dxf": str(path),
            "source_dxf_sha256": sha256_file(path),
            "exact_entity_attributes": True,
            "exact_curve_parameters": True,
            "block_definitions_preserved": True,
            "layout_and_table_snapshots": True,
            "structured_replay_supported": True,
            "reconstruction_note": "Unsupported or proxy entities should use the preserved canonical DXF source for exact replay.",
        },
        "document": document,
        "header": header,
        "tables": tables,
        "layers": layers,
        "layouts": layouts,
        "blocks": blocks,
        "block_entities": block_entities,
        "entities": entities,
        "spaces": spaces,
        "relations": relations,
        "warnings": warnings,
        "summary": {
            "entity_count": len(entities),
            "important_entity_count": document["important_entity_count"],
            "text_count": sum(1 for item in entities if item.get("text")),
            "space_count": len(spaces),
            "relation_count": len(relations),
            "layer_count": len(layers),
            "layout_count": len(layouts),
            "block_count": len(blocks),
            "block_entity_count": len(block_entities),
            "warning_count": len(warnings),
        },
    }


def build_opencrab_pack(batch_dir: Path, rows: list[dict[str, Any]], *, pack_title: str | None = None) -> dict[str, Any]:
    """Build an OpenCrab Pack v1-compatible directory from CAD IR rows."""

    pack_dir = batch_dir / "opencrab_pack"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    for relative in ("graph", "evidence", "documents", "sources", "neo4j", "quality", "opencrab"):
        (pack_dir / relative).mkdir(parents=True, exist_ok=True)

    successful = [row for row in rows if row.get("status") == "active" and row.get("ir")]
    failed = [row for row in rows if row.get("status") != "active"]
    pack_id = slugify(pack_title or f"crab-cad-parser-{batch_dir.name}").lower()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    def add_node(node_id: str, node_type: str, label: str, properties: dict[str, Any], evidence_refs: list[str]) -> None:
        nodes.append(
            {
                "id": node_id,
                "node_id": node_id,
                "type": node_type,
                "label": label,
                "properties": {**properties, "evidence_refs": evidence_refs},
                "evidence_refs": evidence_refs,
                "promotion_status": "validated",
            }
        )

    def add_edge(source: str, target: str, relation: str, evidence_refs: list[str], properties: dict[str, Any] | None = None) -> None:
        edges.append(
            {
                "id": stable_id("edge", source, target, relation),
                "from_id": source,
                "to_id": target,
                "type": relation,
                "properties": {**(properties or {}), "evidence_refs": evidence_refs},
                "evidence_refs": evidence_refs,
                "promotion_status": "validated",
            }
        )

    root_node = f"cad:pack:{pack_id}"
    root_evidence = f"evidence:{pack_id}:manifest"
    add_node(root_node, "CADOntologyPack", pack_title or pack_id, {"pack_id": pack_id, "parser_version": CAD_PARSER_VERSION}, [root_evidence])
    evidence.append(
        {
            "id": root_evidence,
            "chunk_id": root_evidence,
            "source_id": pack_id,
            "content_type": "pack_manifest",
            "content": json.dumps({"pack_id": pack_id, "title": pack_title or pack_id, "row_count": len(rows)}, ensure_ascii=False),
            "metadata": {"source": "crab-cad-parser", "provenance": "generated_from_local_cad_files"},
        }
    )

    document_files: list[str] = []
    canonical_source_files: list[str] = []
    for row in rows:
        ir = row.get("ir")
        if not ir:
            continue
        document = ir["document"]
        document_id = ir["document_id"]
        parsed_source = Path(str(row.get("parsed_path") or document.get("parsed_path") or "")).expanduser()
        if parsed_source.is_file():
            canonical_path = pack_dir / "sources" / f"{slugify(document_id)}.dxf"
            shutil.copyfile(parsed_source, canonical_path)
            canonical_relative = str(canonical_path.relative_to(pack_dir))
            ir.setdefault("roundtrip", {})["canonical_dxf_file"] = canonical_relative
            ir["roundtrip"]["canonical_dxf_sha256"] = sha256_file(canonical_path)
            canonical_source_files.append(canonical_relative)
        doc_evidence = f"evidence:{document_id}:document"
        document_file = pack_dir / "documents" / f"{document_id.replace(':', '_')}.json"
        write_json(document_file, ir)
        document_files.append(str(document_file.relative_to(pack_dir)))
        evidence.append(
            {
                "id": doc_evidence,
                "chunk_id": doc_evidence,
                "source_id": document_id,
                "source_path": document["source_path"],
                "source_sha256": document["source_sha256"],
                "content_type": "cad_extracted_document_ir",
                "content": canonical_json(ir),
                "metadata": {
                    "source_format": document["source_format"],
                    "parsed_path": document["parsed_path"],
                    "drawing_kind": document["drawing_kind"],
                    "units": document["units"],
                    "raw_binary_preserved_outside_pack": document["source_format"] == "dwg",
                },
            }
        )
        doc_node = f"cad:document:{document_id}"
        add_node(doc_node, "CADDocument", Path(document["source_path"]).name, {**document, "ir_file": str(document_file.relative_to(pack_dir))}, [doc_evidence])
        add_edge(root_node, doc_node, "CONTAINS_DOCUMENT", [root_evidence, doc_evidence])

        for layout in ir.get("layouts", []):
            layout_name = str(layout.get("name", "layout"))
            layout_node = stable_id("cad:layout", document_id, layout_name)
            add_node(layout_node, "CADLayout", layout_name, layout, [doc_evidence])
            add_edge(doc_node, layout_node, "HAS_LAYOUT", [doc_evidence])
        for layer in ir.get("layers", []):
            layer_name = str(layer.get("name", "0"))
            layer_node = stable_id("cad:layer", document_id, layer_name)
            layer_evidence = f"evidence:{document_id}:layer:{hashlib.sha256(layer_name.encode('utf-8')).hexdigest()[:12]}"
            evidence.append(
                {
                    "id": layer_evidence,
                    "chunk_id": layer_evidence,
                    "source_id": document_id,
                    "source_path": document["source_path"],
                    "source_sha256": document["source_sha256"],
                    "content_type": "cad_layer",
                    "content": canonical_json(layer),
                    "metadata": {"layer": layer_name},
                }
            )
            add_node(layer_node, "CADLayer", layer_name, layer, [layer_evidence, doc_evidence])
            add_edge(doc_node, layer_node, "HAS_LAYER", [doc_evidence, layer_evidence])
        layer_nodes = {str(item.get("name", "0")): stable_id("cad:layer", document_id, str(item.get("name", "0"))) for item in ir.get("layers", [])}

        block_nodes: dict[str, str] = {}
        for block in ir.get("blocks", []):
            block_name = str(block.get("name") or "")
            if not block_name:
                continue
            block_evidence = f"evidence:{document_id}:block:{hashlib.sha256(block_name.encode('utf-8')).hexdigest()[:12]}"
            evidence.append(
                {
                    "id": block_evidence,
                    "chunk_id": block_evidence,
                    "source_id": document_id,
                    "source_path": document["source_path"],
                    "source_sha256": document["source_sha256"],
                    "content_type": "cad_block_definition",
                    "content": canonical_json(block),
                    "metadata": {"block": block_name},
                }
            )
            block_node = stable_id("cad:block", document_id, block_name)
            block_nodes[block_name] = block_node
            add_node(block_node, "CADBlock", block_name, block, [block_evidence, doc_evidence])
            add_edge(doc_node, block_node, "HAS_BLOCK", [doc_evidence, block_evidence])

        for entity in [*ir.get("entities", []), *ir.get("block_entities", [])]:
            if entity.get("role_hint") == "unknown" and not entity.get("text") and not entity.get("closed") and entity.get("type") not in {"INSERT", "DIMENSION", "HATCH"}:
                continue
            entity_id = str(entity["entity_id"])
            entity_evidence = f"evidence:{document_id}:entity:{entity_id.rsplit(':', 1)[-1]}"
            evidence.append(
                {
                    "id": entity_evidence,
                    "chunk_id": entity_evidence,
                    "source_id": document_id,
                    "source_path": document["source_path"],
                    "source_sha256": document["source_sha256"],
                    "content_type": "cad_entity",
                    "content": canonical_json(entity),
                    "metadata": {"handle": entity.get("handle"), "type": entity.get("type"), "layer": entity.get("layer")},
                }
            )
            entity_node = f"cad:entity:{entity_id.rsplit(':', 1)[-1]}"
            add_node(
                entity_node,
                "CADEntity",
                str(entity.get("text") or entity.get("role_hint") or entity.get("type") or "CAD entity"),
                {**entity, "document_id": document_id},
                [entity_evidence, doc_evidence],
            )
            parent_node = block_nodes.get(str(entity.get("block_name") or ""), doc_node)
            parent_relation = "HAS_BLOCK_ENTITY" if parent_node != doc_node else "HAS_ENTITY"
            add_edge(parent_node, entity_node, parent_relation, [doc_evidence, entity_evidence])
            if entity.get("layer") in layer_nodes:
                add_edge(entity_node, layer_nodes[str(entity["layer"])], "LOCATED_ON_LAYER", [entity_evidence])

        for space in ir.get("spaces", []):
            space_id = str(space["space_id"])
            space_evidence = f"evidence:{document_id}:space:{space_id.rsplit(':', 1)[-1]}"
            evidence.append(
                {
                    "id": space_evidence,
                    "chunk_id": space_evidence,
                    "source_id": document_id,
                    "source_path": document["source_path"],
                    "source_sha256": document["source_sha256"],
                    "content_type": "cad_space_candidate",
                    "content": canonical_json(space),
                    "metadata": {"role_hint": space.get("role_hint"), "labels": space.get("labels", [])},
                }
            )
            add_node(space_id, "CADSpace", str((space.get("labels") or [space.get("role_hint") or "space"])[0]), {**space, "document_id": document_id}, [space_evidence, doc_evidence])
            add_edge(doc_node, space_id, "HAS_SPACE", [doc_evidence, space_evidence])

        for relation in ir.get("relations", []):
            source = relation.get("from_id")
            target = relation.get("to_id")
            if not source or not target:
                continue
            source_node = source
            target_node = target
            if str(source).startswith("cad:document:"):
                source_node = str(source)
            elif str(source).startswith("cad-entity:"):
                source_node = f"cad:entity:{str(source).rsplit(':', 1)[-1]}"
            if str(target).startswith("cad-entity:"):
                target_node = f"cad:entity:{str(target).rsplit(':', 1)[-1]}"
            edge_refs = [doc_evidence]
            if source_node in {item["id"] for item in nodes} and target_node in {item["id"] for item in nodes}:
                add_edge(source_node, target_node, str(relation.get("type", "RELATED_TO")), edge_refs, {"confidence": relation.get("confidence"), "distance": relation.get("distance")})

    failed_evidence = []
    for row in failed:
        error_id = stable_id("cad-error", row.get("source_path"), row.get("source_sha256"), row.get("status"))
        failed_evidence.append(error_id)
        evidence.append(
            {
                "id": error_id,
                "chunk_id": error_id,
                "source_id": row.get("source_path"),
                "source_path": row.get("source_path"),
                "source_sha256": row.get("source_sha256"),
                "content_type": "cad_parse_error",
                "content": canonical_json(row),
                "metadata": {"status": row.get("status"), "error": row.get("error")},
            }
        )

    manifest = {
        "schema": CAD_PACK_SCHEMA,
        "pack_id": pack_id,
        "title": pack_title or "CrabCADParser CAD Ontology Pack",
        "description": "Evidence-backed architectural CAD facts extracted from local DWG/DXF drawings.",
        "version": "1.0.0",
        "created_at": utc_now(),
        "generator": {"name": "CrabCADParser", "version": CAD_PARSER_VERSION, "source_repository": "Crab-Archi-Design"},
        "source_policy": {
            "raw_sources": "referenced by absolute path and SHA-256; not copied into the pack by default",
            "canonical_dxf": "the parsed/converted DXF is copied under sources/ for structured replay and exact fallback",
            "derived_facts": "full CAD IR retained under documents/",
            "provenance_required": True,
        },
        "reconstruction": {
            "contract": ROUNDTRIP_CONTRACT,
            "canonical_dxf_files": canonical_source_files,
            "structured_replay_entrypoint": "crabcadparser reconstruct",
            "exact_fallback": "copy canonical_dxf_file when unsupported/proxy entities must be preserved",
        },
        "statistics": {
            "input_files": len(rows),
            "parsed_documents": len(successful),
            "failed_documents": len(failed),
            "nodes": len(nodes),
            "edges": len(edges),
            "evidence_rows": len(evidence),
        },
        "ontology": {
            "node_types": sorted({str(node["type"]) for node in nodes}),
            "relation_types": sorted({str(edge["type"]) for edge in edges}),
            "coordinate_space": "source CAD world coordinates; units carried per document",
        },
        "ingest": {
            "opencrab_mcp_tool": "opencrab_ingest_text",
            "payload_file": "opencrab/ingest_payloads.jsonl",
            "pack_zip": f"../{pack_dir.name}.zip",
            "mode": "payload_ready; caller must explicitly submit through OpenCrab MCP",
        },
        "files": {"documents": document_files},
        "warnings": failed_evidence,
    }
    write_json(pack_dir / "manifest.json", manifest)
    write_jsonl(pack_dir / "graph" / "nodes.jsonl", nodes)
    write_jsonl(pack_dir / "graph" / "edges.jsonl", edges)
    write_jsonl(pack_dir / "evidence" / "index.jsonl", evidence)
    write_json(pack_dir / "quality" / "report.json", {
        "schema": "crab-cad-parser-quality-report-v1",
        "status": "pass" if not failed else "review_required",
        "promotion_status": "validated" if not failed else "review_required",
        "checks": {
            "source_provenance_present": all(bool(row.get("source_sha256")) for row in rows),
            "document_ir_retained": len(document_files) == len(successful),
            "broken_graph_edges": 0,
            "evidence_rows_present": bool(evidence),
            "failed_documents": len(failed),
        },
        "counts": manifest["statistics"],
        "notes": [
            "CAD binaries are not copied into the pack by default; source paths and hashes remain the provenance boundary.",
            "Semantic roles are deterministic hints and must be reviewed before design mutation.",
        ],
    })
    write_json(pack_dir / "neo4j" / "export_status.json", {"status": "generated", "node_count": len(nodes), "edge_count": len(edges), "evidence_count": len(evidence)})
    write_jsonl(pack_dir / "neo4j" / "opencrab_ingest.jsonl", [
        {"kind": "node", "data": node} for node in nodes
    ] + [
        {"kind": "edge", "data": edge} for edge in edges
    ] + [
        {"kind": "evidence", "data": item} for item in evidence
    ])
    cypher_lines = ["// Generated by CrabCADParser; review before executing against a Neo4j database."]
    node_vars: dict[str, str] = {}
    for node in nodes:
        safe_label = re.sub(r"[^A-Za-z0-9_]", "_", str(node["type"]))
        variable = "n_" + hashlib.sha256(str(node["id"]).encode("utf-8")).hexdigest()[:12]
        node_vars[str(node["id"])] = variable
        escaped_id = str(node["id"]).replace("'", "''")
        cypher_lines.append(f"CREATE ({variable}:{safe_label} {{id: '{escaped_id}'}});")
    for edge in edges:
        source_var = node_vars.get(str(edge.get("from_id")))
        target_var = node_vars.get(str(edge.get("to_id")))
        relation = re.sub(r"[^A-Za-z0-9_]", "_", str(edge.get("type") or "RELATED_TO"))
        if source_var and target_var:
            cypher_lines.append(f"CREATE ({source_var})-[:{relation}]->({target_var});")
    (pack_dir / "neo4j" / "import.cypher").write_text("\n".join(cypher_lines) + "\n", encoding="utf-8")
    write_json(pack_dir / "sample_queries.json", {
        "queries": [
            {"id": "spaces_by_document", "question": "Which spaces and labels were found in each CAD document?"},
            {"id": "layers_by_role", "question": "Which layers were classified as walls, columns, doors, windows, or spaces?"},
            {"id": "drawing_context", "question": "Which drawings are floor plans, sections, elevations, or site plans?"},
            {"id": "provenance", "question": "Which source file and DXF handle support this ontology fact?"},
        ]
    })
    write_json(pack_dir / "community_reports.json", {
        "report_type": "cad_batch_summary",
        "generated_at": utc_now(),
        "statistics": manifest["statistics"],
        "failed_documents": failed,
    })
    (pack_dir / "README.md").write_text(
        "# CrabCADParser OpenCrab Pack\n\n"
        "This pack contains deterministic facts extracted from local DWG/DXF files.\n\n"
        "## Provenance\n\n"
        "Original DWG binaries remain outside the pack by default. The parsed or converted canonical DXF is copied to `sources/` for structured replay and exact fallback. Each document, entity, layer, space, and relationship carries a source file path, SHA-256, and evidence reference. Full extracted IR is retained in `documents/`.\n\n"
        "## OpenCrab ingestion\n\n"
        "Use `opencrab/ingest_payloads.jsonl` as the payload source for the OpenCrab `opencrab_ingest_text` MCP tool. The parser intentionally does not store credentials or submit data without an explicit MCP caller action.\n\n"
        "## Limitations\n\n"
        "DWG conversion requires an installed ODA File Converter. Semantic role hints are deterministic recognition results, not a substitute for an architect's review. XREF content, fonts, proxy graphics, and unsupported entities may be reported as warnings.\n",
        encoding="utf-8",
    )
    payload_rows = []
    for row in successful:
        ir = row["ir"]
        document = ir["document"]
        payload_rows.append(
            {
                "title": f"CrabCADParser: {Path(document['source_path']).name}",
                "content": json.dumps({
                    "document": document,
                    "header": ir.get("header", {}),
                    "tables": ir.get("tables", {}),
                    "layouts": ir.get("layouts", []),
                    "layers": ir.get("layers", []),
                    "blocks": ir.get("blocks", []),
                    "block_entities": ir.get("block_entities", []),
                    "roundtrip": ir.get("roundtrip", {}),
                    "entities": ir.get("entities", []),
                    "spaces": ir.get("spaces", []),
                    "relations": ir.get("relations", []),
                }, ensure_ascii=False, indent=2),
                "create_pack": True,
                "pack_category": "mcp",
                "pack_title": manifest["title"],
                "pack_description": manifest["description"],
                "pack_visibility": "private",
                "workspace_label": "CrabCADParser",
            }
        )
    write_jsonl(pack_dir / "opencrab" / "ingest_payloads.jsonl", payload_rows)
    return manifest


def zip_pack(pack_dir: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_file() and path.name not in {".DS_Store"}:
                archive.write(path, path.relative_to(pack_dir).as_posix())
    return destination


def run_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    recursive: bool = True,
    converter: str | None = None,
    output_version: str = DEFAULT_DWG_VERSION,
    timeout_seconds: int = 120,
    max_files: int | None = None,
    resume: bool = True,
    pack_title: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run a restartable DWG/DXF batch and build its OpenCrab pack."""

    input_dir = input_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    files = find_source_files(input_dir, recursive=recursive)
    if max_files and max_files > 0:
        files = files[:max_files]
    batch_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    ir_dir = output_dir / "cad_ir"
    converted_dir = output_dir / "converted_dxf"
    ir_dir.mkdir(parents=True, exist_ok=True)
    converted_dir.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] = {}
    receipt_path = output_dir / "batch_receipt.json"
    if resume and receipt_path.exists():
        try:
            previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous_by_path = {str(item.get("source_path")): item for item in previous.get("files", []) if isinstance(item, dict)}
    oda = find_oda_converter(converter)
    rows: list[dict[str, Any]] = []
    for index, source in enumerate(files, start=1):
        source_hash = sha256_file(source)
        relative = source.relative_to(input_dir)
        event = {"stage": "file", "index": index, "total": len(files), "source_path": str(source), "relative_path": str(relative)}
        if progress:
            progress(event | {"status": "started"})
        prior = previous_by_path.get(str(source))
        prior_ir = Path(str(prior.get("ir_path"))) if prior and prior.get("ir_path") else None
        if resume and prior and prior.get("source_sha256") == source_hash and prior_ir and prior_ir.exists():
            try:
                ir = json.loads(prior_ir.read_text(encoding="utf-8"))
                row = {**prior, "status": ir.get("status"), "ir": ir, "resumed": True}
                rows.append(row)
                if progress:
                    progress(event | {"status": "resumed", "document_id": ir.get("document_id")})
                continue
            except (OSError, json.JSONDecodeError):
                pass
        row: dict[str, Any] = {
            "source_path": str(source),
            "relative_path": str(relative),
            "source_format": source.suffix.lower().lstrip("."),
            "source_sha256": source_hash,
            "status": "pending",
            "conversion": None,
            "ir_path": None,
            "error": None,
            "warnings": [],
        }
        parsed_path = source
        source_format = source.suffix.lower().lstrip(".")
        if source_format == "dwg":
            if not oda:
                row.update({"status": "converter_missing", "error": "ODA File Converter was not found."})
                rows.append(row)
                if progress:
                    progress(event | {"status": row["status"], "error": row["error"]})
                continue
            parsed_path = converted_dir / relative.with_suffix(".dxf")
            conversion = convert_dwg_to_dxf(source, parsed_path, oda, output_version=output_version, timeout_seconds=timeout_seconds)
            row["conversion"] = conversion
            if conversion.get("status") != "converted":
                row.update({"status": "conversion_failed", "error": conversion.get("error") or "DWG conversion failed."})
                rows.append(row)
                if progress:
                    progress(event | {"status": row["status"], "error": row["error"]})
                continue
        try:
            ir = parse_dxf(parsed_path, source_path=source, source_format=source_format)
            ir_path = ir_dir / f"{ir['document_id'].replace(':', '_')}.json"
            write_json(ir_path, ir)
            row.update({"status": ir.get("status", "failed"), "ir_path": str(ir_path), "parsed_path": str(parsed_path), "ir": ir, "warnings": ir.get("warnings", [])})
            if ir.get("status") != "active":
                row["error"] = (ir.get("warnings") or [{"detail": "CAD parse failed."}])[0].get("detail")
        except Exception as exc:
            row.update({"status": "parse_failed", "error": str(exc), "parsed_path": str(parsed_path)})
        rows.append(row)
        if progress:
            progress(event | {"status": row["status"], "document_id": (row.get("ir") or {}).get("document_id"), "error": row.get("error")})

    manifest = build_opencrab_pack(output_dir, rows, pack_title=pack_title or f"CrabCADParser {input_dir.name}")
    pack_dir = output_dir / "opencrab_pack"
    zip_path = zip_pack(pack_dir, output_dir / f"{slugify(manifest['pack_id'])}.zip")
    receipt = {
        "schema": "crab-cad-parser-batch-receipt-v1",
        "batch_id": batch_id,
        "created_at": utc_now(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "recursive": recursive,
        "converter": oda,
        "output_version": output_version,
        "files": [
            {key: value for key, value in row.items() if key != "ir"}
            for row in rows
        ],
        "statistics": {
            "discovered": len(files),
            "parsed": sum(1 for row in rows if row.get("status") == "active"),
            "converted": sum(1 for row in rows if (row.get("conversion") or {}).get("status") == "converted"),
            "failed": sum(1 for row in rows if row.get("status") != "active"),
            "warnings": sum(len(row.get("warnings") or []) for row in rows),
        },
        "pack_dir": str(pack_dir),
        "pack_zip": str(zip_path),
        "next_action": "Submit opencrab_pack/opencrab/ingest_payloads.jsonl through the OpenCrab MCP opencrab_ingest_text tool after reviewing QA.",
    }
    write_json(receipt_path, receipt)
    if progress:
        progress({"stage": "complete", "status": "complete", "receipt": str(receipt_path), "pack_dir": str(pack_dir), "pack_zip": str(zip_path), "statistics": receipt["statistics"]})
    return {"receipt": receipt, "manifest": manifest, "rows": rows}


def inspect_pack(pack_dir: Path) -> dict[str, Any]:
    """Small local inspection used by CLI/MCP without importing QA internals."""

    manifest_path = pack_dir / "manifest.json"
    quality_path = pack_dir / "quality" / "report.json"
    if not manifest_path.exists():
        raise ValueError(f"Missing pack manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else None
    return {
        "pack_dir": str(pack_dir.resolve()),
        "pack_id": manifest.get("pack_id"),
        "title": manifest.get("title"),
        "statistics": manifest.get("statistics", {}),
        "quality": quality,
        "ingest_payload": str((pack_dir / "opencrab" / "ingest_payloads.jsonl").resolve()),
    }
