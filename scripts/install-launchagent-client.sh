#!/usr/bin/env bash
# install-launchagent-client.sh — macOS LaunchAgent for baleobala proxy client
#
# Installs (or removes) a per-user LaunchAgent that runs
#   baleobala bale-proxy system ...
# as a daemon. The agent:
#  - Starts automatically at login (RunAtLoad=true)
#  - Restarts on crash (KeepAlive=true)
#  - Enables macOS Wi-Fi SOCKS proxy while running
#  - Removes the system proxy on clean shutdown (SIGTERM → Python signal handler)
#
# Usage:
#   install   bash scripts/install-launchagent-client.sh [--install]
#   uninstall bash scripts/install-launchagent-client.sh --uninstall
#   status    bash scripts/install-launchagent-client.sh --status
#   start     bash scripts/install-launchagent-client.sh --start
#   stop      bash scripts/install-launchagent-client.sh --stop
#   logs      bash scripts/install-launchagent-client.sh --logs
#
# Required environment (or pass as arguments):
#   BALEOBALA_JWT_FILE   — path to local Bale JWT file (e.g. ~/.baleobala/.bale_jwt_client)
#   BALEOBALA_PSK_FILE   — path to pre-shared key file (e.g. ~/.baleobala/baleobala-vpn.psk)
#   BALEOBALA_PYTHON     — path to Python interpreter (default: auto-detect .venv)
#   BALEOBALA_LISTEN_PORT — SOCKS5 listen port (default: 1080)
#   BALEOBALA_SERVICE     — macOS network service name (default: Wi-Fi)
#
set -euo pipefail

LABEL="ai.baleobala.proxy-client"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/baleobala"
STDOUT_LOG="$LOG_DIR/proxy-client.log"
STDERR_LOG="$LOG_DIR/proxy-client.err"

# ── Defaults ──────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${BALEOBALA_PYTHON:-}"
JWT_FILE="${BALEOBALA_JWT_FILE:-}"
PSK_FILE="${BALEOBALA_PSK_FILE:-}"
LISTEN_PORT="${BALEOBALA_LISTEN_PORT:-1080}"
NETWORK_SERVICE="${BALEOBALA_SERVICE:-Wi-Fi}"
ANSWER_TIMEOUT="${BALEOBALA_ANSWER_TIMEOUT:-300}"
WS_NO_VERIFY="${BALEOBALA_WS_NO_VERIFY:-1}"  # set to 0 to enable WS TLS verification

ACTION="${1:---install}"

# ── Helpers ───────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

detect_python() {
    # 1. explicit venv in repo
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
        echo "$REPO_ROOT/.venv/bin/python"; return
    fi
    # 2. baleobala on PATH
    local py
    if py="$(command -v baleobala 2>/dev/null)"; then
        # strip the shim and find the interpreter
        local interp
        interp="$(head -1 "$py" | sed 's|^#!||')"
        if [[ -x "$interp" ]]; then echo "$interp"; return; fi
    fi
    # 3. python3 on PATH
    if command -v python3 &>/dev/null; then
        echo "$(command -v python3)"; return
    fi
    die "Could not find a Python interpreter. Set BALEOBALA_PYTHON."
}

detect_jwt() {
    local candidates=(
        "$HOME/.baleobala/.bale_jwt_client"
        "$HOME/.baleobala/bale_jwt.txt"
        "/tmp/bale_jwt_client.txt"
        "/tmp/bale_jwt.txt"
    )
    for f in "${candidates[@]}"; do
        [[ -f "$f" ]] && echo "$f" && return
    done
    die "Could not find a JWT file. Set BALEOBALA_JWT_FILE or create one of: ${candidates[*]}"
}

detect_psk() {
    local candidates=(
        "$HOME/.baleobala/baleobala-vpn.psk"
        "$HOME/.baleobala/proxy.psk"
    )
    for f in "${candidates[@]}"; do
        [[ -f "$f" ]] && echo "$f" && return
    done
    # PSK is optional — relay may not require it
    echo ""
}

# ── Install ───────────────────────────────────────────────────────────────────
do_install() {
    [[ -n "$PYTHON" ]]   || PYTHON="$(detect_python)"
    [[ -n "$JWT_FILE" ]] || JWT_FILE="$(detect_jwt)"
    [[ -n "$PSK_FILE" ]] || PSK_FILE="$(detect_psk)"

    [[ -f "$JWT_FILE" ]] || die "JWT file not found: $JWT_FILE"
    [[ -x "$PYTHON" ]]   || die "Python not executable: $PYTHON"

    mkdir -p "$PLIST_DIR" "$LOG_DIR"

    # Build the argument list
    local args_xml
    args_xml="$(python3 -c "
import sys
args = [
    '$PYTHON', '-m', 'baleobala.cli',
    'bale-proxy', 'system',
    '--transport', 'dc',
    '--bale-jwt-file', '$JWT_FILE',
    '--answer',
    '--answer-timeout', '$ANSWER_TIMEOUT',
    '--listen-port', '$LISTEN_PORT',
    '--service', '$NETWORK_SERVICE',
"
    if [[ -n "$PSK_FILE" ]]; then
        args_xml+="    '--proxy-secret-file', '$PSK_FILE',"$'\n'
    fi
    if [[ "$WS_NO_VERIFY" == "1" ]]; then
        args_xml+="    '--ws-ssl-no-verify',"$'\n'
    fi
    args_xml+="
]
for a in args:
    print('        <string>' + a + '</string>')
" 2>/dev/null || true)"

    # Manually build the args block in case python3 isn't available in PATH
    if [[ -z "$args_xml" ]]; then
        args_xml="        <string>$PYTHON</string>
        <string>-m</string>
        <string>baleobala.cli</string>
        <string>bale-proxy</string>
        <string>system</string>
        <string>--transport</string>
        <string>dc</string>
        <string>--bale-jwt-file</string>
        <string>$JWT_FILE</string>
        <string>--answer</string>
        <string>--answer-timeout</string>
        <string>$ANSWER_TIMEOUT</string>
        <string>--listen-port</string>
        <string>$LISTEN_PORT</string>
        <string>--service</string>
        <string>$NETWORK_SERVICE</string>"
        [[ -n "$PSK_FILE" ]] && args_xml+="
        <string>--proxy-secret-file</string>
        <string>$PSK_FILE</string>"
        [[ "$WS_NO_VERIFY" == "1" ]] && args_xml+="
        <string>--ws-ssl-no-verify</string>"
    fi

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- baleobala proxy client LaunchAgent -->
    <!-- Generated by install-launchagent-client.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ") -->
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
${args_xml}
    </array>

    <!-- Restart automatically if it crashes -->
    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
    </dict>

    <!-- Wait 5 s before restarting (throttle crash loops) -->
    <key>ThrottleInterval</key>
    <integer>5</integer>

    <!-- Start at login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Log files — also used by --logs flag -->
    <key>StandardOutPath</key>
    <string>${STDOUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${STDERR_LOG}</string>

    <!-- Give the process its own process group so SIGTERM is delivered cleanly -->
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST

    echo "==================================================="
    echo "  baleobala — LaunchAgent install"
    echo "==================================================="
    info "Label    : $LABEL"
    info "Plist    : $PLIST_PATH"
    info "Python   : $PYTHON"
    info "JWT      : $JWT_FILE"
    info "PSK      : ${PSK_FILE:-<none>}"
    info "Port     : $LISTEN_PORT"
    info "Service  : $NETWORK_SERVICE"
    info "Stdout   : $STDOUT_LOG"
    info "Stderr   : $STDERR_LOG"
    echo ""

    # Load (or reload) the agent
    if launchctl list "$LABEL" &>/dev/null; then
        echo "Agent already loaded — reloading..."
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
    fi
    launchctl load -w "$PLIST_PATH"
    echo ""
    echo "OK: LaunchAgent loaded. The proxy will start within seconds."
    echo "    Use --status to check, --logs to follow logs."
    echo ""
    echo "  To start now:   launchctl start $LABEL"
    echo "  To stop:        launchctl stop  $LABEL"
    echo "  To uninstall:   bash scripts/install-launchagent-client.sh --uninstall"
}

# ── Uninstall ─────────────────────────────────────────────────────────────────
do_uninstall() {
    echo "Uninstalling LaunchAgent: $LABEL"
    if launchctl list "$LABEL" &>/dev/null; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        echo "  Agent stopped and unloaded."
    else
        echo "  Agent was not loaded."
    fi
    if [[ -f "$PLIST_PATH" ]]; then
        rm -f "$PLIST_PATH"
        echo "  Plist removed: $PLIST_PATH"
    fi
    # Restore system proxy (safety net — the Python process should have done this already)
    local iface="${BALEOBALA_SERVICE:-Wi-Fi}"
    if networksetup -getsocksfirewallproxy "$iface" 2>/dev/null | grep -q "^Enabled: Yes"; then
        echo "  Disabling SOCKS proxy on $iface (cleanup)..."
        networksetup -setsocksfirewallproxystate "$iface" off 2>/dev/null || true
    fi
    echo "Done."
}

# ── Status ────────────────────────────────────────────────────────────────────
do_status() {
    echo "=== LaunchAgent status: $LABEL ==="
    if launchctl list "$LABEL" 2>/dev/null; then
        echo ""
        echo "Plist : ${PLIST_PATH}"
        echo "Loaded: YES"
    else
        echo "Loaded: NO (plist: $PLIST_PATH)"
        [[ -f "$PLIST_PATH" ]] && echo "  (plist exists but is not loaded)" || echo "  (plist not installed)"
    fi
    echo ""
    echo "=== System SOCKS proxy (${BALEOBALA_SERVICE:-Wi-Fi}) ==="
    networksetup -getsocksfirewallproxy "${BALEOBALA_SERVICE:-Wi-Fi}" 2>/dev/null || true
    echo ""
    echo "=== Listener on :${LISTEN_PORT} ==="
    lsof -nP -iTCP:"$LISTEN_PORT" -sTCP:LISTEN 2>/dev/null || echo "  (nothing listening on :$LISTEN_PORT)"
}

# ── Start / Stop ──────────────────────────────────────────────────────────────
do_start() {
    [[ -f "$PLIST_PATH" ]] || die "LaunchAgent not installed. Run --install first."
    launchctl start "$LABEL"
    echo "Started $LABEL"
}

do_stop() {
    launchctl stop "$LABEL" 2>/dev/null && echo "Stopped $LABEL" || echo "Agent was not running."
}

# ── Logs ──────────────────────────────────────────────────────────────────────
do_logs() {
    mkdir -p "$LOG_DIR"
    echo "=== stdout: $STDOUT_LOG ==="
    tail -n 60 "$STDOUT_LOG" 2>/dev/null || echo "(empty)"
    echo ""
    echo "=== stderr: $STDERR_LOG ==="
    tail -n 60 "$STDERR_LOG" 2>/dev/null || echo "(empty)"
    echo ""
    echo "--- following (Ctrl-C to quit) ---"
    tail -f "$STDOUT_LOG" "$STDERR_LOG" 2>/dev/null
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "$ACTION" in
    --install|-i|install)    do_install ;;
    --uninstall|-u|uninstall) do_uninstall ;;
    --status|-s|status)      do_status ;;
    --start|start)           do_start ;;
    --stop|stop)             do_stop ;;
    --logs|-l|logs)          do_logs ;;
    *)
        echo "Usage: $0 [--install|--uninstall|--status|--start|--stop|--logs]"
        echo ""
        echo "Environment variables:"
        echo "  BALEOBALA_JWT_FILE     path to Bale JWT (required for install)"
        echo "  BALEOBALA_PSK_FILE     path to proxy PSK (optional)"
        echo "  BALEOBALA_PYTHON       Python interpreter path (default: auto)"
        echo "  BALEOBALA_LISTEN_PORT  SOCKS5 port (default: 1080)"
        echo "  BALEOBALA_SERVICE      macOS network service (default: Wi-Fi)"
        echo "  BALEOBALA_WS_NO_VERIFY 1 = skip WS TLS verify (default: 1)"
        exit 1
        ;;
esac
