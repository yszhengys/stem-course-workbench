#!/bin/bash
# Development environment startup for Open Notebook
# Assumes SurrealDB is already running externally (per .env config)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# --- helpers ---------------------------------------------------------------

port_open() {
  nc -z localhost "$1" >/dev/null 2>&1
}

kill_tree() {
  # Kill a process and its descendants, bottom-up. Missing processes are fine.
  local pid=$1 child
  [ -z "$pid" ] && return 0
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

wait_for_health() {
  local url=$1 attempts=${2:-30} delay=${3:-1}
  for _ in $(seq 1 "$attempts"); do
    if curl -sf -m 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  return 1
}

API_PID=""
WORKER_PID=""
FRONTEND_PID=""
CLEANED=0

cleanup() {
  [ "$CLEANED" = 1 ] && return
  CLEANED=1
  echo ""
  echo "🛑 Shutting down started services..."
  if [ -n "$API_PID" ]; then
    kill_tree "$API_PID"
    # The uvicorn reloader hands the socket to a child, so also release the
    # port — but ONLY when we started the API: never kill a listener on
    # API_PORT that this script did not launch itself.
    command -v lsof >/dev/null 2>&1 && lsof -t -i ":${API_PORT}" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  fi
  [ -n "$WORKER_PID" ] && kill_tree "$WORKER_PID"
  [ -n "$FRONTEND_PID" ] && kill_tree "$FRONTEND_PID"
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

# --- pre-flight checks (fail fast, with actionable messages) ---------------

echo "=== Open Notebook Dev Startup ==="

command -v uv >/dev/null 2>&1 || {
  echo "❌ uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
}
command -v nc >/dev/null 2>&1 || {
  echo "❌ nc (netcat) not found — needed for the port checks."
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "❌ curl not found — needed for the API health check."
  exit 1
}

if [ ! -f .env ]; then
  echo "❌ No .env file found."
  echo "   Create one first:"
  echo "     cp .env.example .env"
  echo "   Then edit OPEN_NOTEBOOK_ENCRYPTION_KEY, and make sure SURREAL_URL"
  echo "   points at ws://127.0.0.1:8000/rpc when running on the host."
  exit 1
fi

SURREAL_PORT=${SURREAL_PORT:-8000}
API_PORT=${API_PORT:-5055}

echo "Checking SurrealDB on port $SURREAL_PORT..."
if ! port_open "$SURREAL_PORT"; then
  echo "  Not reachable yet — waiting up to 30s for it to start..."
  SURREAL_OK=0
  for _ in $(seq 1 30); do
    sleep 1
    if port_open "$SURREAL_PORT"; then
      SURREAL_OK=1
      break
    fi
  done
  if [ "$SURREAL_OK" != 1 ]; then
    echo "❌ SurrealDB not reachable on port $SURREAL_PORT. Start it first with:"
    echo "     make database"
    exit 1
  fi
fi
echo "✅ SurrealDB is running"

for port in "$API_PORT" 3000; do
  if port_open "$port"; then
    echo "❌ Port $port is already in use — another instance (possibly from a"
    echo "   different checkout) is running there. Stop it first (make stop-all)"
    echo "   or free the port, then re-run this script."
    exit 1
  fi
done

if pgrep -f "surreal-commands-worker" >/dev/null 2>&1; then
  echo "⚠️  A surreal-commands-worker is already running — skipping worker start."
  echo "   (Run 'make worker-start' if you intentionally want a second one.)"
  START_WORKER=0
else
  START_WORKER=1
fi

# --- install dependencies ---------------------------------------------------

echo "Syncing Python dependencies..."
uv sync

echo "Syncing frontend dependencies..."
(cd frontend && npm install)

# --- start API, wait until it is actually healthy ---------------------------

echo "Starting API backend (port $API_PORT)..."
uv run --env-file .env run_api.py &
API_PID=$!

if ! wait_for_health "http://127.0.0.1:${API_PORT}/health" 30 1; then
  echo "❌ API did not become healthy on http://127.0.0.1:${API_PORT}/health"
  echo "   within 30s. See the API log output above (migrations run on startup;"
  echo "   they fail fast if the database is unreachable or misconfigured)."
  exit 1
fi
echo "✅ API is healthy"

# --- start worker (only if none is already running) --------------------------

if [ "$START_WORKER" = 1 ]; then
  echo "Starting background worker..."
  uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
  WORKER_PID=$!
fi

# --- start frontend (backgrounded so the exit trap can stop it cleanly) ------

echo "Starting Next.js frontend (port 3000)..."
(cd frontend && exec npm run dev) &
FRONTEND_PID=$!

echo ""
echo "✅ All services starting!"
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:$API_PORT"
echo "  API Docs: http://localhost:$API_PORT/docs"
echo "  (Ctrl+C stops everything this script started)"
echo ""

wait "$FRONTEND_PID"
