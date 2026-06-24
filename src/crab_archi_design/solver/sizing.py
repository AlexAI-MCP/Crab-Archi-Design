from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

PYEONG_TO_M2 = 3.305785

ROLE_ALIASES = {
    "greenery_lounge": ["그리너리라운지", "그리너리 라운지", "그리너리카페", "그리너리 카페", "작은도서관", "도서관", "카페", "library", "cafe"],
    "fitness_gx": ["피트니스", "gx룸", "gx", "fitness"],
    "golf_screen": ["골프클럽", "스크린골프", "인도어 골프", "골프", "golf"],
    "sauna_locker_shower": ["샤워실", "샤워", "사우나", "건식사우나", "락커", "locker", "sauna", "shower"],
    "hall_lobby": ["로비", "홀", "lobby", "hall"],
    "management_support": ["생활지원센터", "관리", "관리사무소", "회의", "탕비", "management"],
}

SKIP_LABELS = {
    "구분",
    "세대수",
    "법정시설",
    "공용면적",
    "필수",
    "특화",
    "소형",
    "중형",
    "대형",
    "합계",
    "면적(평)",
    "면적",
}


def classify_program_role(text: str) -> str | None:
    normalized = normalize_text(text)
    for role, aliases in ROLE_ALIASES.items():
        if any(normalize_text(alias) in normalized for alias in aliases):
            return role
    return None


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def read_csv_grid(path: Path) -> tuple[list[list[str]], str]:
    for encoding in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            text = path.read_text(encoding=encoding)
            return list(csv.reader(text.splitlines())), encoding
        except UnicodeDecodeError:
            continue
    text = path.read_text(encoding="utf-8", errors="replace")
    return list(csv.reader(text.splitlines())), "utf-8-replace"


def numeric_text(value: Any) -> str:
    return re.sub(r"[^0-9.]", "", str(value or ""))


def parse_number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group(0)) if match else None


def find_household_column(rows: list[list[str]], households: int | None) -> int | None:
    if households is None:
        return None
    household_text = str(households)
    for row in rows:
        if not any("세대" in str(cell) or "household" in str(cell).lower() for cell in row):
            continue
        for index, cell in enumerate(row):
            if numeric_text(cell) == household_text:
                return index
    for row in rows:
        for index, cell in enumerate(row):
            if numeric_text(cell) == household_text:
                return index
    return None


def metric_index(row: list[str]) -> int | None:
    for index, cell in enumerate(row):
        text = normalize_text(cell)
        if "면적" in text and ("평" in text or "m2" in text or "㎡" in text):
            return index
    return None


def program_label(row: list[str], metric_column: int) -> str:
    labels = []
    for cell in row[:metric_column]:
        text = str(cell or "").strip()
        if not text or normalize_text(text) in {normalize_text(item) for item in SKIP_LABELS}:
            continue
        labels.append(text)
    return " ".join(labels[-2:]) if labels else ""


def area_unit(metric_cell: str) -> str:
    text = normalize_text(metric_cell)
    if "평" in text:
        return "pyeong"
    if "㎡" in text or "m2" in text:
        return "m2"
    return "unknown"


def line_item_from_grid_row(row: list[str], household_column: int, source_file: str, row_number: int) -> dict[str, Any] | None:
    metric_column = metric_index(row)
    if metric_column is None or household_column >= len(row):
        return None
    value = parse_number(row[household_column])
    if value is None:
        return None
    label = program_label(row, metric_column)
    role = classify_program_role(" ".join(row[: metric_column + 1]))
    if role is None:
        return None
    unit = area_unit(row[metric_column])
    area_pyeong = value if unit == "pyeong" else value / PYEONG_TO_M2 if unit == "m2" else None
    area_m2 = value * PYEONG_TO_M2 if unit == "pyeong" else value if unit == "m2" else None
    return {
        "role": role,
        "label": label,
        "metric": row[metric_column],
        "value": value,
        "unit": unit,
        "area_pyeong": round(area_pyeong, 3) if area_pyeong is not None else None,
        "area_m2": round(area_m2, 3) if area_m2 is not None else None,
        "source_file": source_file,
        "row_number": row_number,
        "source": "standards.csv_grid",
    }


def extract_grid_line_items(path: Path, households: int | None) -> list[dict[str, Any]]:
    rows, _encoding = read_csv_grid(path)
    household_column = find_household_column(rows, households)
    if household_column is None:
        return []
    items: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=1):
        item = line_item_from_grid_row(row, household_column, str(path), row_number)
        if item:
            items.append(item)
    return items


def extract_dict_line_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = item.get("payload", {}).get("selected_rows", [])
    if not isinstance(rows, list):
        return []
    line_items: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        text = " ".join(f"{key} {value}" for key, value in row.items())
        role = classify_program_role(text)
        if role is None:
            continue
        area = row_area_hint(row)
        if area is None:
            continue
        line_items.append(
            {
                "role": role,
                "label": compact_label(text),
                "metric": "manifest_selected_row",
                "value": area,
                "unit": "unknown",
                "area_pyeong": None,
                "area_m2": None,
                "source_file": item.get("source_file"),
                "row_number": row_index,
                "source": "standards.selected_rows",
            }
        )
    return line_items


def row_area_hint(row: dict[str, Any]) -> float | None:
    for key, value in row.items():
        text = f"{key} {value}"
        if not any(term in text.lower() for term in ["면적", "area", "평", "sqm", "m2", "㎡"]):
            continue
        number = parse_number(value)
        if number is not None:
            return number
    for value in row.values():
        number = parse_number(value)
        if number is not None:
            return number
    return None


def compact_label(text: str, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def dedupe_line_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("source_file"), item.get("row_number"), item.get("role"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def extract_standard_line_items(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    households = manifest.get("household_count")
    items: list[dict[str, Any]] = []
    for standard in manifest.get("standard_items", []):
        source_file = Path(str(standard.get("source_file") or "")).expanduser()
        if source_file.exists() and source_file.suffix.lower() == ".csv":
            items.extend(extract_grid_line_items(source_file, households))
        else:
            items.extend(extract_dict_line_items(standard))
    if not items:
        for standard in manifest.get("standard_items", []):
            items.extend(extract_dict_line_items(standard))
    return dedupe_line_items(items)


def is_greenery_combined(item: dict[str, Any]) -> bool:
    text = normalize_text(item.get("label"))
    return item.get("role") == "greenery_lounge" and "카페" in text and ("도서관" in text or "그리너리" in text)


def sauna_additive_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shower = [item for item in items if "샤워" in normalize_text(item.get("label"))]
    dry = [item for item in items if "건식" in normalize_text(item.get("label"))]
    sauna = [item for item in items if "사우나" in normalize_text(item.get("label")) and item not in dry]
    selected = []
    selected.extend(shower[:1])
    if sauna:
        selected.append(max(sauna, key=lambda item: float(item.get("area_m2") or item.get("value") or 0.0)))
    selected.extend(dry[:1])
    return selected or items


def aggregate_role_items(role: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    selected = items
    policy = "sum_role_area_rows"
    if role == "greenery_lounge":
        combined = [item for item in items if is_greenery_combined(item)]
        if combined:
            selected = [max(combined, key=lambda item: float(item.get("area_m2") or item.get("value") or 0.0))]
            policy = "prefer_combined_greenery_cafe_library_row"
    elif role == "sauna_locker_shower":
        selected = sauna_additive_items(items)
        policy = "sum_shower_plus_largest_sauna_option_plus_dry_sauna"
    area_pyeong_values = [float(item["area_pyeong"]) for item in selected if item.get("area_pyeong") is not None]
    area_m2_values = [float(item["area_m2"]) for item in selected if item.get("area_m2") is not None]
    return {
        "role": role,
        "row_count": len(items),
        "selected_row_count": len(selected),
        "aggregation_policy": policy,
        "target_area_pyeong": round(sum(area_pyeong_values), 3) if area_pyeong_values else None,
        "target_area_m2": round(sum(area_m2_values), 3) if area_m2_values else None,
        "line_items": selected,
    }


def extract_program_targets(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = extract_standard_line_items(manifest)
    by_role: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_role.setdefault(str(item["role"]), []).append(item)
    return [aggregate_role_items(role, by_role[role]) for role in sorted(by_role)]


def standard_role_rows_from_manifest(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    targets = extract_program_targets(manifest)
    rows: dict[str, dict[str, Any]] = {}
    for target in targets:
        role = str(target["role"])
        rows[role] = {
            "role": role,
            "rows": target.get("line_items", []),
            "row_count": target.get("row_count", 0),
            "target_area_hint": target.get("target_area_pyeong"),
            "target_area_pyeong": target.get("target_area_pyeong"),
            "target_area_m2": target.get("target_area_m2"),
            "aggregation_policy": target.get("aggregation_policy"),
        }
    return rows
