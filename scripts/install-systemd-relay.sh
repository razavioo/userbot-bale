#!/usr/bin/env bash
# Install baleobala relay/client as a systemd service.
#
set -euo pipefail

PEER_ID="${1:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/baleobala}"
TUN_IFACE="${TUN_IFACE:-vpn0}"

if [[ -z "$PEER_ID" ]]; then
    PEER_ID="$(cat /etc/baleobala/exit_peer_id.txt 2>/dev/null || true)"
fi
if [[ -z "$PEER_ID" ]]; then
    echo "ERROR: exit node peer ID required"
    echo "Usage: bash install-systemd-relay.sh <exit-peer-id>"
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root"
    exit 1
fi

cat > /etc/systemd/system/baleobala-relay.service << EOF
[Unit]
Description=baleobala VPN Relay Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/baleobala tunnel up \\
    --bale-jwt-file /etc/baleobala/jwt.txt \\
    --peer-id ${PEER_ID} \\
    --tun ${TUN_IFACE}
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable baleobala-relay.service
systemctl start baleobala-relay.service

echo "OK: baleobala-relay service installed and started"
echo "  Status : systemctl status baleobala-relay"
echo "  Logs   : journalctl -u baleobala-relay -f"
