from __future__ import annotations

from xml.etree.ElementTree import Element

PRESENTATION_ATTRS = {
    "class",
    "fill",
    "font-family",
    "font-size",
    "opacity",
    "stroke",
    "stroke-width",
    "text-anchor",
}


def parse_inline_style(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    style: dict[str, str] = {}
    for item in raw.split(";"):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        style[key.strip()] = value.strip()
    return style


def inherited_style(parent: dict[str, str], element: Element) -> dict[str, str]:
    style = dict(parent)
    for key in PRESENTATION_ATTRS:
        if key in element.attrib:
            style[key] = element.attrib[key]
    style.update(parse_inline_style(element.attrib.get("style")))
    return style


def style_number(style: dict[str, str], key: str, default: float = 0.0) -> float:
    from crab_archi_design.svg.transform import parse_numbers

    numbers = parse_numbers(style.get(key, ""))
    return numbers[0] if numbers else default
