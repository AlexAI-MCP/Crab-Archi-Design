#!/usr/bin/env bash
# One-command launcher for the Crab Archi Design live SVG canvas.
# Usage: ./canvas.sh [port] [svg-file]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8770}"
SVG="${2:-}"
cd "$ROOT_DIR"

if [ -x "$ROOT_DIR/.venv/bin/crab-archi-design-canvas" ]; then
  CLI=("$ROOT_DIR/.venv/bin/crab-archi-design-canvas")
elif command -v crab-archi-design-canvas >/dev/null 2>&1; then
  CLI=(crab-archi-design-canvas)
else
  echo "==> First run: creating .venv and installing crab-archi-design"
  python3 -m venv "$ROOT_DIR/.venv"
  "$ROOT_DIR/.venv/bin/pip" install -q -e "$ROOT_DIR"
  CLI=("$ROOT_DIR/.venv/bin/crab-archi-design-canvas")
fi

ARGS=(--project-root "$ROOT_DIR/projects" --port "$PORT" --open)
if [ -n "$SVG" ]; then ARGS+=(--svg "$SVG"); fi
echo "==> Canvas: http://127.0.0.1:$PORT/  (Ctrl+C to stop)"
echo "==> MCP:    claude mcp add crab-canvas -- $ROOT_DIR/.venv/bin/crab-archi-design-canvas-mcp --port $PORT"
exec "${CLI[@]}" "${ARGS[@]}"
