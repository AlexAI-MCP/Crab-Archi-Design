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

- Session: `canvas_status`, `open_svg`, `get_canvas_svg`, `save_svg`, `undo`
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

The browser toolbar mirrors the same operations (선택/직선/폴리라인/곡선/사각형/원/텍스트/구역/복사/스타일복사,
Delete 키 삭제, 실행취소, 자연어 프롬프트 → 재생성).

## 4. Three.js camera-cut handoff

Press `◫ 3D` after opening or regenerating an SVG. The upper pane recognizes wall, column,
and furniture candidates and extrudes only those categories into a review-only Three.js
scene; it never writes 3D coordinates back into the SVG. Semantic `data-role` values take
priority per element while untagged elements keep the inference fallback, so partially tagged
SVGs remain complete. Flattened CAD exports use a bounded pass based on structural stroke
weight, repeated compact outlines, and wall-axis support. Group-inherited roles/styles and
SVG transforms are resolved into the root plan coordinate system; duplicate coincident wall
segments are emitted once. The on-screen status labels inference rather than presenting it as
source-authored semantics.

Confirm or adjust wall height, wall thickness, slab thickness, furniture height, and finish,
then use OrbitControls to orbit/pan/zoom the composition. Category checkboxes isolate walls,
columns, or furniture. `2D 정합선` places a rendered copy of the same SVG bbox directly under
the model so plan-coordinate drift is visible during review.

- `컷을 Codex로`: saves a PNG and `crab-archi-design-three-camera-cut-v1` metadata under
  `projects/<source-name>/canvas/three_d_cuts/`, then queues a `three_d_camera_cut` message.
  An MCP agent reads `browser_state`, then calls `get_latest_3d_cut` and
  `get_latest_3d_cut_metadata`. The server rejects a cut if the model revision no longer
  matches the current SVG revision.
- `캡처 복사`: copies the exact Three.js canvas as `image/png` for pasting into Codex, GPT,
  or Nano Banana.
- `렌더 요청 복사`: copies the user-entered render direction, or a safe default that requires
  preserving the camera and architectural geometry.

When the SVG has `data-crab-mm-per-unit`, the scene uses confirmed metric scale. Otherwise it
is explicitly marked `preview_normalized_focus_to_45m`; the cut is still useful for composition but
must not be treated as verified building dimensions.

## Notes

- The server binds to 127.0.0.1 only; the op allowlist rejects anything outside the
  document operations above.
- Three.js is pinned to `0.180.0` through jsDelivr; the 2D canvas continues to work if the
  module cannot be loaded, while the 3D pane reports the dependency error.
- Undo history keeps the last 50 mutations.
- Bounding boxes are computed from geometry attributes and ignore transforms (approximate).
- The legacy studio (`./studio.sh`) and exec MCP (`crab-archi-design-mcp`) are unchanged;
  the canvas is the lightweight interactive front end on top of the same pipeline.
