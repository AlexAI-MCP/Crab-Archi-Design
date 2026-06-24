from __future__ import annotations

from pathlib import Path
import re
from xml.etree.ElementTree import Element, ElementTree

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DTDForbidden


class SvgLoadError(ValueError):
    pass


def safe_load_svg(path: Path, max_bytes: int = 64 * 1024 * 1024, max_nodes: int = 250_000) -> Element:
    if not path.exists():
        raise SvgLoadError(f"Missing SVG: {path}")
    if path.stat().st_size > max_bytes:
        raise SvgLoadError(f"SVG exceeds max_bytes={max_bytes}: {path}")
    try:
        tree: ElementTree = DefusedElementTree.parse(path, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DTDForbidden:
        text = strip_doctype(path.read_text(encoding="utf-8", errors="replace"))
        root = DefusedElementTree.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        guard_node_count(root, max_nodes)
        return root
    except Exception as exc:  # defusedxml raises several parse/security subclasses.
        raise SvgLoadError(f"Unable to safely parse SVG: {exc}") from exc
    root = tree.getroot()
    guard_node_count(root, max_nodes)
    return root


def strip_doctype(text: str) -> str:
    return re.sub(r"<!DOCTYPE[^>]*(?:\[[\s\S]*?\]\s*)?>", "", text, count=1, flags=re.IGNORECASE)


def guard_node_count(root: Element, max_nodes: int) -> None:
    node_count = sum(1 for _ in root.iter())
    if node_count > max_nodes:
        raise SvgLoadError(f"SVG node count exceeds max_nodes={max_nodes}: {node_count}")
