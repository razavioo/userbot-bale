#!/usr/bin/env bash
set -euo pipefail

LOCAL_PORT="${USERBOT_BALE_PROXY_LOCAL_PORT:-1080}"
REMOTE_HOST="${USERBOT_BALE_PROXY_HOST:-68.183.118.171}"
REMOTE_USER="${USERBOT_BALE_PROXY_USER:-root}"
LOCAL_HOST="${USERBOT_BALE_PROXY_LOCAL_HOST:-127.0.0.1}"
LOG_DIR="${USERBOT_BALE_PROXY_LOG_DIR:-$HOME/Library/Logs/userbot-bale}"
PID_FILE="$LOG_DIR/foreign-browser-proxy.pid"

is_our_proxy() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -F -- "$REMOTE_USER@$REMOTE_HOST" | grep -F -- "-D $LOCAL_HOST:$LOCAL_PORT" >/dev/null
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if is_our_proxy "$PID" && kill -0 "$PID" >/dev/null 2>&1; then
    kill "$PID"
  fi
  rm -f "$PID_FILE"
fi

PIDS="$(lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN || true)"
if [[ -n "$PIDS" ]]; then
  while IFS= read -r PID; do
    if is_our_proxy "$PID"; then
      kill "$PID"
    fi
  done <<< "$PIDS"
fi

echo "Stopped proxy on $LOCAL_HOST:$LOCAL_PORT"
