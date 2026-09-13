#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${USERBOT_BALE_PROXY_HOST:-68.183.118.171}"
REMOTE_USER="${USERBOT_BALE_PROXY_USER:-root}"
LOCAL_HOST="${USERBOT_BALE_PROXY_LOCAL_HOST:-127.0.0.1}"
LOCAL_PORT="${USERBOT_BALE_PROXY_LOCAL_PORT:-1080}"
PROFILE_DIR="${USERBOT_BALE_CHROME_PROFILE:-$HOME/Library/Application Support/userbot-bale-proxy-chrome}"
LOG_DIR="${USERBOT_BALE_PROXY_LOG_DIR:-$HOME/Library/Logs/userbot-bale}"
LOG_FILE="$LOG_DIR/foreign-browser-proxy.log"
PID_FILE="$LOG_DIR/foreign-browser-proxy.pid"
START_URL="${1:-https://web.bale.ai}"

mkdir -p "$LOG_DIR" "$PROFILE_DIR"

is_listening() {
  lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1
}

proxy_pid() {
  lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null | head -1
}

is_our_proxy() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -F -- "$REMOTE_USER@$REMOTE_HOST" | grep -F -- "-D $LOCAL_HOST:$LOCAL_PORT" >/dev/null
}

if ! is_listening; then
  /usr/bin/ssh \
    -f \
    -N \
    -D "$LOCAL_HOST:$LOCAL_PORT" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes \
    -o StrictHostKeyChecking=accept-new \
    "$REMOTE_USER@$REMOTE_HOST" >>"$LOG_FILE" 2>&1

  for _ in {1..30}; do
    is_listening && break
    sleep 0.2
  done

  PID="$(proxy_pid)"
  if is_our_proxy "$PID"; then
    echo "$PID" > "$PID_FILE"
  fi
else
  PID="$(proxy_pid)"
  if ! is_our_proxy "$PID"; then
    echo "Port $LOCAL_HOST:$LOCAL_PORT is already in use by a different process." >&2
    echo "Choose another port with USERBOT_BALE_PROXY_LOCAL_PORT=1081 or stop that process first." >&2
    exit 1
  fi
  echo "$PID" > "$PID_FILE"
fi

if ! is_listening; then
  echo "Proxy did not start on $LOCAL_HOST:$LOCAL_PORT. See $LOG_FILE" >&2
  exit 1
fi

open -na "Google Chrome" --args \
  --user-data-dir="$PROFILE_DIR" \
  --proxy-server="socks5://$LOCAL_HOST:$LOCAL_PORT" \
  --host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE $LOCAL_HOST" \
  --new-window "$START_URL"

echo "Proxy: socks5://$LOCAL_HOST:$LOCAL_PORT"
echo "Chrome profile: $PROFILE_DIR"
echo "Log: $LOG_FILE"
