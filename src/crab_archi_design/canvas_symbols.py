"""Multi-discipline parametric symbol library for the live canvas.

Each symbol is built from SVG primitives in a local coordinate frame centered
at the origin with a nominal footprint of ~10 units, then placed with
translate/rotate/scale. Disciplines follow Korean drawing conventions closely
enough to read correctly on a plan.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Callable

SVG_NS = "http://www.w3.org/2000/svg"


def q(tag: str) -> str:
    return f"{{{SVG_NS}}}{tag}"


def _el(parent: ET.Element, tag: str, **attrs: str) -> ET.Element:
    return ET.SubElement(parent, q(tag), {k.replace("_", "-"): str(v) for k, v in attrs.items()})


K = "#111111"  # default ink


# ---- builders (local frame, ~10u nominal) -----------------------------------

def _tree_deciduous(g: ET.Element) -> None:      # 교목: 원 + 십자 가지
    _el(g, "circle", cx=0, cy=0, r=6, stroke=K, fill="none", stroke_width=1.2)
    for x1, y1, x2, y2 in ((-4, -4, 4, 4), (-4, 4, 4, -4)):
        _el(g, "line", x1=x1, y1=y1, x2=x2, y2=y2, stroke=K, stroke_width=0.8)
    _el(g, "circle", cx=0, cy=0, r=0.8, fill=K, stroke="none")


def _tree_conifer(g: ET.Element) -> None:        # 침엽수: 톱니 원
    import math
    pts = []
    for i in range(16):
        a = math.pi * 2 * i / 16
        r = 6 if i % 2 == 0 else 4
        pts.append(f"{r*math.cos(a):.2f},{r*math.sin(a):.2f}")
    _el(g, "polygon", points=" ".join(pts), stroke=K, fill="none", stroke_width=1)
    _el(g, "circle", cx=0, cy=0, r=0.8, fill=K, stroke="none")


def _shrub(g: ET.Element) -> None:               # 관목: 물결 원
    _el(g, "circle", cx=0, cy=0, r=3.5, stroke=K, fill="none",
        stroke_width=1, stroke_dasharray="1.5 1")


def _bench(g: ET.Element) -> None:               # 벤치
    _el(g, "rect", x=-5, y=-1.5, width=10, height=3, stroke=K, fill="none", stroke_width=1)
    _el(g, "line", x1=-5, y1=0, x2=5, y2=0, stroke=K, stroke_width=0.6)


def _door(g: ET.Element) -> None:                # 문: 개구부 + 90° 호
    _el(g, "line", x1=0, y1=0, x2=0, y2=-9, stroke=K, stroke_width=1.4)
    _el(g, "path", d="M 0 -9 A 9 9 0 0 1 9 0", stroke=K, fill="none", stroke_width=0.8)


def _column(g: ET.Element) -> None:              # 기둥: 채움 사각
    _el(g, "rect", x=-4, y=-4, width=8, height=8, fill=K, stroke="none")


def _stair_arrow(g: ET.Element) -> None:         # 계단 진행 화살표
    _el(g, "line", x1=-8, y1=0, x2=8, y2=0, stroke=K, stroke_width=1.2)
    _el(g, "polygon", points="8,0 4,-2.5 4,2.5", fill=K, stroke="none")


def _light_ceiling(g: ET.Element) -> None:       # 천장 조명: ○ + ×
    _el(g, "circle", cx=0, cy=0, r=4, stroke=K, fill="none", stroke_width=1.2)
    for x1, y1, x2, y2 in ((-2.8, -2.8, 2.8, 2.8), (-2.8, 2.8, 2.8, -2.8)):
        _el(g, "line", x1=x1, y1=y1, x2=x2, y2=y2, stroke=K, stroke_width=1)


def _outlet(g: ET.Element) -> None:              # 콘센트: 반원 + 2선
    _el(g, "path", d="M -4 0 A 4 4 0 0 1 4 0", stroke=K, fill="none", stroke_width=1.2)
    _el(g, "line", x1=-4, y1=0, x2=4, y2=0, stroke=K, stroke_width=1.2)
    _el(g, "line", x1=0, y1=0, x2=0, y2=4, stroke=K, stroke_width=1)


def _switch(g: ET.Element) -> None:              # 스위치
    _el(g, "circle", cx=0, cy=0, r=2, stroke=K, fill="none", stroke_width=1.1)
    _el(g, "line", x1=1.4, y1=-1.4, x2=5, y2=-5, stroke=K, stroke_width=1.1)


def _panel(g: ET.Element) -> None:               # 분전반
    _el(g, "rect", x=-5, y=-3, width=10, height=6, stroke=K, fill="none", stroke_width=1.3)
    _el(g, "line", x1=-5, y1=-3, x2=5, y2=3, stroke=K, stroke_width=0.8)


def _diffuser_supply(g: ET.Element) -> None:     # 급기 디퓨저: □+대각 4
    _el(g, "rect", x=-5, y=-5, width=10, height=10, stroke=K, fill="none", stroke_width=1.1)
    for x2, y2 in ((5, 5), (-5, 5), (5, -5), (-5, -5)):
        _el(g, "line", x1=0, y1=0, x2=x2, y2=y2, stroke=K, stroke_width=0.7)


def _diffuser_return(g: ET.Element) -> None:     # 배기: □+십자
    _el(g, "rect", x=-5, y=-5, width=10, height=10, stroke=K, fill="none", stroke_width=1.1)
    _el(g, "line", x1=-5, y1=0, x2=5, y2=0, stroke=K, stroke_width=0.7)
    _el(g, "line", x1=0, y1=-5, x2=0, y2=5, stroke=K, stroke_width=0.7)


def _fcu(g: ET.Element) -> None:                 # 팬코일
    _el(g, "rect", x=-7, y=-4, width=14, height=8, stroke=K, fill="none", stroke_width=1.2)
    _el(g, "text", x=-4.5, y=2, font_size="5", fill=K).text = "FCU"


def _valve(g: ET.Element) -> None:               # 밸브: 나비
    _el(g, "polygon", points="-5,-3 0,0 -5,3", stroke=K, fill="none", stroke_width=1)
    _el(g, "polygon", points="5,-3 0,0 5,3", stroke=K, fill="none", stroke_width=1)


def _pipe_riser(g: ET.Element) -> None:          # 입상관
    _el(g, "circle", cx=0, cy=0, r=3.5, stroke=K, fill="none", stroke_width=1.2)
    _el(g, "line", x1=-2.5, y1=2.5, x2=2.5, y2=-2.5, stroke=K, stroke_width=1)


def _sprinkler(g: ET.Element) -> None:           # 스프링클러: ⊙
    _el(g, "circle", cx=0, cy=0, r=3, stroke=K, fill="none", stroke_width=1.1)
    _el(g, "circle", cx=0, cy=0, r=0.9, fill=K, stroke="none")


def _detector(g: ET.Element) -> None:            # 감지기: ○ 안 S
    _el(g, "circle", cx=0, cy=0, r=3.5, stroke=K, fill="none", stroke_width=1.1)
    _el(g, "text", x=-1.8, y=2, font_size="5", fill=K).text = "S"


def _extinguisher(g: ET.Element) -> None:        # 소화기: △
    _el(g, "polygon", points="0,-4.5 4,3.5 -4,3.5", stroke=K, fill="none", stroke_width=1.2)


def _manhole(g: ET.Element) -> None:             # 맨홀: ○ 안 사각
    _el(g, "circle", cx=0, cy=0, r=5, stroke=K, fill="none", stroke_width=1.2)
    _el(g, "rect", x=-2.5, y=-2.5, width=5, height=5, stroke=K, fill="none", stroke_width=0.9)


def _catch_basin(g: ET.Element) -> None:         # 우수받이: □ 격자
    _el(g, "rect", x=-4, y=-4, width=8, height=8, stroke=K, fill="none", stroke_width=1.1)
    for off in (-2, 0, 2):
        _el(g, "line", x1=off, y1=-4, x2=off, y2=4, stroke=K, stroke_width=0.6)


def _slope_arrow(g: ET.Element) -> None:         # 구배 화살표
    _el(g, "line", x1=-9, y1=0, x2=9, y2=0, stroke=K, stroke_width=1)
    _el(g, "polygon", points="9,0 5,-2 5,2", fill=K, stroke="none")
    _el(g, "text", x=-8, y=-2, font_size="4", fill=K).text = "SL"


def _boundary_stake(g: ET.Element) -> None:      # 경계점
    _el(g, "circle", cx=0, cy=0, r=2, stroke=K, fill="none", stroke_width=1)
    _el(g, "line", x1=0, y1=-4, x2=0, y2=4, stroke=K, stroke_width=0.8)
    _el(g, "line", x1=-4, y1=0, x2=4, y2=0, stroke=K, stroke_width=0.8)


SYMBOLS: dict[str, dict[str, object]] = {
    # 건축 (arch)
    "door":            {"discipline": "arch", "ko": "문(90° 스윙)", "build": _door},
    "column":          {"discipline": "arch", "ko": "기둥", "build": _column},
    "stair_arrow":     {"discipline": "arch", "ko": "계단 진행 화살표", "build": _stair_arrow},
    # 조경 (landscape)
    "tree_deciduous":  {"discipline": "landscape", "ko": "낙엽교목", "build": _tree_deciduous},
    "tree_conifer":    {"discipline": "landscape", "ko": "침엽교목", "build": _tree_conifer},
    "shrub":           {"discipline": "landscape", "ko": "관목", "build": _shrub},
    "bench":           {"discipline": "landscape", "ko": "벤치", "build": _bench},
    # 전기 (electrical)
    "light_ceiling":   {"discipline": "electrical", "ko": "천장 조명", "build": _light_ceiling},
    "outlet":          {"discipline": "electrical", "ko": "콘센트", "build": _outlet},
    "switch":          {"discipline": "electrical", "ko": "스위치", "build": _switch},
    "panel":           {"discipline": "electrical", "ko": "분전반", "build": _panel},
    # 기계 (mechanical)
    "diffuser_supply": {"discipline": "mechanical", "ko": "급기 디퓨저", "build": _diffuser_supply},
    "diffuser_return": {"discipline": "mechanical", "ko": "배기 디퓨저", "build": _diffuser_return},
    "fcu":             {"discipline": "mechanical", "ko": "팬코일유닛", "build": _fcu},
    "valve":           {"discipline": "mechanical", "ko": "밸브", "build": _valve},
    "pipe_riser":      {"discipline": "mechanical", "ko": "입상관", "build": _pipe_riser},
    # 소방 (fire)
    "sprinkler":       {"discipline": "fire", "ko": "스프링클러 헤드", "build": _sprinkler},
    "detector":        {"discipline": "fire", "ko": "화재 감지기", "build": _detector},
    "extinguisher":    {"discipline": "fire", "ko": "소화기", "build": _extinguisher},
    # 토목 (civil)
    "manhole":         {"discipline": "civil", "ko": "맨홀", "build": _manhole},
    "catch_basin":     {"discipline": "civil", "ko": "우수받이", "build": _catch_basin},
    "slope_arrow":     {"discipline": "civil", "ko": "구배 화살표", "build": _slope_arrow},
    "boundary_stake":  {"discipline": "civil", "ko": "경계점", "build": _boundary_stake},
}

DISCIPLINES = sorted({str(s["discipline"]) for s in SYMBOLS.values()})


def catalog() -> list[dict[str, str]]:
    return [{"name": name, "discipline": str(spec["discipline"]), "ko": str(spec["ko"])}
            for name, spec in SYMBOLS.items()]


def build_symbol(name: str) -> tuple[ET.Element, str]:
    """Return a detached <g> holding the symbol primitives, plus its discipline."""
    spec = SYMBOLS.get(name)
    if spec is None:
        raise KeyError(name)
    g = ET.Element(q("g"))
    builder: Callable[[ET.Element], None] = spec["build"]  # type: ignore[assignment]
    builder(g)
    return g, str(spec["discipline"])
