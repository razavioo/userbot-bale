#!/usr/bin/env bash
# Deploy userbot-bale to a remote exit node.
#
# Required:
#   TARGET_HOST=<host-or-ip> JWT_FILE=<local-jwt-file> bash scripts/push-exit-node.sh
#
# Optional:
#   REMOTE_USER=root WAN_IFACE=eth0 REMOTE_DIR=/opt/userbot-bale TUN_IFACE=vpn0
#   TUN_ADDR=10.77.0.1/24 SSH_OPTS="..."
#
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-${1:-}}"
REMOTE_USER="${REMOTE_USER:-root}"
JWT_FILE="${JWT_FILE:-${2:-}}"
WAN_IFACE="${WAN_IFACE:-${3:-eth0}}"
REMOTE_DIR="${REMOTE_DIR:-/opt/userbot-bale}"
TUN_IFACE="${TUN_IFACE:-vpn0}"
TUN_ADDR="${TUN_ADDR:-10.77.0.1/24}"
TUN_MTU="${TUN_MTU:-1400}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="${SSH_OPTS:-"-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"}"

if [[ -z "$TARGET_HOST" || -z "$JWT_FILE" ]]; then
    echo "ERROR: TARGET_HOST and JWT_FILE are required"
    echo "Usage:"
    echo "  TARGET_HOST=<host> JWT_FILE=<jwt> bash scripts/push-exit-node.sh"
    exit 1
fi
if [[ ! -f "$JWT_FILE" ]]; then
    echo "ERROR: JWT file not found: $JWT_FILE"
    exit 1
fi

echo "==================================================="
echo "  userbot-bale - Exit Node Deployment"
echo "  Target: $REMOTE_USER@$TARGET_HOST"
echo "  Remote dir: $REMOTE_DIR"
echo "  TUN: $TUN_IFACE @ $TUN_ADDR"
echo "  WAN: $WAN_IFACE"
echo "==================================================="

echo ""
echo "[1/4] Syncing repo to $TARGET_HOST:$REMOTE_DIR ..."
rsync -az --delete \
    -e "ssh $SSH_OPTS" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.mypy_cache' \
    --exclude='.ruff_cache' \
    --exclude='.pytest_cache' \
    --exclude='build' \
    "$REPO_ROOT/" \
    "$REMOTE_USER@$TARGET_HOST:$REMOTE_DIR/"

echo "[2/4] Copying JWT..."
scp $SSH_OPTS "$JWT_FILE" "$REMOTE_USER@$TARGET_HOST:/tmp/userbot-bale-jwt.txt"

echo "[3/4] Running remote deploy..."
ssh $SSH_OPTS "$REMOTE_USER@$TARGET_HOST" \
    "INSTALL_DIR='$REMOTE_DIR' TUN_IFACE='$TUN_IFACE' TUN_ADDR='$TUN_ADDR' TUN_MTU='$TUN_MTU' bash '$REMOTE_DIR/scripts/deploy-exit-node.sh' /tmp/userbot-bale-jwt.txt '$WAN_IFACE'"

echo "[4/4] Installing systemd service..."
ssh $SSH_OPTS "$REMOTE_USER@$TARGET_HOST" \
    "INSTALL_DIR='$REMOTE_DIR' TUN_IFACE='$TUN_IFACE' bash '$REMOTE_DIR/scripts/install-systemd-exit.sh' '$WAN_IFACE'"

echo ""
echo "OK: exit node deployed."
