#!/usr/bin/env bash
set -euo pipefail

LOCAL_PORT="${BALEOBALA_PROXY_LOCAL_PORT:-1080}"
LOG_DIR="${BALEOBALA_PROXY_LOG_DIR:-$HOME/Library/Logs/baleobala}"
PID_FILE="$LOG_DIR/foreign-browser-proxy.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if [[ -n "$PID" ]] && kill -0 "$PID" >/dev/null 2>&1; then
    kill "$PID"
  fi
  rm -f "$PID_FILE"
fi

PIDS="$(lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN || true)"
if [[ -n "$PIDS" ]]; then
  kill $PIDS
fi

echo "Stopped proxy on 127.0.0.1:$LOCAL_PORT"
