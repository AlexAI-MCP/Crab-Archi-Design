"""Tests for the live canvas document, HTTP server, and MCP bridge."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from crab_archi_design.canvas_document import CanvasDocument, CanvasError
from crab_archi_design.canvas_mcp import CanvasClient, handle_request
from crab_archi_design.canvas_server import CanvasState, make_handler
from http.server import ThreadingHTTPServer

SAMPLE_SVG = """<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 400 300\">
  <rect id=\"wall\" x=\"10\" y=\"10\" width=\"100\" height=\"80\" stroke=\"#333\" fill=\"none\"/>
  <line x1=\"10\" y1=\"120\" x2=\"110\" y2=\"120\" stroke=\"#333\" stroke-width=\"4\"/>
  <text x=\"20\" y=\"50\" font-size=\"12\">LOUNGE</text>
</svg>"""


@pytest.fixture
def doc() -> CanvasDocument:
    document = CanvasDocument()
    document.load_text(SAMPLE_SVG)
    return document


def test_load_assigns_cids_and_status(doc: CanvasDocument) -> None:
    status = doc.status()
    assert status["loaded"] and status["element_count"] == 3
    elements = doc.list_elements()
    assert len({e["cid"] for e in elements}) == 3
    text = next(e for e in elements if e["tag"] == "text")
    assert text["text"] == "LOUNGE"


def test_draw_delete_undo_cycle(doc: CanvasDocument) -> None:
    result = doc.apply("draw_line", {"x1": 0, "y1": 0, "x2": 50, "y2": 50})
    cid = result["cid"]
    assert any(e["cid"] == cid for e in doc.list_elements())
    doc.apply("delete_elements", {"cids": [cid]})
    assert not any(e["cid"] == cid for e in doc.list_elements())
    doc.undo()  # restore line
    assert any(e["cid"] == cid for e in doc.list_elements())


def test_draw_curve_and_polyline(doc: CanvasDocument) -> None:
    curve = doc.apply("draw_curve", {"points": [[0, 0], [50, 80], [100, 0]]})
    assert doc.find(curve["cid"]).get("d", "").startswith("M")
    closed = doc.apply("draw_polyline", {"points": [[0, 0], [10, 0], [10, 10]], "closed": True})
    assert doc.describe(doc.find(closed["cid"]))["tag"] == "polygon"
    with pytest.raises(CanvasError):
        doc.apply("draw_curve", {"points": [[0, 0], [1, 1]]})


def test_copy_element_and_style(doc: CanvasDocument) -> None:
    rect_cid = next(e["cid"] for e in doc.list_elements() if e["tag"] == "rect")
    line_cid = next(e["cid"] for e in doc.list_elements() if e["tag"] == "line")
    clone = doc.apply("copy_element", {"cid": rect_cid, "dx": 20, "dy": 30})
    assert clone["cid"] != rect_cid
    assert "translate(20,30)" in doc.find(clone["cid"]).get("transform", "")
    doc.apply("copy_style", {"source_cid": line_cid, "target_cids": [rect_cid]})
    assert doc.find(rect_cid).get("stroke-width") == "4"


def test_zones_and_strokes(doc: CanvasDocument) -> None:
    doc.apply("set_zone", {"mode": "mutable_zone", "points": [[0, 0], [100, 0], [100, 100]]})
    with pytest.raises(CanvasError):
        doc.apply("set_zone", {"mode": "bogus", "points": [[0, 0], [1, 0], [1, 1]]})
    zones = doc.list_zones()
    assert zones and zones[0]["mode"] == "mutable_zone"
    strokes = doc.zones_as_strokes()
    assert strokes[0]["points"][0] == strokes[0]["points"][-1]  # auto-closed
    assert doc.apply("clear_zones", {})["cleared"] == 1


def test_set_attrs_and_move(doc: CanvasDocument) -> None:
    line_cid = next(e["cid"] for e in doc.list_elements() if e["tag"] == "line")
    doc.apply("set_attrs", {"cid": line_cid, "attrs": {"x2": "200", "stroke": None}})
    element = doc.find(line_cid)
    assert element.get("x2") == "200" and element.get("stroke") is None
    doc.apply("move_element", {"cid": line_cid, "dx": 5, "dy": -5})
    assert "translate(5,-5)" in element.get("transform", "")


def test_save_and_reload_roundtrip(doc: CanvasDocument, tmp_path: Path) -> None:
    doc.apply("draw_rect", {"x": 0, "y": 0, "width": 10, "height": 10})
    saved = doc.save(str(tmp_path / "out.svg"))
    reloaded = CanvasDocument()
    reloaded.load(saved)
    assert reloaded.status()["element_count"] == doc.status()["element_count"]


@pytest.fixture
def server(tmp_path: Path):
    state = CanvasState(project_root=tmp_path / "projects")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    svg_path = tmp_path / "sample.svg"
    svg_path.write_text(SAMPLE_SVG, encoding="utf-8")
    yield f"http://127.0.0.1:{httpd.server_address[1]}", svg_path
    httpd.shutdown()


def http_json(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def test_http_open_op_doc_flow(server) -> None:
    base, svg_path = server
    health = http_json(base + "/api/health")
    assert health["status"] == "ok" and not health["loaded"]
    opened = http_json(base + "/api/open", {"path": str(svg_path)})
    assert opened["status"] == "ok" and opened["element_count"] == 3
    result = http_json(base + "/api/op", {"op": "draw_line", "params": {"x1": 0, "y1": 0, "x2": 9, "y2": 9}})
    assert result["status"] == "ok" and result["cid"]
    doc = http_json(base + f"/api/doc?rev=-1")
    # 3 originals + the crab_drawn layer group + the new line
    assert doc["status"] == "ok" and "svg" in doc and doc["element_count"] == 5
    unchanged = http_json(base + f"/api/doc?rev={doc['revision']}")
    assert unchanged["status"] == "unchanged"


def test_http_rejects_unknown_op(server) -> None:
    base, svg_path = server
    http_json(base + "/api/open", {"path": str(svg_path)})
    try:
        http_json(base + "/api/op", {"op": "shutil_rmtree", "params": {}})
        raise AssertionError("expected HTTP 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_mcp_bridge_tools_list_and_call(server) -> None:
    base, svg_path = server
    client = CanvasClient(base)
    listed = handle_request(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    assert {"open_svg", "list_elements", "draw_line", "set_zone", "copy_style",
            "delete_elements", "regenerate_layout", "undo"} <= set(names)
    called = handle_request(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                     "params": {"name": "open_svg", "arguments": {"path": str(svg_path)}}})
    assert not called["result"]["isError"]
    drawn = handle_request(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": "draw_rect",
                                               "arguments": {"x": 1, "y": 1, "width": 5, "height": 5}}})
    assert drawn["result"]["structuredContent"]["cid"]
    elements = handle_request(client, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                       "params": {"name": "list_elements", "arguments": {"tag": "rect"}}})
    assert len(elements["result"]["structuredContent"]["elements"]) == 2


def test_mcp_bridge_unreachable_server_is_soft_error() -> None:
    client = CanvasClient("http://127.0.0.1:1")
    result = handle_request(client, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                     "params": {"name": "canvas_status", "arguments": {}}})
    assert result["result"]["isError"]
    assert "unreachable" in result["result"]["structuredContent"]["error"]


def test_clean_path_unescapes_shell_paste() -> None:
    from crab_archi_design.canvas_server import clean_path
    assert clean_path(r"/a/A-801\~802\ 도면_Model.svg") == "/a/A-801~802 도면_Model.svg"
    assert clean_path("'/a/plain path.svg'") == "/a/plain path.svg"
    assert clean_path("/a/normal.svg") == "/a/normal.svg"
