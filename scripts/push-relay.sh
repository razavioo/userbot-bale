#!/usr/bin/env bash
# Deploy baleobala to a remote relay/client node.
#
# Required:
#   TARGET_HOST=<host-or-ip> JWT_FILE=<local-jwt-file> PEER_ID=<exit-peer-id> bash scripts/push-relay.sh
#
# Optional:
#   REMOTE_USER=ubuntu REMOTE_DIR=/opt/baleobala TUN_IFACE=vpn0 TUN_ADDR=10.77.0.2/24
#   SSHPASS=<password> for password-based SSH auth
#
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-${1:-}}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
JWT_FILE="${JWT_FILE:-${2:-}}"
PEER_ID="${PEER_ID:-${3:-}}"
REMOTE_DIR="${REMOTE_DIR:-/opt/baleobala}"
TUN_IFACE="${TUN_IFACE:-vpn0}"
TUN_ADDR="${TUN_ADDR:-10.77.0.2/24}"
TUN_MTU="${TUN_MTU:-1400}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-8}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="${SSH_OPTS:-"-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o ConnectTimeout=$CONNECT_TIMEOUT -o ConnectionAttempts=1 -o StrictHostKeyChecking=accept-new"}"

if [[ -z "$TARGET_HOST" || -z "$JWT_FILE" || -z "$PEER_ID" ]]; then
    echo "ERROR: TARGET_HOST, JWT_FILE, and PEER_ID are required"
    echo "Usage:"
    echo "  TARGET_HOST=<host> JWT_FILE=<jwt> PEER_ID=<peer-id> bash scripts/push-relay.sh"
    exit 1
fi
if [[ ! -f "$JWT_FILE" ]]; then
    echo "ERROR: JWT file not found: $JWT_FILE"
    exit 1
fi

print_network_hint() {
    echo ""
    echo "Network diagnostics:"
    local route_interface=""
    if command -v route &>/dev/null; then
        route_interface="$(route get "$TARGET_HOST" 2>/dev/null | awk '/interface:/ { print $2; exit }' || true)"
        route get "$TARGET_HOST" 2>/dev/null | awk '
            /interface:/ { print "  route interface: " $2 }
            /gateway:/ { print "  route gateway  : " $2 }
        ' || true
    fi
    if [[ "$route_interface" == utun* ]]; then
        echo "  note: this destination is routed through a utun VPN interface."
    elif ifconfig 2>/dev/null | grep -qE '^utun[0-9]+:.*<.*UP'; then
        echo "  note: utun interfaces are UP, but this destination is routed through ${route_interface:-unknown}."
    fi
    echo ""
    echo "Manual test:"
    echo "  ssh -vvv -o ConnectTimeout=$CONNECT_TIMEOUT $REMOTE_USER@$TARGET_HOST"
}

echo "==================================================="
echo "  baleobala - Relay Deployment"
echo "  Target: $REMOTE_USER@$TARGET_HOST"
echo "  Remote dir: $REMOTE_DIR"
echo "  TUN: $TUN_IFACE @ $TUN_ADDR"
echo "  Peer ID: $PEER_ID"
echo "==================================================="

echo ""
echo "Checking connectivity to $TARGET_HOST port 22 (timeout: ${CONNECT_TIMEOUT}s)..."
if command -v perl &>/dev/null; then
    if ! perl -MIO::Socket::INET -e '
        my ($host, $port, $timeout) = @ARGV;
        my $sock = IO::Socket::INET->new(
            PeerHost => $host,
            PeerPort => $port,
            Proto => "tcp",
            Timeout => $timeout,
        );
        exit($sock ? 0 : 1);
    ' "$TARGET_HOST" 22 "$CONNECT_TIMEOUT"; then
        echo "ERROR: Cannot reach $TARGET_HOST:22 within ${CONNECT_TIMEOUT}s."
        print_network_hint
        exit 1
    fi
elif ! nc -G "$CONNECT_TIMEOUT" -z "$TARGET_HOST" 22 2>/dev/null && ! nc -w "$CONNECT_TIMEOUT" -z "$TARGET_HOST" 22 2>/dev/null; then
    echo "ERROR: Cannot reach $TARGET_HOST:22 within ${CONNECT_TIMEOUT}s."
    print_network_hint
    exit 1
fi
echo "OK: port 22 reachable"

if ssh $SSH_OPTS -o BatchMode=yes -o PasswordAuthentication=no "$REMOTE_USER@$TARGET_HOST" true 2>/dev/null; then
    echo "OK: key-based SSH auth works"
    SSH_CMD="ssh $SSH_OPTS"
    SCP_CMD="scp $SSH_OPTS"
    RSYNC_CMD="rsync"
    RSYNC_RSH="ssh $SSH_OPTS"
elif command -v sshpass &>/dev/null; then
    if [[ -z "${SSHPASS:-}" ]]; then
        printf "SSH password for %s@%s: " "$REMOTE_USER" "$TARGET_HOST"
        read -rs SSHPASS
        echo ""
        export SSHPASS
    fi
    SSH_CMD="sshpass -e ssh $SSH_OPTS"
    SCP_CMD="sshpass -e scp $SSH_OPTS"
    RSYNC_CMD="sshpass -e rsync"
    RSYNC_RSH="sshpass -e ssh $SSH_OPTS"
elif command -v expect &>/dev/null; then
    if [[ -z "${SSHPASS:-}" ]]; then
        printf "SSH password for %s@%s: " "$REMOTE_USER" "$TARGET_HOST"
        read -rs SSHPASS
        echo ""
        export SSHPASS
    fi
    EXPECT_HELPER="$(mktemp -t baleobala-ssh-expect.XXXXXX)"
    cat > "$EXPECT_HELPER" << 'EOF'
#!/usr/bin/expect -f
set timeout -1
set password $env(SSHPASS)
log_user 1
spawn {*}$argv
expect {
    -re "(?i)are you sure you want to continue connecting" {
        send "yes\r"
        exp_continue
    }
    -re "(?i)password:" {
        send "$password\r"
        exp_continue
    }
    eof
}
catch wait result
exit [lindex $result 3]
EOF
    chmod 700 "$EXPECT_HELPER"
    trap 'rm -f "${EXPECT_HELPER:-}"' EXIT
    SSH_CMD="$EXPECT_HELPER ssh $SSH_OPTS"
    SCP_CMD="$EXPECT_HELPER scp $SSH_OPTS"
    RSYNC_CMD="$EXPECT_HELPER rsync"
    RSYNC_RSH="$EXPECT_HELPER ssh $SSH_OPTS"
else
    echo "ERROR: key auth failed and neither sshpass nor expect is available"
    exit 1
fi

echo ""
echo "[1/4] Syncing repo to $TARGET_HOST:$REMOTE_DIR ..."
$RSYNC_CMD -az --delete \
    -e "$RSYNC_RSH" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.mypy_cache' \
    --exclude='.ruff_cache' \
    --exclude='.pytest_cache' \
    --exclude='build' \
    --rsync-path='sudo rsync' \
    "$REPO_ROOT/" \
    "$REMOTE_USER@$TARGET_HOST:$REMOTE_DIR/"

echo "[2/4] Copying JWT..."
$SCP_CMD "$JWT_FILE" "$REMOTE_USER@$TARGET_HOST:/tmp/baleobala-jwt.txt"

echo "[3/4] Running remote deploy..."
$SSH_CMD "$REMOTE_USER@$TARGET_HOST" \
    "sudo env INSTALL_DIR='$REMOTE_DIR' TUN_IFACE='$TUN_IFACE' TUN_ADDR='$TUN_ADDR' TUN_MTU='$TUN_MTU' bash '$REMOTE_DIR/scripts/deploy-relay.sh' /tmp/baleobala-jwt.txt '$PEER_ID'"

echo "[4/4] Installing systemd service..."
$SSH_CMD "$REMOTE_USER@$TARGET_HOST" \
    "sudo env INSTALL_DIR='$REMOTE_DIR' TUN_IFACE='$TUN_IFACE' bash '$REMOTE_DIR/scripts/install-systemd-relay.sh' '$PEER_ID'"

echo ""
echo "OK: relay deployed."
