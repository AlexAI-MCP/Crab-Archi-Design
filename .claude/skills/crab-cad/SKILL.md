---
name: crab-cad
description: Crab-Archi-Design 라이브 SVG 캔버스로 건축 도면을 분석·수정하는 검증된 워크플로우. 도면 로드, 스케일 보정, 방 인식(면적), 기준 대조, 안전한 편집(드래프트-커밋, 불가침 존), 시각 검증까지. 트리거 — 도면/평면도 분석·수정 요청, "캔버스에 띄워줘", "면적 확인", "방 배치 바꿔줘", SVG CAD 작업.
---

# crab-cad — 라이브 SVG CAD 워크플로우

서버가 없으면 먼저 실행: `./canvas.sh 8770 [SVG경로]` (레포 루트). MCP 툴은 `mcp__crab-canvas__*`,
없으면 HTTP API(127.0.0.1:8770)를 curl로 직접 호출해도 동일하다.

## 필수 순서

1. **상태 확인** — `canvas_status`. 사용자가 이미 띄운 도면이 있으면 그걸 쓴다. `open_svg`는 새 파일일 때만.
2. **스케일 보정** — `set_scale`. 우선순위: 타이틀블록 축척 표기 → 주차구획(폭 2.5m=known_mm 2500) →
   기둥 스팬. 보정 없이 면적을 논하지 말 것. 상태의 `mm_per_unit`으로 보정 여부 확인.
3. **인식** — `list_elements(tag="text")`로 실 라벨 파악 → `recognize_rooms`로 방별 면적(m²/평)과
   실치수(`size_m`). 개별 요소의 길이(m)·둘레·면적·벽두께(mm)·방향각은 `measure(cids)` — 스케일 보정
   후에만 미터 값이 나온다. 사용자가 브라우저에서 그린 스케치는 `list_elements(drawn=true)`가 유일한 진입점.
4. **불가침 선언** — 편집 전에 램프·기둥·코어·주차 구역을 `set_zone(no_go_zone/lock_boundary)`으로
   등록한다. 이후 모든 draw/move/stretch가 자동 차단된다 (의도적 예외만 force=true).
5. **편집** — 대량·위험 편집은 반드시 `draft(begin)` → 편집 → `render_view`로 확인 → `commit`
   (문제 시 `rollback`). 벽 연장/방 확장은 삭제 후 재작도가 아니라 `stretch_elements` 사용.
   정확 치수 지시는 전용 도구로: "이 벽 3.5m로" → `set_length(cid, m=3.5, anchor)`,
   "200mm 내력벽으로" → `set_thickness(cids, mm=200)`, "이 존 30평으로" → `set_area(cid, pyeong=30)`.
   적용 후 `measure`로 결과 수치를 되읽어 확인한다.
6. **시각 검증** — 편집 후 `render_view(bbox=작업영역)`으로 눈으로 확인. 스크린샷 없이 완료 선언 금지.
7. **저장·내보내기** — `save_svg(path=원본과 다른 경로)`. 원본은 절대 덮어쓰지 않는다.
   CAD 납품·교환은 `export_cad(path, format)` — 반드시 스케일 보정 후에(그래야 mm 좌표).
   레이어·선굵기·한글 텍스트가 DXF로 넘어간다. format="dwg"는 ODA File Converter가 있을 때만
   실제 DWG가 나오고, 없으면 DXF + 안내를 반환한다 (DXF는 AutoCAD에서 그대로 열림).

## 좌표·선택 요령
- 좌표는 SVG viewBox 단위. bbox 창으로 요소를 고를 때 격자선(회색 #bababa/#767676, 페이지 관통 장선)과
  구조 기둥(굵은 해칭 사각형)을 제외할 것.
- 면적 기준 대조: `/Users/alex/Work/Community/그리너리라운지 세부 설계면적 기준(2024).csv` (세대수 열 선택).
- 근거 조회: OpenCrab MCP `opencrab_query` — "A-801~802 커뮤니티" 팩들에 투영구역·토폴로지 레버·세션 이력.

## 양방향 소통 (브라우저 ↔ 에이전트)
- 사용자가 "이거/이 선/여기"라고 하면 **무조건 `browser_state` 먼저** — 선택 요소 상세, 현재 보고 있는 viewBox,
  사용자가 💬 버튼으로 보낸 메시지(당시 선택 동봉)가 온다. viewport를 `render_view(bbox=viewport)`에 넣으면
  사용자와 같은 화면을 본다.
- 설명할 때 `notify_user(text, pointer=[x,y])`로 브라우저에 알림 + 깜빡이는 마커를 찍어 "여기"를 가리켜라.
- 작업 완료·경고도 notify_user로 즉시 알린다 (사용자는 채팅창을 안 보고 있을 수 있다).

## 다분야 작업 (건축·조경·전기·기계·소방·토목)
- 분야 요소는 반드시 해당 레이어에: draw 계열의 `layer` 인자 또는 `place_symbol`(자동 배정).
- 심볼 카탈로그는 `list_symbols` — 수목/조명/콘센트/디퓨저/밸브/스프링클러/맨홀 등 23종, 회전·스케일·라벨 지원.
- 분야별 검토 시 `list_elements(layer=...)` + `render_view(bbox)`로 해당 레이어만 확인.
- 겹침 규칙: 전기·기계·소방 심볼은 건축 벽체를 피해 실내에, 조경·토목은 외부/조경대에. 배치 전 recognize_room으로 실 확인.

## 금지
- 원본 파일 덮어쓰기, 불가침 존 무단 force, 리허설 없는 1000+ 요소 삭제, 스케일 미보정 상태의 평수 보고.
