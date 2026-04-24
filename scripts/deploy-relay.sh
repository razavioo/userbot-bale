#!/usr/bin/env bash
# Install and configure baleobala on a relay/client node.
#
# Usage:
#   INSTALL_DIR=/opt/baleobala TUN_IFACE=vpn0 TUN_ADDR=10.77.0.2/24 \
#     bash deploy-relay.sh <jwt-file> <exit-peer-id>
#
set -euo pipefail

JWT_FILE="${1:-}"
PEER_ID="${2:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/baleobala}"
TUN_IFACE="${TUN_IFACE:-vpn0}"
TUN_ADDR="${TUN_ADDR:-10.77.0.2/24}"
TUN_MTU="${TUN_MTU:-1400}"

if [[ -z "$JWT_FILE" || -z "$PEER_ID" ]]; then
    echo "ERROR: JWT file and peer ID are required"
    echo "Usage: bash deploy-relay.sh <jwt-file> <exit-peer-id>"
    exit 1
fi
if [[ ! -f "$JWT_FILE" ]]; then
    echo "ERROR: JWT file not found: $JWT_FILE"
    exit 1
fi

echo "==================================================="
echo "  baleobala - Relay Setup"
echo "  Install dir: $INSTALL_DIR"
echo "  TUN: $TUN_IFACE @ $TUN_ADDR"
echo "  Peer ID: $PEER_ID"
echo "==================================================="

echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git iproute2 iptables curl

echo "[2/6] Using synced code at $INSTALL_DIR..."
if [[ ! -d "$INSTALL_DIR/src" ]]; then
    echo "ERROR: $INSTALL_DIR not found or incomplete."
    exit 1
fi
cd "$INSTALL_DIR"

echo "[3/6] Creating virtualenv and installing dependencies..."
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[bale,vpn-video]"

echo "[4/6] Storing JWT..."
mkdir -p /etc/baleobala
cp "$JWT_FILE" /etc/baleobala/jwt.txt
chmod 600 /etc/baleobala/jwt.txt

echo "[5/6] Registering JWT in baleobala auth store..."
baleobala auth login --jwt-file /etc/baleobala/jwt.txt

echo "[6/6] Creating TUN device $TUN_IFACE @ $TUN_ADDR..."
bash scripts/vpn-setup-tun.sh "$TUN_IFACE" "$TUN_ADDR" "$TUN_MTU" "$(id -un)" || true

echo "$PEER_ID" > /etc/baleobala/exit_peer_id.txt

echo ""
echo "OK: relay node ready."
