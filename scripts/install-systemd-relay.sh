#!/usr/bin/env bash
# Install userbot-bale relay/client as a systemd service.
#
set -euo pipefail

PEER_ID="${1:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/userbot-bale}"
TUN_IFACE="${TUN_IFACE:-vpn0}"

if [[ -z "$PEER_ID" ]]; then
    PEER_ID="$(cat /etc/userbot-bale/exit_peer_id.txt 2>/dev/null || true)"
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

cat > /etc/systemd/system/userbot-bale-relay.service << EOF
[Unit]
Description=userbot-bale VPN Relay Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/userbot-bale tunnel up \\
    --bale-jwt-file /etc/userbot-bale/jwt.txt \\
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
systemctl enable userbot-bale-relay.service
systemctl start userbot-bale-relay.service

echo "OK: userbot-bale-relay service installed and started"
echo "  Status : systemctl status userbot-bale-relay"
echo "  Logs   : journalctl -u userbot-bale-relay -f"
