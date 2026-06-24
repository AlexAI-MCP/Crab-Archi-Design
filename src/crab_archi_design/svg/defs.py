from __future__ import annotations

from xml.etree.ElementTree import Element

from crab_archi_design.svg.namespace import local_name
from crab_archi_design.svg.transform import Matrix, multiply_matrix, parse_numbers


def collect_id_index(root: Element) -> dict[str, Element]:
    index: dict[str, Element] = {}
    for element in root.iter():
        element_id = element.attrib.get("id")
        if element_id and element_id not in index:
            index[element_id] = element
    return index


def href_value(element: Element) -> str | None:
    for key, value in element.attrib.items():
        if key == "href" or key == "xlink:href" or key.endswith("}href"):
            return value
    return None


def referenced_element(element: Element, index: dict[str, Element]) -> tuple[Element | None, str | None]:
    href = href_value(element)
    if not href:
        return None, "use_missing_href"
    if not href.startswith("#"):
        return None, "use_external_href_not_supported"
    target = index.get(href[1:])
    if target is None:
        return None, "use_reference_not_found"
    return target, None


def use_instance_matrix(use_element: Element, referenced: Element) -> Matrix:
    matrix = translation_matrix(number_attr(use_element, "x", 0.0), number_attr(use_element, "y", 0.0))
    symbol_fit = symbol_viewbox_matrix(use_element, referenced)
    if symbol_fit is not None:
        matrix = multiply_matrix(matrix, symbol_fit)
    return matrix


def translation_matrix(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, x, y)


def scale_matrix(x: float, y: float) -> Matrix:
    return (x, 0.0, 0.0, y, 0.0, 0.0)


def number_attr(element: Element, name: str, default: float) -> float:
    values = parse_numbers(element.attrib.get(name, ""))
    return values[0] if values else default


def symbol_viewbox_matrix(use_element: Element, referenced: Element) -> Matrix | None:
    if local_name(referenced.tag) != "symbol":
        return None
    viewbox_values = parse_numbers(referenced.attrib.get("viewBox", ""))
    if len(viewbox_values) != 4:
        return None
    min_x, min_y, width, height = viewbox_values
    if abs(width) < 1e-12 or abs(height) < 1e-12:
        return None
    use_width_values = parse_numbers(use_element.attrib.get("width", ""))
    use_height_values = parse_numbers(use_element.attrib.get("height", ""))
    if not use_width_values or not use_height_values:
        return None
    fit = scale_matrix(use_width_values[0] / width, use_height_values[0] / height)
    return multiply_matrix(fit, translation_matrix(-min_x, -min_y))
