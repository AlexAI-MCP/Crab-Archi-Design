from __future__ import annotations

import pytest

from crab_archi_design.canvas_document import CanvasDocument, CanvasError
from crab_archi_design.web_runtime import BrowserSession

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><rect x="10" y="10" width="30" height="20"/><path d="M0 0L100 0"/></svg>'


def test_compound_failure_restores_geometry_history_and_revision():
    doc = CanvasDocument()
    doc.load_text(SVG)
    cid = doc.list_elements()[0]["cid"]
    doc.apply("set_style", {"cids": [cid], "style": {"fill": "red"}})
    doc.undo()
    before, status = doc.to_string(), doc.status()
    with pytest.raises(CanvasError):
        doc.apply("set_style", {"cids": [cid, "missing"], "style": {"fill": "blue"}})
    assert doc.to_string() == before
    assert doc.status() == status
    doc.redo()
    assert doc.find(cid).get("fill") == "red"


@pytest.mark.parametrize("scale", [0, -1, float("inf"), float("nan")])
def test_scale_rejects_invalid_values_without_mutation(scale):
    doc = CanvasDocument()
    doc.load_text(SVG)
    before = doc.to_string()
    with pytest.raises(CanvasError):
        doc.apply("set_scale", {"mm_per_unit": scale})
    assert doc.to_string() == before


def test_imported_ids_remain_unique_when_missing_id_precedes_existing():
    doc = CanvasDocument()
    doc.load_text('<svg><rect/><rect data-cid="e0001"/><rect data-cid="e0001"/></svg>')
    ids = [item["cid"] for item in doc.list_elements()]
    assert len(set(ids)) == 3
    assert ids[1] == "e0001"


def test_browser_sessions_edit_independently_and_export_intent():
    a, b = BrowserSession(), BrowserSession()
    for session in (a, b):
        assert session.request('/api/open-text', {"svg": SVG, "name": "plan.svg"})["status"] == "ok"
    cid = a.document.list_elements()[0]["cid"]
    result = a.request('/api/op', {"op": "move_element", "params": {"cid": cid, "dx": 5, "dy": 0}})
    assert result["status"] == "ok"
    assert a.document.find(cid).get("x") == "15"
    assert b.document.find(cid).get("x") == "10"
    a.request('/api/undo')
    assert a.document.find(cid).get("x") == "10"
    a.request('/api/redo')
    a.request('/api/op', {"op": "set_scale", "params": {"mm_per_unit": 30}})
    a.request('/api/message', {"text": "Merge lounge and library"})
    handoff = a.request('/api/design-request')["request"]
    assert handoff["messages"][0]["text"] == "Merge lounge and library"
    assert handoff["scale_mm_per_world"] == 30
    assert handoff["opencrab_evidence_required"] is True
    assert handoff["agent_connected"] is False
    assert handoff["design_validation"] == "not_run"
    assert handoff["source_sha256"] != handoff["edited_svg_sha256"]
    assert 'x="15"' in a.request('/api/save')["svg"]


@pytest.mark.parametrize("element", [
    '<script>alert(1)</script>', '<rect onload="alert(1)"/>',
    '<foreignObject><iframe/></foreignObject>', '<use href="https://example.org/a.svg"/>',
    '<rect style="fill:url(https://example.org)"/>',
    '<style>body {display:none}</style>', '<set attributeName="onload" to="alert(1)"/>',
])
def test_browser_rejects_active_svg_without_replacing_current_document(element):
    session = BrowserSession()
    session.request('/api/open-text', {"svg": SVG})
    before = session.document.to_string()
    result = session.request('/api/open-text', {"svg": '<svg>' + element + '</svg>'})
    assert result["status"] == "error"
    assert session.document.to_string() == before


def test_browser_rejects_active_attribute_edits_and_local_file_routes():
    session = BrowserSession()
    session.request('/api/open-text', {"svg": SVG})
    cid = session.document.list_elements()[0]["cid"]
    assert session.request('/api/op', {"op": "set_attrs", "params": {
        "cid": cid, "attrs": {"onload": "alert(1)"}}})["status"] == "error"
    for route in ('/api/open', '/api/regenerate', '/api/export'):
        assert session.request(route, {"path": "/etc/passwd"})["status"] == "error"


def test_browser_preserves_internal_svg_definitions():
    session = BrowserSession()
    svg = '<svg><defs><pattern id="p" width="1" height="1"/></defs><rect fill="url(#p)"/></svg>'
    assert session.request('/api/open-text', {"svg": svg})["status"] == "ok"


@pytest.mark.parametrize("op,extra", [("delete_elements", {}), ("move_element", {"dx": 100, "dy": 0})])
def test_protected_existing_geometry_cannot_be_deleted_or_moved_out(op, extra):
    doc = CanvasDocument()
    doc.load_text(SVG)
    cid = doc.list_elements()[0]["cid"]
    doc.apply("set_zone", {"mode": "protect_zone", "points": [[9, 9], [41, 9], [41, 31], [9, 31]]})
    before = doc.to_string()
    params = {"cids": [cid]} if op == "delete_elements" else {"cid": cid, **extra}
    with pytest.raises(CanvasError, match="protected zone"):
        doc.apply(op, params)
    assert doc.to_string() == before


def test_small_protected_zone_inside_large_new_element_is_not_missed():
    doc = CanvasDocument()
    doc.load_text(SVG)
    doc.apply("set_zone", {"mode": "no_go_zone", "points": [[20, 20], [22, 20], [22, 22], [20, 22]]})
    with pytest.raises(CanvasError, match="protected zone"):
        doc.apply("draw_rect", {"x": 0, "y": 0, "width": 100, "height": 100})


def test_restored_session_keeps_original_fingerprint_and_request_selection():
    a = BrowserSession()
    a.request('/api/open-text', {"svg": SVG, "name": "plan.svg"})
    a.request('/api/session/update', {"selection": ["e0001"], "viewport": [0, 0, 200, 100]})
    a.request('/api/message', {"text": "Move selected wall"})
    a.request('/api/op', {"op": "move_element", "params": {"cid": "e0001", "dx": 5, "dy": 0}})
    handoff = a.request('/api/design-request')["request"]
    b = BrowserSession()
    result = b.request('/api/restore', {"svg": a.document.to_string(), "name": "plan.svg",
                       "source_sha256": handoff["source_sha256"], "messages": handoff["messages"]})
    assert result["status"] == "ok"
    restored = b.request('/api/design-request')["request"]
    assert restored["source_sha256"] == handoff["source_sha256"]
    assert restored["edited_svg_sha256"] == handoff["edited_svg_sha256"]
    assert restored["messages"] == handoff["messages"]
