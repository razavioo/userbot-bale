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

PYTHON="${BALEOBALA_PYTHON:-/Users/emad/IdeaProjects/baleobala/.venv/bin/python}"
JWT_FILE="${BALEOBALA_JWT_FILE:-/Users/emad/.bale_jwt_b}"
PSK_FILE="${BALEOBALA_PSK_FILE:-/Users/emad/.baleobala/baleobala-vpn.psk}"
LISTEN_PORT="${BALEOBALA_LISTEN_PORT:-1080}"
NETWORK_SERVICE="${BALEOBALA_SERVICE:-Wi-Fi}"
# Relay account A peer_id — the VPS relay this client dials into
RELAY_PEER_ID="${BALEOBALA_RELAY_PEER_ID:-1519372475}"

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

echo "$(date -u +%FT%TZ) [proxy-client] waiting for network..." >&2
wait_for_network
echo "$(date -u +%FT%TZ) [proxy-client] network ready, starting proxy..." >&2

exec "$PYTHON" -u -m baleobala.cli bale-proxy system \
    --transport dc \
    --bale-jwt-file "$JWT_FILE" \
    --peer-id "$RELAY_PEER_ID" \
    --listen-host 127.0.0.1 \
    --listen-port "$LISTEN_PORT" \
    --proxy-secret-file "$PSK_FILE" \
    --service "$NETWORK_SERVICE" \
    --proxy-ready-timeout 60 \
    --ws-ssl-no-verify
