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

The browser toolbar mirrors the same operations (선택/직선/폴리라인/곡선/사각형/원/텍스트/구역/복사/스타일복사,
Delete 키 삭제, 실행취소, 자연어 프롬프트 → 재생성).

## Notes

- The server binds to 127.0.0.1 only; the op allowlist rejects anything outside the
  document operations above.
- Undo history keeps the last 50 mutations.
- Bounding boxes are computed from geometry attributes and ignore transforms (approximate).
- The legacy studio (`./studio.sh`) and exec MCP (`crab-archi-design-mcp`) are unchanged;
  the canvas is the lightweight interactive front end on top of the same pipeline.
