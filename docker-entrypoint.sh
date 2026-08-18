#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-main}"
if [[ $# -gt 0 ]]; then
  shift
fi

wait_for_mcp() {
  local host="${PYFIX_MCP_SERVER_HOST:-127.0.0.1}"
  local port="${PYFIX_MCP_SERVER_PORT:-8000}"
  python - "$host" "$port" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
deadline = time.time() + 15
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(0.25)
sys.exit(1)
PY
}

case "$MODE" in
  server)
    exec python /app/mcp_server.py "$@"
    ;;
  main)
    python /app/mcp_server.py > /tmp/pyfix-mcp.log 2>&1 &
    mcp_pid=$!
    cleanup() {
      if kill -0 "$mcp_pid" 2>/dev/null; then
        kill "$mcp_pid" 2>/dev/null || true
        wait "$mcp_pid" 2>/dev/null || true
      fi
    }
    trap cleanup EXIT

    if ! wait_for_mcp; then
      echo "PyFix MCP server did not start successfully" >&2
      if [[ -f /tmp/pyfix-mcp.log ]]; then
        cat /tmp/pyfix-mcp.log >&2
      fi
      exit 1
    fi

    exec python /app/main.py "$@"
    ;;
  bash|sh)
    exec "$MODE" "$@"
    ;;
  *)
    python /app/mcp_server.py > /tmp/pyfix-mcp.log 2>&1 &
    mcp_pid=$!
    cleanup() {
      if kill -0 "$mcp_pid" 2>/dev/null; then
        kill "$mcp_pid" 2>/dev/null || true
        wait "$mcp_pid" 2>/dev/null || true
      fi
    }
    trap cleanup EXIT

    if ! wait_for_mcp; then
      echo "PyFix MCP server did not start successfully" >&2
      if [[ -f /tmp/pyfix-mcp.log ]]; then
        cat /tmp/pyfix-mcp.log >&2
      fi
      exit 1
    fi

    exec python /app/main.py "$MODE" "$@"
    ;;
esac
