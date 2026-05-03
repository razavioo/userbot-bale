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
# --answer-timeout 86400: without an explicit value bale-call --answer
#   exits with status 2 after 300 s of idleness, which sends systemd
#   into a 5-minute restart loop and creates a tiny race window during
#   which incoming Bale calls can be dropped on the floor. 24 h is what
#   you usually want from a long-running exit node.
# --transport dc: pin the LiveKit DataChannel transport. The default
#   "auto" tries dc/qr/audio/rpc/mtproto_rpc in turn, which adds noise
#   to journalctl and slows down first-frame delivery — dc is the only
#   transport the Android client speaks today.
ExecStart=${INSTALL_DIR}/.venv/bin/baleobala tunnel exit-node \\
    --bale-jwt-file /etc/baleobala/jwt.txt \\
    --tun ${TUN_IFACE} \\
    --wan ${WAN_IFACE} \\
    --transport dc \\
    --answer \\
    --answer-timeout 86400
# Restart=always (NOT on-failure): the python tunnel exits with status
# 0 on every clean carrier-dead detection (see _wait_for_signal_or_
# carrier_dead). on-failure would treat that as a healthy stop and
# wedge the service in `inactive`, leaving the next inbound Bale call
# to dead silence. We always want the listener back.
Restart=always
# Each accepted call runs exactly once and then exits for a clean
# restart. Keeping RestartSec low keeps the gap between sessions small
# so a client toggling the VPN off/on doesn't fire a Bale call into a
# closed window.
RestartSec=3
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
echo
echo "IMPORTANT: don't run any other long-lived process (bale-proxy"
echo "client, bale-call --answer, another exit-node) with the SAME"
echo "Bale JWT as /etc/baleobala/jwt.txt. The Bale server picks one"
echo "WS session at random when an inbound call arrives, so a second"
echo "client on the same account silently steals incoming calls and"
echo "the exit node never gets to NAT them."
