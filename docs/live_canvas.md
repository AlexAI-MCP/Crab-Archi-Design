# Live SVG Canvas + MCP (Blender-MCP style)

The live canvas turns Crab Archi Design into an interactive SVG CAD surface:
the original drawing is loaded into a localhost browser canvas, and MCP agents
(Claude Code, Codex, Antigravity, ...) edit the exact same document the user
sees. Every change — human sketch or agent tool call — bumps a revision counter
the browser polls, so edits appear live.

```
browser (tools/canvas.html)  ──HTTP──▶  canvas server (crab-archi-design-canvas)
agent  (canvas MCP, stdio)   ──HTTP──▶       └── CanvasDocument (single source of truth)
                                             └── /api/regenerate → studio pipeline (deterministic engines)
                                             └── Three.js review cut → PNG + camera metadata → agent inbox
```

## 1. Start the canvas

```bash
./canvas.sh                      # port 8770, empty canvas
./canvas.sh 8770 examples/original_sample.svg
```

## 2. Connect an agent

Claude Code:

```bash
claude mcp add crab-canvas -- /path/to/.venv/bin/crab-archi-design-canvas-mcp --port 8770
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.crab-canvas]
command = "/path/to/.venv/bin/crab-archi-design-canvas-mcp"
args = ["--port", "8770"]
```

Antigravity / any MCP client: register the same stdio command.

## 3. Tool surface

- Session: `canvas_status`, `open_svg`, `get_canvas_svg`, `save_svg`, `undo`, `redo`
- Recognition: `list_elements` (stable `cid` handles, bbox, style, label text), `list_zones`
- Drawing: `draw_line`, `draw_polyline`, `draw_curve`, `draw_path`, `draw_rect`, `draw_ellipse`, `add_text`
- Editing: `delete_elements`, `move_element`, `copy_element`, `copy_style`, `set_style`, `set_attrs`
- Zones: `set_zone` / `clear_zones` — community_shell, no_go_zone, lock_boundary, protect_zone, mutable_zone, projectable_zone
- Regeneration: `regenerate_layout(prompt, ...)` — saves the canvas, converts zones into the
  constraint sketch, runs the deterministic studio pipeline (`workflow-run`/`revision-run`),
  and loads the generated alternative back into the canvas.
- 3D review: `get_latest_3d_cut`, `get_latest_3d_cut_metadata` — returns the exact Three.js
  orbit/pan camera cut captured by the user plus source revision/hash, camera, finish settings,
  and render prompt.
- Furniture layout: `auto_arrange_in_polyline(cids, boundary_cid, ...)` — deterministically
  clones the selected furniture set inside a user-drawn polyline or polygon. It derives the
  grid and placement coordinates from geometry, keeps the source set untouched, and respects
  no-go zones.
- Kids-zone layout: `populate_kids_zone(boundary_cid)` — derives a balanced six-program native
  SVG plan from the boundary alone (child table, soft play, playhouse/slide, reading corner,
  toy storage, guardian bench), keeps every item inside the polygon, and respects no-go zones.

The browser toolbar mirrors the same operations (선택/직선/폴리라인/곡선/사각형/원/텍스트/구역/복사/스타일복사,
Delete 키 삭제, 실행취소/다시 실행, 자연어 프롬프트 → 재생성). Use `Ctrl/Cmd+Z` for undo and
`Ctrl/Cmd+Y` or `Ctrl/Cmd+Shift+Z` for redo; a new successful mutation clears redo history.

## 4. Three.js camera-cut handoff

Press `◫ 3D` after opening or regenerating an SVG. The upper pane recognizes wall, column,
furniture, and repeated parking-bay candidates into a review-only Three.js
scene; it never writes 3D coordinates back into the SVG. Semantic `data-role` values take
priority per element while untagged elements keep the inference fallback, so partially tagged
SVGs remain complete. Flattened CAD exports use a bounded pass based on structural stroke
weight, repeated compact outlines, and wall-axis support. Group-inherited roles/styles and
SVG transforms are resolved into the root plan coordinate system; duplicate coincident wall
segments are emitted once. The on-screen status labels inference rather than presenting it as
source-authored semantics.

Confirm or adjust wall height, wall thickness, slab thickness, furniture height, and finish,
then use OrbitControls to orbit/pan/zoom the composition. Category checkboxes isolate walls,
columns, furniture, or parking markings. Repeated short rectangular line bays are classified
as flat parking markings before wall extrusion; a nearby EV/charging text or compact circular
charging symbol with a diagonal bay mark is shown in blue. `2D 도면 패턴` samples the source
SVG's own paths into a bounded, high-contrast native Three.js line overlay above the ground
surface, so plan-coordinate drift and source linework remain visible without a PNG/raster texture.

Click a rendered object to review its own height without changing the SVG. For a building,
`동 층수 적용` multiplies the selected floor count by the selected floor-to-floor height; for
height-capable structures, trees, shrubs, hedges, walls, columns, and furniture, `객체 높이 적용`
uses the direct millimetre value instead. Number changes are applied automatically after a short
typing pause; Enter and the explicit apply buttons remain available for immediate confirmation.
After applying, the camera frames the selected object and a cyan height box makes the changed
vertical extent visible. Lawn, planting, water, paving, road, parking, and terrain surfaces remain
selectable review context but do not expose a misleading height or floor control. Every mesh with
the same source element moves together, and `선택 평면` draws a deduplicated cyan plan footprint.
Floor-level guides appear only for building masses and stay bounded on tall towers. `초기화`
returns only that viewer-side override to its source-derived height. These are temporary Three.js
review settings, never SVG edits or new design geometry.

Selection is bidirectional: clicking a height-capable element in the 2D SVG enables the same
height controls and highlights its matching 3D object, while clicking a 3D object selects its
source SVG element. On dense drawings with overlapping SVG linework, use the `3D 객체 선택…`
list to choose the recognised building or object directly. The inline selection hint explains
when a selected 2D element is not part of the current 3D review model. The dropdown is intentionally
bounded on large landscape sheets; every omitted height-capable object remains reachable by clicking
it in the 2D or 3D view.

Use `↔ 좌우 분할` to toggle the 2D/3D layout between top-bottom and side-by-side (narrow
screens automatically remain stacked). Drag the divider between the panes to resize them;
the separate top-bottom and side-by-side ratios are remembered in the browser. Drag the `⠿`
handle on the 3D controls to the nearest top/bottom/left/right edge to dock it; the four small
arrow buttons provide the same placement without dragging. Press `−` to collapse the controls
to only the move handle and restore button. These view preferences do not modify SVG state.

The 2D `팬` tool also switches the 3D canvas left drag from orbit to pan. Holding Space while
left-dragging temporarily pans both canvases; releasing Space restores the 3D orbit behavior.

### Multi-discipline review channels

The same review scene can show building exterior/massing (including roof and facade), landscape,
civil works, and terrain at the same time as the existing wall, column, furniture, and parking
channels. `전체 동시 보기` is the default; the other view presets and every category checkbox
only change mesh visibility. They never change the source SVG, re-run classification, or alter
the displayed counts.

Classification prioritizes explicit element metadata and inherited parent-group metadata in this
order: `data-discipline`, `data-role`, `data-symbol`, and `data-name`. Thus symbols placed by the
canvas retain their `landscape tree_deciduous` or `civil manhole` role across all child primitives.
Untagged repeated compact indoor outlines still use the existing furniture fallback, while repeated
or closed green plan symbols/surfaces are treated as landscape before that fallback. On a flattened
sheet with a strong green landscape signal, the 3D focus box follows the site's green extent instead
of title-block whitespace. Neutral repeated geometry is not assumed to be landscape: large closed,
complex envelopes in the drawing's heaviest neutral stroke band become deduplicated building masses,
and untagged compact geometry on that site sheet remains in the 2D alignment overlay instead of being
invented as furniture. Explicitly tagged furniture is still shown, and the legacy compact-furniture
fallback remains active on interior drawings. This site-plan inference is relative to the imported
sheet. Civil or landscape semantics therefore cannot become furniture merely because their geometry
repeats.

Representation is a separate step after discipline classification. Building envelopes become
editable masses, linear structural elements stay structures, and landscape is split into trees,
shrubs, hedges, planting surfaces, hardscape, and water. Civil work is split into roads, utilities,
surfaces, and lines; terrain remains a surface or contour. Explicit metadata is labelled as such,
while geometry/paint fallbacks remain visibly identified as inference. This distinction prevents
all green geometry from becoming the same short cylinder and prevents every site object from
pretending to be a floor-bearing building.

When a flattened site SVG has no building height, the review viewer derives a bounded temporary
storey count from footprint size, draws facade storey bands and a roof cap, and labels the result
`발자국 기반 가높이`. Trees and shrubs receive legible multi-lobe crowns at full-site scale and
remain labelled `형상 기반 가높이`. These are visualization defaults only; explicit SVG/QGIS
height and floor attributes always take priority.

Terrain uses a low, flat review surface unless an SVG element (or parent group) explicitly supplies
`data-elevation-mm` or `data-z-mm`; contour lines and planar fills do not invent elevation. The
viewer caps building, landscape, civil, and terrain mesh channels independently for large flattened
CAD sheets and reports a reached cap in the status/log. These are bounded visual-review channels,
not a surveyed terrain or quantity model.

- `컷을 Codex로`: saves a PNG and `crab-archi-design-three-camera-cut-v1` metadata under
  `projects/<source-name>/canvas/three_d_cuts/`, then queues a `three_d_camera_cut` message.
  An MCP agent reads `browser_state`, then calls `get_latest_3d_cut` and
  `get_latest_3d_cut_metadata`. The server rejects a cut if the model revision no longer
  matches the current SVG revision.
- `캡처 복사`: copies the exact Three.js canvas as `image/png` for pasting into Codex, GPT,
  or Nano Banana.
- `렌더 요청 복사`: copies the user-entered render direction, or a safe default that requires
  preserving the camera and architectural geometry.
- `⬇ DXF`: writes a standards-based DXF. `⬇ DWG` first writes that DXF and converts it only
  when ODA File Converter is installed locally; it never relabels a DXF file as DWG. The UI
  reports both the resulting DWG path and the retained DXF source, or the converter setup note.

When the SVG has `data-crab-mm-per-unit`, the scene uses confirmed metric scale. Otherwise it
is explicitly marked `⚠ 스케일 미확정(45m 보기 정규화)`; the cut is still useful for composition
but must not be treated as verified building dimensions.

## Notes

- The server binds to 127.0.0.1 only; the op allowlist rejects anything outside the
  document operations above.
- Three.js is pinned to `0.180.0` through jsDelivr; the 2D canvas continues to work if the
  module cannot be loaded, while the 3D pane reports the dependency error.
- Undo and redo each keep the last 50 document snapshots.
- Bounding boxes are computed from geometry attributes and ignore transforms (approximate).
- The legacy studio (`./studio.sh`) and exec MCP (`crab-archi-design-mcp`) are unchanged;
  the canvas is the lightweight interactive front end on top of the same pipeline.
