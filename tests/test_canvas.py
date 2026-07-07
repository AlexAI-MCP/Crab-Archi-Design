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
    # 이동은 transform을 쌓지 않고 좌표에 직접 반영(bake)된다
    assert element.get("transform") is None
    assert element.get("x1") == "15" and element.get("y1") == "115"


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


def test_list_elements_drawn_only(doc: CanvasDocument) -> None:
    doc.apply("draw_line", {"x1": 0, "y1": 0, "x2": 5, "y2": 5})
    doc.apply("set_zone", {"mode": "mutable_zone", "points": [[0, 0], [9, 0], [9, 9]]})
    drawn = doc.list_elements(drawn_only=True)
    assert {e["tag"] for e in drawn} == {"line", "polygon"}
    assert len(doc.list_elements()) > len(drawn)  # 원본 요소는 제외됨


ROOM_SVG = """<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 300 300\">
  <line x1=\"50\" y1=\"50\" x2=\"250\" y2=\"50\" stroke=\"black\" stroke-width=\"2\"/>
  <line x1=\"250\" y1=\"50\" x2=\"250\" y2=\"200\" stroke=\"black\" stroke-width=\"2\"/>
  <line x1=\"250\" y1=\"200\" x2=\"50\" y2=\"200\" stroke=\"black\" stroke-width=\"2\"/>
  <line x1=\"50\" y1=\"200\" x2=\"50\" y2=\"50\" stroke=\"black\" stroke-width=\"2\"/>
  <text x=\"120\" y=\"120\" font-size=\"10\">ROOM</text>
</svg>"""


def test_recognize_room_area_and_scale() -> None:
    document = CanvasDocument()
    document.load_text(ROOM_SVG)
    document.apply("set_scale", {"mm_per_unit": 100})  # 1 unit = 0.1m
    room = document.recognize_room(150, 120, cell=2, max_span=200)
    assert room["enclosed"]
    # 200x150 units = 20m x 15m = 300 m²(±격자 오차)
    assert abs(room["area_m2"] - 300) < 25
    assert room["area_pyeong"] > 80
    rooms = document.recognize_rooms(cell=2, max_span=200)
    assert rooms and rooms[0]["label"] == "ROOM" and rooms[0]["enclosed"]


def test_set_scale_calibration() -> None:
    document = CanvasDocument()
    document.load_text(ROOM_SVG)
    result = document.apply("set_scale", {"known_mm": 2500, "p1": [0, 0], "p2": [25, 0]})
    assert abs(result["mm_per_unit"] - 100) < 1e-6
    assert document.status()["mm_per_unit"] == 100


def test_stretch_extends_walls() -> None:
    document = CanvasDocument()
    document.load_text(ROOM_SVG)
    # 방 하부(y150~230)를 아래로 40 늘리기: 세로벽 연장 + 하부벽 이동
    result = document.apply("stretch", {"box": [40, 150, 260, 230], "dx": 0, "dy": 40})
    assert result["stretched_count"] == 3  # 좌우 세로벽 하단점 2 + 하부벽 1
    room = document.recognize_room(150, 120, cell=2, max_span=250)
    assert abs((room["bbox"][3] - room["bbox"][1]) - 190) < 8  # 150 → 190


def test_no_go_zone_blocks_and_force_overrides() -> None:
    document = CanvasDocument()
    document.load_text(ROOM_SVG)
    document.apply("set_zone", {"mode": "no_go_zone", "points": [[0, 0], [40, 0], [40, 300], [0, 300]], "name": "램프"})
    with pytest.raises(CanvasError, match="램프"):
        document.apply("draw_line", {"x1": 10, "y1": 10, "x2": 30, "y2": 30})
    ok = document.apply("draw_line", {"x1": 10, "y1": 10, "x2": 30, "y2": 30, "force": True})
    assert ok["cid"]
    with pytest.raises(CanvasError):
        document.apply("stretch", {"box": [45, 40, 60, 210], "dx": -20, "dy": 0})


def test_draft_rollback_restores_bulk_delete(doc: CanvasDocument) -> None:
    before = doc.status()["element_count"]
    doc.draft_begin()
    cids = [e["cid"] for e in doc.list_elements()]
    doc.apply("delete_elements", {"cids": cids})
    assert doc.status()["element_count"] < before
    doc.draft_rollback()
    assert doc.status()["element_count"] == before
    with pytest.raises(CanvasError):
        doc.draft_commit()  # 이미 닫힘


def test_autosave_journal(tmp_path: Path) -> None:
    document = CanvasDocument()
    document.autosave_path = tmp_path / "auto.svg"
    document.load_text(ROOM_SVG)
    document.apply("draw_line", {"x1": 0, "y1": 0, "x2": 5, "y2": 5})
    assert document.autosave_path.exists()
    restored = CanvasDocument()
    restored.load_text(document.autosave_path.read_text(encoding="utf-8"))
    assert restored.status()["element_count"] == document.status()["element_count"]


def test_layers_and_symbols(doc: CanvasDocument) -> None:
    from crab_archi_design.canvas_symbols import SYMBOLS, catalog
    assert len(catalog()) == len(SYMBOLS) >= 20
    tree = doc.apply("place_symbol", {"name": "tree_deciduous", "x": 50, "y": 50, "label": "느티나무"})
    assert tree["discipline"] == "landscape" and tree["layer"] == "crab_layer_landscape"
    doc.apply("place_symbol", {"name": "sprinkler", "x": 60, "y": 60})
    doc.apply("draw_line", {"x1": 0, "y1": 0, "x2": 9, "y2": 9, "layer": "electrical"})
    layers = {l["id"]: l["elements"] for l in doc.list_layers()}
    assert "crab_layer_landscape" in layers and "crab_layer_fire" in layers
    only_elec = doc.list_elements(layer="electrical")
    assert len(only_elec) == 1 and only_elec[0]["tag"] == "line"
    drawn = doc.list_elements(drawn_only=True)
    assert any(e["tag"] == "g" or e["cid"] == tree["cid"] for e in drawn)
    with pytest.raises(CanvasError, match="unknown symbol"):
        doc.apply("place_symbol", {"name": "ufo", "x": 0, "y": 0})


def test_symbol_respects_no_go(doc: CanvasDocument) -> None:
    doc.apply("set_zone", {"mode": "no_go_zone", "points": [[0, 0], [100, 0], [100, 100], [0, 100]], "name": "보호"})
    with pytest.raises(CanvasError, match="보호"):
        doc.apply("place_symbol", {"name": "manhole", "x": 50, "y": 50})


def test_bidirectional_session_channel(server) -> None:
    base, svg_path = server
    http_json(base + "/api/open", {"path": str(svg_path)})
    line = http_json(base + "/api/op", {"op": "draw_line", "params": {"x1": 0, "y1": 0, "x2": 9, "y2": 9}})
    # 브라우저 → 선택/뷰포트 보고
    http_json(base + "/api/session/update", {"selection": [line["cid"]], "viewport": [0, 0, 100, 80]})
    # 브라우저 → 사용자 메시지
    http_json(base + "/api/message", {"text": "이 선을 벽으로 바꿔줘"})
    # 에이전트 → 세션 조회 (선택 해석 + 메시지 drain)
    session = http_json(base + "/api/session?drain=1")
    assert session["viewport"] == [0, 0, 100, 80]
    assert session["selection"][0]["cid"] == line["cid"] and session["selection"][0]["tag"] == "line"
    assert session["user_messages"][0]["text"] == "이 선을 벽으로 바꿔줘"
    assert session["user_messages"][0]["selection"] == [line["cid"]]  # 메시지에 당시 선택 동봉
    assert http_json(base + "/api/session?drain=1")["user_messages"] == []  # drained
    # 에이전트 → 알림 + 포인터
    http_json(base + "/api/notify", {"text": "여기를 보세요", "pointer": [50, 40]})
    notices = http_json(base + "/api/session/browser?since=0")
    assert notices["notices"][0]["text"] == "여기를 보세요"
    assert notices["notices"][0]["pointer"] == [50, 40]
    assert http_json(base + "/api/session/browser?since=" + str(notices["seq"]))["notices"] == []


# ---- SVG 친화 개선: path 스트레치, transform 인식, style 병합, bake, measure ----

def test_stretch_moves_path_points() -> None:
    doc = CanvasDocument()
    doc.load_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                  '<path d="M 10 10 L 100 10 l 0 90" fill="none" stroke="#000"/></svg>')
    # 오른쪽 끝점 두 개(100,10)(100,100)만 박스에 넣고 오른쪽으로 20 이동 → 벽이 늘어난다
    doc.apply("stretch", {"box": [90, 0, 120, 200], "dx": 20, "dy": 0})
    element = next(e for e in doc.require_root().iter() if e.tag.endswith("path"))
    pts = sorted((round(x), round(y)) for x, y in
                 __import__("crab_archi_design.canvas_document", fromlist=["path_points"]).path_points(element.get("d")))
    assert pts == [(10, 10), (120, 10), (120, 100)]


def test_stretch_handles_transformed_group() -> None:
    doc = CanvasDocument()
    doc.load_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                  '<g transform="translate(50,0)"><line x1="0" y1="10" x2="100" y2="10" stroke="#000"/></g></svg>')
    # 월드좌표 (150,10) = 로컬 (100,10) 끝점만 박스에 → 스트레치되어야 한다
    doc.apply("stretch", {"box": [140, 0, 160, 20], "dx": 0, "dy": 30})
    line = next(e for e in doc.require_root().iter() if e.tag.endswith("line"))
    assert (line.get("x1"), line.get("y1")) == ("0", "10")
    assert (line.get("x2"), line.get("y2")) == ("100", "40")


def test_path_parse_serialize_relative_arc_hv() -> None:
    from crab_archi_design.canvas_document import parse_path_segments, path_from_segments
    segs = parse_path_segments("m 10 10 h 20 v 5 a 5 5 0 0 1 10 0 c 1 1 2 2 3 3 z")
    ops = [s[0] for s in segs]
    assert ops == ["M", "L", "L", "A", "C", "Z"]
    assert segs[1][1] == [30.0, 10.0] and segs[2][1] == [30.0, 15.0]     # h/v → 절대 L
    assert segs[3][1][5:] == [40.0, 15.0]                                 # 상대 arc 끝점 절대화
    assert "A5 5 0 0 1 40 15" in path_from_segments(segs).replace("  ", " ")


def test_move_element_bakes_coordinates(doc: CanvasDocument) -> None:
    line_cid = next(e["cid"] for e in doc.list_elements(tag="line"))
    result = doc.apply("move_element", {"cid": line_cid, "dx": 5, "dy": -10})
    assert result.get("baked") is True
    element = next(e for e in doc.require_root().iter() if e.get("data-cid") == line_cid)
    assert element.get("transform") is None          # transform이 쌓이지 않는다
    assert element.get("x1") == "15" and element.get("y1") == "110"
    # bake된 요소는 이후 stretch 편집이 그대로 가능
    doc.apply("stretch", {"box": [100, 100, 130, 120], "dx": 7, "dy": 0})
    assert element.get("x2") == "122"


def test_set_style_overrides_inline_css(doc: CanvasDocument) -> None:
    cid = doc.apply("draw_rect", {"x": 200, "y": 200, "width": 20, "height": 10})["cid"]
    doc.apply("set_attrs", {"cid": cid, "attrs": {"style": "fill:red;stroke:blue;opacity:0.5"}})
    doc.apply("set_style", {"cids": [cid], "style": {"fill": "#0f0"}})
    element = next(e for e in doc.require_root().iter() if e.get("data-cid") == cid)
    assert element.get("fill") == "#0f0"
    inline = element.get("style")
    assert "fill" not in inline and "stroke:blue" in inline and "opacity:0.5" in inline


def test_measure_lengths_and_areas() -> None:
    doc = CanvasDocument()
    doc.load_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">'
                  '<line x1="0" y1="0" x2="30" y2="40" stroke="#000"/>'
                  '<rect x="10" y="10" width="20" height="10"/>'
                  '<circle cx="100" cy="100" r="10"/>'
                  '<g transform="scale(2)"><line x1="0" y1="0" x2="50" y2="0" stroke="#000"/></g>'
                  '<path d="M 0 0 L 10 0 L 10 10 L 0 10 Z"/></svg>')
    doc.apply("set_scale", {"mm_per_unit": 100})  # 1 unit = 0.1m
    cids = [e["cid"] for e in doc.list_elements()]
    by_tag = {m["tag"]: m for m in doc.measure(cids) if "error" not in m}
    assert by_tag["line"]["length_m"] == 5.0 or by_tag["line"]["length_units"] == 100.0  # scale(2) line 또는 3-4-5 line
    line_lengths = sorted(m["length_units"] for m in doc.measure(cids) if m["tag"] == "line")
    assert line_lengths == [50.0, 100.0]                       # 3-4-5 → 50, scale(2)·50 → 100 (transform 반영)
    assert by_tag["rect"]["area_m2"] == 2.0                    # 20×10 units → 2×1 m
    assert abs(by_tag["circle"]["area_m2"] - 3.14) < 0.05      # πr², 다각형 근사
    assert by_tag["path"]["closed"] and by_tag["path"]["area_m2"] == 1.0


def test_measure_flattens_curves() -> None:
    import math
    from crab_archi_design.canvas_document import flatten_path
    # 반원 근사 큐빅 베지어: 반지름 50 → 호 길이 ≈ π·50
    subs = flatten_path("M 0 0 C 0 -66.7 100 -66.7 100 0")
    length = sum(math.dist(a, b) for sub in subs for a, b in zip(sub, sub[1:]))
    assert abs(length - math.pi * 50) < 3


def test_http_measure_endpoint(server) -> None:
    base, svg_path = server
    http_json(base + "/api/open", {"path": str(svg_path)})
    cid = http_json(base + "/api/op", {"op": "draw_line", "params": {"x1": 0, "y1": 0, "x2": 60, "y2": 80}})["cid"]
    out = http_json(base + "/api/measure", {"cids": [cid]})
    assert out["measurements"][0]["length_units"] == 100.0
