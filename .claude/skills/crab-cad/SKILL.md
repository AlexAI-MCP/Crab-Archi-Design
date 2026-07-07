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
3. **인식** — `list_elements(tag="text")`로 실 라벨 파악 → `recognize_rooms`로 방별 면적(m²/평).
   사용자가 브라우저에서 그린 스케치는 `list_elements(drawn=true)`가 유일한 진입점.
4. **불가침 선언** — 편집 전에 램프·기둥·코어·주차 구역을 `set_zone(no_go_zone/lock_boundary)`으로
   등록한다. 이후 모든 draw/move/stretch가 자동 차단된다 (의도적 예외만 force=true).
5. **편집** — 대량·위험 편집은 반드시 `draft(begin)` → 편집 → `render_view`로 확인 → `commit`
   (문제 시 `rollback`). 벽 연장/방 확장은 삭제 후 재작도가 아니라 `stretch_elements` 사용.
6. **시각 검증** — 편집 후 `render_view(bbox=작업영역)`으로 눈으로 확인. 스크린샷 없이 완료 선언 금지.
7. **저장** — `save_svg(path=원본과 다른 경로)`. 원본은 절대 덮어쓰지 않는다.

## 좌표·선택 요령
- 좌표는 SVG viewBox 단위. bbox 창으로 요소를 고를 때 격자선(회색 #bababa/#767676, 페이지 관통 장선)과
  구조 기둥(굵은 해칭 사각형)을 제외할 것.
- 면적 기준 대조: `/Users/alex/Work/Community/그리너리라운지 세부 설계면적 기준(2024).csv` (세대수 열 선택).
- 근거 조회: OpenCrab MCP `opencrab_query` — "A-801~802 커뮤니티" 팩들에 투영구역·토폴로지 레버·세션 이력.

## 금지
- 원본 파일 덮어쓰기, 불가침 존 무단 force, 리허설 없는 1000+ 요소 삭제, 스케일 미보정 상태의 평수 보고.
