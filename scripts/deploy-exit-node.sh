#!/usr/bin/env bash
# Install and configure baleobala on an exit node.
#
# Usage:
#   INSTALL_DIR=/opt/baleobala TUN_IFACE=vpn0 TUN_ADDR=10.77.0.1/24 \
#     bash deploy-exit-node.sh <jwt-file> [wan-interface]
#
set -euo pipefail

JWT_FILE="${1:-}"
WAN_IFACE="${2:-${WAN_IFACE:-eth0}}"
INSTALL_DIR="${INSTALL_DIR:-/opt/baleobala}"
TUN_IFACE="${TUN_IFACE:-vpn0}"
TUN_ADDR="${TUN_ADDR:-10.77.0.1/24}"
TUN_MTU="${TUN_MTU:-1400}"

if [[ -z "$JWT_FILE" ]]; then
    echo "ERROR: JWT file path required"
    echo "Usage: bash deploy-exit-node.sh <jwt-file> [wan-interface]"
    exit 1
fi
if [[ ! -f "$JWT_FILE" ]]; then
    echo "ERROR: JWT file not found: $JWT_FILE"
    exit 1
fi

echo "==================================================="
echo "  baleobala - Exit Node Setup"
echo "  Install dir: $INSTALL_DIR"
echo "  TUN: $TUN_IFACE @ $TUN_ADDR"
echo "  WAN: $WAN_IFACE"
echo "==================================================="

echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git iproute2 iptables curl

echo "[2/7] Using synced code at $INSTALL_DIR..."
if [[ ! -d "$INSTALL_DIR/src" ]]; then
    echo "ERROR: $INSTALL_DIR not found or incomplete."
    exit 1
fi
cd "$INSTALL_DIR"

echo "[3/7] Creating virtualenv and installing dependencies..."
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[bale,vpn-video]"

echo "[4/7] Storing JWT..."
mkdir -p /etc/baleobala
cp "$JWT_FILE" /etc/baleobala/jwt.txt
chmod 600 /etc/baleobala/jwt.txt

echo "[5/7] Registering JWT in baleobala auth store..."
baleobala auth login --jwt-file /etc/baleobala/jwt.txt

echo "[6/7] Creating TUN device $TUN_IFACE @ $TUN_ADDR..."
bash scripts/vpn-setup-tun.sh "$TUN_IFACE" "$TUN_ADDR" "$TUN_MTU" "$(id -un)" || true

echo "[7/7] Enabling IPv4 forwarding and MASQUERADE on $WAN_IFACE..."
bash scripts/vpn-exit-node.sh "$TUN_IFACE" "$WAN_IFACE"

echo ""
echo "OK: exit node ready."
