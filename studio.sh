#!/usr/bin/env bash
# One-command launcher for the Crab Archi Design localhost studio.
# Usage: ./studio.sh [port]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8765}"
cd "$ROOT_DIR"

if [ -x "$ROOT_DIR/.venv/bin/crab-archi-design" ]; then
  CLI=("$ROOT_DIR/.venv/bin/crab-archi-design")
elif command -v crab-archi-design >/dev/null 2>&1; then
  CLI=(crab-archi-design)
else
  echo "==> First run: creating .venv and installing crab-archi-design"
  python3 -m venv "$ROOT_DIR/.venv"
  "$ROOT_DIR/.venv/bin/pip" install -q -e "$ROOT_DIR"
  CLI=("$ROOT_DIR/.venv/bin/crab-archi-design")
fi

echo "==> Studio: http://127.0.0.1:$PORT/  (Ctrl+C to stop)"
exec "${CLI[@]}" studio --project-root "$ROOT_DIR/projects" --port "$PORT" --open
