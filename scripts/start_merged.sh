#!/usr/bin/env bash
# Start Council FastAPI (8765) + COA Flask (5050) + unified Vite (5173).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

COA_DIR="${COA_PROJECT_DIR:-$ROOT/COA/COA_Project}"
COA_DIR="$(cd "$COA_DIR" && pwd)"

if [[ -x "$COA_DIR/venv/bin/python3" ]]; then
  COA_PYTHON="$COA_DIR/venv/bin/python3"
elif [[ -x "$COA_DIR/.venv/bin/python3" ]]; then
  COA_PYTHON="$COA_DIR/.venv/bin/python3"
else
  COA_PYTHON="python3"
fi

PIDS=()
cleanup() {
  echo ""
  echo "Stopping background services..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

wait_http() {
  local url="$1" name="$2" max="${3:-60}"
  local i=0
  while (( i < max )); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "OK: $name ($url)"
      return 0
    fi
    sleep 1
    ((i++)) || true
  done
  echo "TIMEOUT waiting for $name ($url)"
  return 1
}

echo "Starting Council API (uvicorn) on 8765..."
uvicorn api.app:app --host 127.0.0.1 --port 8765 &
PIDS+=("$!")
wait_http "http://127.0.0.1:8765/api/health" "Council FastAPI" 45

echo "Starting COA Flask (web_api.py) on 5050..."
(cd "$COA_DIR" && exec "$COA_PYTHON" web_api.py) &
PIDS+=("$!")
wait_http "http://127.0.0.1:5050/api/health" "COA Flask" 60

echo "Starting Vite (unified UI) on 5173..."
(cd "$ROOT/web" && exec npm run dev)
