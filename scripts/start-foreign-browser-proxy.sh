#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${BALEOBALA_PROXY_HOST:-68.183.118.171}"
REMOTE_USER="${BALEOBALA_PROXY_USER:-root}"
LOCAL_HOST="${BALEOBALA_PROXY_LOCAL_HOST:-127.0.0.1}"
LOCAL_PORT="${BALEOBALA_PROXY_LOCAL_PORT:-1080}"
PROFILE_DIR="${BALEOBALA_CHROME_PROFILE:-$HOME/Library/Application Support/baleobala-proxy-chrome}"
LOG_DIR="${BALEOBALA_PROXY_LOG_DIR:-$HOME/Library/Logs/baleobala}"
LOG_FILE="$LOG_DIR/foreign-browser-proxy.log"
PID_FILE="$LOG_DIR/foreign-browser-proxy.pid"
START_URL="${1:-https://web.bale.ai}"

mkdir -p "$LOG_DIR" "$PROFILE_DIR"

is_listening() {
  lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1
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

  lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN > "$PID_FILE" 2>/dev/null || true
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
