from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import Element

from crab_archi_design.svg.namespace import local_name

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


CSS_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.MULTILINE)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


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


def collect_css_rules(root: Element) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    order = 0
    for element in root.iter():
        if local_name(element.tag) != "style":
            continue
        css = CSS_COMMENT_RE.sub("", "".join(element.itertext()))
        for selector_text, declaration_text in CSS_BLOCK_RE.findall(css):
            declarations = parse_inline_style(declaration_text)
            if not declarations:
                continue
            for selector in selector_text.split(","):
                parsed_selector = parse_simple_selector(selector.strip())
                if parsed_selector is None:
                    continue
                rules.append(
                    {
                        "selector": parsed_selector,
                        "declarations": declarations,
                        "specificity": selector_specificity(parsed_selector),
                        "order": order,
                    }
                )
                order += 1
    return rules


def parse_simple_selector(selector: str) -> dict[str, Any] | None:
    if not selector or any(token in selector for token in [" ", ">", "+", "~", "[", "]", ":", "*"]):
        return None
    tag_match = re.match(r"^[A-Za-z_][A-Za-z0-9_-]*", selector)
    tag = tag_match.group(0) if tag_match else None
    rest = selector[len(tag) :] if tag else selector
    selector_id: str | None = None
    classes: list[str] = []
    while rest:
        marker = rest[0]
        match = re.match(r"^[.#]([A-Za-z_][A-Za-z0-9_-]*)", rest)
        if marker not in {".", "#"} or match is None:
            return None
        value = match.group(1)
        if marker == "#":
            selector_id = value
        else:
            classes.append(value)
        rest = rest[len(match.group(0)) :]
    if tag is None and selector_id is None and not classes:
        return None
    return {"tag": tag, "id": selector_id, "classes": classes}


def selector_specificity(selector: dict[str, Any]) -> int:
    return (100 if selector.get("id") else 0) + 10 * len(selector.get("classes") or []) + (1 if selector.get("tag") else 0)


def css_rule_matches(element: Element, selector: dict[str, Any]) -> bool:
    tag = selector.get("tag")
    if tag and tag != local_name(element.tag):
        return False
    selector_id = selector.get("id")
    if selector_id and selector_id != element.attrib.get("id"):
        return False
    element_classes = set(element.attrib.get("class", "").split())
    return all(item in element_classes for item in selector.get("classes") or [])


def matched_css_style(element: Element, css_rules: list[dict[str, Any]] | None) -> dict[str, str]:
    if not css_rules:
        return {}
    style: dict[str, str] = {}
    for rule in sorted(css_rules, key=lambda item: (int(item.get("specificity") or 0), int(item.get("order") or 0))):
        if css_rule_matches(element, rule["selector"]):
            style.update(rule["declarations"])
    return style


def inherited_style(parent: dict[str, str], element: Element, css_rules: list[dict[str, Any]] | None = None) -> dict[str, str]:
    style = dict(parent)
    style.update(matched_css_style(element, css_rules))
    for key in PRESENTATION_ATTRS:
        if key in element.attrib:
            style[key] = element.attrib[key]
    style.update(parse_inline_style(element.attrib.get("style")))
    return style


def style_number(style: dict[str, str], key: str, default: float = 0.0) -> float:
    from crab_archi_design.svg.transform import parse_numbers

    numbers = parse_numbers(style.get(key, ""))
    return numbers[0] if numbers else default
