#!/bin/bash
# run-proxy-client.sh — wrapper for bale-proxy system when running as LaunchAgent
# 
# This wrapper:
# 1. Ignores SIGPIPE (prevents BrokenPipe from killing the process)
# 2. Ensures stdout/stderr are unbuffered
# 3. Waits for network availability before starting
# 4. Retries on transient WS connection failures
#
set -euo pipefail

PYTHON="${USERBOT_BALE_PYTHON:-/Users/emad/IdeaProjects/userbot-bale/.venv/bin/python}"
JWT_FILE="${USERBOT_BALE_JWT_FILE:-/Users/emad/.bale_jwt_b}"
PSK_FILE="${USERBOT_BALE_PSK_FILE:-/Users/emad/.userbot-bale/userbot-bale-vpn.psk}"
LISTEN_PORT="${USERBOT_BALE_LISTEN_PORT:-1080}"
NETWORK_SERVICE="${USERBOT_BALE_SERVICE:-Wi-Fi}"
# Relay account A peer_id — the VPS relay this client dials into
RELAY_PEER_ID="${USERBOT_BALE_RELAY_PEER_ID:-1519372475}"

# Ignore SIGPIPE — prevents BrokenPipe from terminating us
trap '' PIPE

# Wait until network is actually reachable (max 30s)
wait_for_network() {
    local deadline=$((SECONDS + 30))
    while [[ $SECONDS -lt $deadline ]]; do
        if /usr/bin/curl -s --max-time 2 --head https://next-ws.bale.ai/ &>/dev/null; then
            return 0
        fi
        sleep 2
    done
    echo "$(date -u +%FT%TZ) [proxy-client] WARNING: network check timed out, starting anyway" >&2
}

# Disable system proxies to prevent connection deadlocks before starting
disable_proxies() {
    local svc="$NETWORK_SERVICE"
    echo "$(date -u +%FT%TZ) [proxy-client] ensuring system proxies are disabled for $svc..." >&2
    networksetup -setwebproxystate "$svc" off 2>/dev/null || true
    networksetup -setsecurewebproxystate "$svc" off 2>/dev/null || true
    networksetup -setsocksfirewallproxystate "$svc" off 2>/dev/null || true
}

# Kill any stale proxy processes and free the listen port
cleanup_stale() {
    # Kill previous bale-proxy system processes (but not ourselves)
    local stale
    stale=$(pgrep -f "bale-proxy system" 2>/dev/null | grep -v "^$$\$" || true)
    if [[ -n "$stale" ]]; then
        echo "$(date -u +%FT%TZ) [proxy-client] killing stale proxy processes: $stale" >&2
        echo "$stale" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
    # Free the listen port if anything else is holding it
    local port_pids
    port_pids=$(lsof -ti :"$LISTEN_PORT" 2>/dev/null || true)
    if [[ -n "$port_pids" ]]; then
        echo "$(date -u +%FT%TZ) [proxy-client] freeing port $LISTEN_PORT (pids: $port_pids)" >&2
        echo "$port_pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

cleanup_stale
disable_proxies
echo "$(date -u +%FT%TZ) [proxy-client] waiting for network..." >&2
wait_for_network
echo "$(date -u +%FT%TZ) [proxy-client] network ready, starting proxy..." >&2

exec "$PYTHON" -u -m userbot_bale.cli bale-proxy system \
    --transport dc \
    --bale-jwt-file "$JWT_FILE" \
    --peer-id "$RELAY_PEER_ID" \
    --listen-host 127.0.0.1 \
    --listen-port "$LISTEN_PORT" \
    --proxy-secret-file "$PSK_FILE" \
    --service "$NETWORK_SERVICE" \
    --proxy-ready-timeout 60 \
    --ws-ssl-no-verify
