from __future__ import annotations

import statistics
import re
from typing import Any


def svg_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_bbox(box: dict[str, Any] | None) -> dict[str, float]:
    box = box or {}
    return {
        "x": svg_float(box.get("x")),
        "y": svg_float(box.get("y")),
        "width": svg_float(box.get("width", box.get("w"))),
        "height": svg_float(box.get("height", box.get("h"))),
    }


def point_from(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return (svg_float(value[0]), svg_float(value[1]))


def parse_dimension_mm(text: str, min_mm: float = 1000.0, max_mm: float = 60000.0) -> float | None:
    cleaned = str(text or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:\s*(?:mm|㎜|m|M))?", cleaned):
        return None
    number_match = re.match(r"\d+(?:\.\d+)?", cleaned)
    if not number_match:
        return None
    value = float(number_match.group(0))
    lowered = cleaned.lower()
    if lowered.endswith("m") and not lowered.endswith("mm"):
        value *= 1000.0
    if min_mm <= value <= max_mm:
        return value
    return None


def text_dimension_candidates(ir: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in (ir or {}).get("nodes", []):
        if node.get("tag") != "text":
            continue
        content = str((node.get("text") or {}).get("content") or "").strip()
        dimension_mm = parse_dimension_mm(content)
        if dimension_mm is None:
            continue
        anchor = point_from((node.get("text") or {}).get("anchor")) or point_from(node.get("centroid"))
        if anchor is None:
            continue
        candidates.append(
            {
                "node_id": node.get("id"),
                "source_id": node.get("source_id"),
                "text": content,
                "dimension_mm": dimension_mm,
                "point": [round(anchor[0], 3), round(anchor[1], 3)],
            }
        )
    return candidates


def line_axis(box: dict[str, float]) -> str | None:
    width = box["width"]
    height = box["height"]
    span = max(width, height)
    thickness = min(width, height)
    if span < 20.0:
        return None
    if width >= height * 8.0 and thickness <= max(8.0, width * 0.05):
        return "horizontal"
    if height >= width * 8.0 and thickness <= max(8.0, height * 0.05):
        return "vertical"
    return None


def line_segment_candidates(ir: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in (ir or {}).get("nodes", []):
        if node.get("tag") not in {"line", "polyline", "path"} or node.get("is_closed"):
            continue
        box = normalize_bbox(node.get("bbox"))
        axis = line_axis(box)
        if axis is None:
            continue
        span = box["width"] if axis == "horizontal" else box["height"]
        center = [round(box["x"] + box["width"] * 0.5, 3), round(box["y"] + box["height"] * 0.5, 3)]
        candidates.append(
            {
                "node_id": node.get("id"),
                "source_id": node.get("source_id"),
                "tag": node.get("tag"),
                "axis": axis,
                "bbox": box,
                "center": center,
                "span_world": round(span, 3),
            }
        )
    return candidates


def text_line_distance(text: dict[str, Any], line: dict[str, Any]) -> float | None:
    x, y = text["point"]
    box = line["bbox"]
    span = line["span_world"]
    pad = max(35.0, span * 0.2)
    if line["axis"] == "horizontal":
        if not (box["x"] - pad <= x <= box["x"] + box["width"] + pad):
            return None
        return abs(y - (box["y"] + box["height"] * 0.5))
    if not (box["y"] - pad <= y <= box["y"] + box["height"] + pad):
        return None
    return abs(x - (box["x"] + box["width"] * 0.5))


def calibration_pairs(texts: list[dict[str, Any]], lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for text in texts:
        options: list[dict[str, Any]] = []
        for line in lines:
            distance = text_line_distance(text, line)
            if distance is None:
                continue
            max_distance = max(45.0, float(line["span_world"]) * 0.18)
            if distance > max_distance:
                continue
            ratio = float(text["dimension_mm"]) / max(float(line["span_world"]), 1e-9)
            if 0.1 <= ratio <= 1000.0:
                options.append(
                    {
                        "dimension_node_id": text.get("node_id"),
                        "dimension_text": text.get("text"),
                        "dimension_mm": text.get("dimension_mm"),
                        "line_node_id": line.get("node_id"),
                        "line_axis": line.get("axis"),
                        "line_span_world": line.get("span_world"),
                        "distance_world": round(distance, 3),
                        "mm_per_world": round(ratio, 6),
                    }
                )
        if options:
            pairs.append(sorted(options, key=lambda item: (float(item["distance_world"]), -float(item["line_span_world"])))[0])
    return pairs


def best_ratio_cluster(pairs: list[dict[str, Any]], tolerance: float = 0.06) -> list[dict[str, Any]]:
    if not pairs:
        return []
    ordered = sorted(pairs, key=lambda item: float(item["mm_per_world"]))
    best: list[dict[str, Any]] = []
    for start, item in enumerate(ordered):
        base = float(item["mm_per_world"])
        cluster = [candidate for candidate in ordered[start:] if abs(float(candidate["mm_per_world"]) - base) / max(base, 1e-9) <= tolerance]
        if len(cluster) > len(best):
            best = cluster
        elif len(cluster) == len(best) and cluster and best:
            if cluster_ratio_spread(cluster) < cluster_ratio_spread(best):
                best = cluster
    return best


def cluster_ratio_spread(cluster: list[dict[str, Any]]) -> float:
    ratios = [float(item["mm_per_world"]) for item in cluster]
    if len(ratios) < 2:
        return 0.0
    mean = statistics.fmean(ratios)
    return (max(ratios) - min(ratios)) / max(mean, 1e-9)


def confidence_for_cluster(cluster: list[dict[str, Any]], pair_count: int, text_count: int) -> float:
    if not cluster:
        return 0.0
    ratios = [float(item["mm_per_world"]) for item in cluster]
    mean = statistics.fmean(ratios)
    spread = (max(ratios) - min(ratios)) / max(mean, 1e-9) if len(ratios) > 1 else 0.0
    support = min(1.0, len(cluster) / 3.0)
    consistency = max(0.0, 1.0 - spread * 10.0)
    coverage = len(cluster) / max(text_count, 1)
    pair_support = min(1.0, pair_count / 4.0)
    confidence = min(0.95, 0.15 + support * 0.45 + consistency * 0.25 + coverage * 0.1 + pair_support * 0.05)
    if len(cluster) < 2:
        confidence = min(confidence, 0.45)
    return round(confidence, 3)


def infer_architectural_scale(ir: dict[str, Any] | None) -> dict[str, Any]:
    texts = text_dimension_candidates(ir)
    lines = line_segment_candidates(ir)
    pairs = calibration_pairs(texts, lines)
    cluster = best_ratio_cluster(pairs)
    ratios = [float(item["mm_per_world"]) for item in cluster]
    mm_per_world = round(statistics.median(ratios), 6) if ratios else None
    confidence = confidence_for_cluster(cluster, len(pairs), len(texts))
    status = "active" if mm_per_world is not None and confidence >= 0.6 and len(cluster) >= 2 else "review_required"
    return {
        "schema": "crab-archi-design-scale-calibration-v1",
        "status": status,
        "source": "recognition_ir_v2.dimension_text_to_segment",
        "unit": "mm_per_world",
        "mm_per_world": mm_per_world,
        "m_per_world": round(mm_per_world / 1000.0, 9) if mm_per_world is not None else None,
        "confidence": confidence,
        "dimension_text_candidate_count": len(texts),
        "line_segment_candidate_count": len(lines),
        "pair_candidate_count": len(pairs),
        "clustered_pair_count": len(cluster),
        "cluster_pairs": cluster[:20],
        "warnings": [] if status == "active" else ["insufficient_consistent_dimension_text_to_segment_pairs"],
    }
