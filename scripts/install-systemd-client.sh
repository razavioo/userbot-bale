#!/usr/bin/env bash
# Install userbot-bale VPN client as a pair of systemd services:
#
#   userbot-bale-tun-setup.service  — one-shot (root): create the TUN device at
#                                   boot so the VPN daemon can run unprivileged.
#   userbot-bale-client.service     — the VPN client itself (runs as USER).
#
# Usage (run as root):
#   sudo bash scripts/install-systemd-client.sh [<username>]
#
# Environment overrides:
#   USER_NAME    Linux user that owns the VPN process (default: first non-root
#                user with a home dir, or the positional argument)
#   INSTALL_DIR  userbot-bale project directory (default: /opt/userbot-bale)
#   TUN_IFACE    TUN interface name           (default: vpn0)
#   TUN_ADDR     TUN address/prefix           (default: 10.77.0.2/24)
#   TUN_MTU      TUN MTU                      (default: 1400)
#   KILL_SWITCH  Enable kill switch?           (default: false)
#
# Example:
#   sudo INSTALL_DIR=/home/alice/userbot-bale \
#        KILL_SWITCH=true \
#        bash scripts/install-systemd-client.sh alice
set -euo pipefail

# ── Resolve config ──────────────────────────────────────────────────────────
USER_NAME="${1:-${USER_NAME:-}}"
INSTALL_DIR="${INSTALL_DIR:-/opt/userbot-bale}"
TUN_IFACE="${TUN_IFACE:-vpn0}"
TUN_ADDR="${TUN_ADDR:-10.77.0.2/24}"
TUN_MTU="${TUN_MTU:-1400}"
KILL_SWITCH="${KILL_SWITCH:-false}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (try: sudo $0 ...)" >&2
    exit 1
fi

# If caller didn't name a user, pick the first non-root user with a home dir.
if [[ -z "$USER_NAME" ]]; then
    USER_NAME="$(awk -F: '$3 >= 1000 && $6 ~ /^\/home/ { print $1; exit }' /etc/passwd || true)"
fi
if [[ -z "$USER_NAME" ]]; then
    echo "ERROR: could not determine a target user; pass it as an argument." >&2
    exit 1
fi
if ! id "$USER_NAME" &>/dev/null; then
    echo "ERROR: user '${USER_NAME}' does not exist." >&2
    exit 1
fi
USER_HOME="$(eval echo "~${USER_NAME}")"

echo "Installing userbot-bale VPN client services"
echo "  user:       ${USER_NAME}"
echo "  install:    ${INSTALL_DIR}"
echo "  tun:        ${TUN_IFACE}  addr=${TUN_ADDR}  mtu=${TUN_MTU}"
echo "  kill-switch: ${KILL_SWITCH}"
echo ""

# ── TUN setup service (root, one-shot) ─────────────────────────────────────
# Creates the persistent TUN device owned by USER_NAME at boot. This is the
# "privileged helper": it runs as root only during early boot and exits
# immediately after, so the VPN client can open /dev/net/tun without root.
cat > /etc/systemd/system/userbot-bale-tun-setup.service << EOF
[Unit]
Description=userbot-bale TUN device setup
# Must complete before the VPN client starts.
Before=userbot-bale-client.service
# Run after the kernel modules are available but before the network comes up
# so the TUN device is ready for the VPN client to reference from the start.
After=local-fs.target
DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\
    modprobe tun 2>/dev/null || true; \
    if ip link show ${TUN_IFACE} >/dev/null 2>&1; then \
        ip link set ${TUN_IFACE} down 2>/dev/null || true; \
        ip tuntap del dev ${TUN_IFACE} mode tun 2>/dev/null || true; \
    fi; \
    ip tuntap add mode tun user ${USER_NAME} name ${TUN_IFACE}; \
    ip addr add ${TUN_ADDR} dev ${TUN_IFACE}; \
    ip link set ${TUN_IFACE} mtu ${TUN_MTU} up'
ExecStop=/bin/bash -c '\
    ip link del ${TUN_IFACE} 2>/dev/null || true'
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ── VPN client service (runs as USER_NAME) ──────────────────────────────────
# Env vars control full-tunnel and kill-switch without editing the unit file:
#   USERBOT_BALE_FULL_TUNNEL=true  — capture the default route (0.0.0.0/1 + 128.0.0.0/1)
#   USERBOT_BALE_KILL_SWITCH=true  — block all non-tunnel outbound traffic
KS_ENV=""
if [[ "${KILL_SWITCH}" == "true" || "${KILL_SWITCH}" == "1" || "${KILL_SWITCH}" == "yes" ]]; then
    KS_ENV="Environment=USERBOT_BALE_KILL_SWITCH=true"
fi

cat > /etc/systemd/system/userbot-bale-client.service << EOF
[Unit]
Description=userbot-bale VPN Client
After=network-online.target userbot-bale-tun-setup.service
Wants=network-online.target
Requires=userbot-bale-tun-setup.service

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
# Credentials live in the user's userbot-bale config dir (~/.config/userbot-bale/).
# Run \`userbot-bale auth login\` and \`userbot-bale pair ...\` as ${USER_NAME} before
# starting this service.
Environment=HOME=${USER_HOME}
${KS_ENV}
ExecStart=${INSTALL_DIR}/.venv/bin/userbot-bale vpn up
# on-failure: restart on crashes / carrier negotiation timeouts.
# Clean exits (Ctrl-C, \`vpn down\`) keep the service inactive so it doesn't
# restart on deliberate user stops.
Restart=on-failure
RestartSec=15
# Give the carrier negotiation enough time before systemd decides it timed out.
TimeoutStartSec=120
StandardOutput=journal
StandardError=journal
KillSignal=SIGTERM
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF

# ── Enable and start ─────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable userbot-bale-tun-setup.service
systemctl enable userbot-bale-client.service
systemctl start userbot-bale-tun-setup.service
systemctl start userbot-bale-client.service

echo "OK: userbot-bale client services installed and started"
echo ""
echo "  TUN setup  : systemctl status userbot-bale-tun-setup"
echo "  VPN client : systemctl status userbot-bale-client"
echo "  Logs       : journalctl -u userbot-bale-client -f"
echo ""
echo "Before starting the VPN, make sure ${USER_NAME} has run:"
echo "  userbot-bale auth login"
echo "  userbot-bale pair ...         (to link a relay)"
echo ""
echo "To stop without restarting:"
echo "  systemctl stop userbot-bale-client"
echo "  userbot-bale vpn down"
