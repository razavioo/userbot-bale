#!/usr/bin/env bash
# Install baleobala exit-node as a systemd service.
#
set -euo pipefail

WAN_IFACE="${1:-${WAN_IFACE:-eth0}}"
INSTALL_DIR="${INSTALL_DIR:-/opt/baleobala}"
TUN_IFACE="${TUN_IFACE:-vpn0}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root"
    exit 1
fi

cat > /etc/systemd/system/baleobala-exit.service << EOF
[Unit]
Description=baleobala VPN Exit Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/baleobala tunnel exit-node \\
    --bale-jwt-file /etc/baleobala/jwt.txt \\
    --tun ${TUN_IFACE} \\
    --wan ${WAN_IFACE} \\
    --answer
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable baleobala-exit.service
systemctl start baleobala-exit.service

echo "OK: baleobala-exit service installed and started"
echo "  Status : systemctl status baleobala-exit"
echo "  Logs   : journalctl -u baleobala-exit -f"
