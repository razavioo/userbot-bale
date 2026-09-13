#!/usr/bin/env bash
# Create a persistent TUN device owned by an unprivileged user, assign
# an IP, bring it up. Run ONCE, as root, before `userbot-bale vpn up`.
#
# Usage:
#   sudo ./scripts/vpn-setup-tun.sh <iface> <addr/cidr> <mtu> <owner-user>
#
# Example (client):
#   sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.2/24 1400 "$USER"
# Example (exit node):
#   sudo ./scripts/vpn-setup-tun.sh vpn0 10.77.0.1/24 1400 "$USER"
#
# The device persists across userbot-bale restarts; tear it down with:
#   sudo ip link del vpn0
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 <iface> <addr/cidr> <mtu> <owner-user>" >&2
  exit 1
fi

iface=$1
addr=$2
mtu=$3
owner=$4

if [[ $EUID -ne 0 ]]; then
  echo "must be run as root (try: sudo $0 ...)" >&2
  exit 2
fi

modprobe tun 2>/dev/null || true

if ip link show "$iface" >/dev/null 2>&1; then
  echo "note: $iface already exists; re-configuring it"
else
  ip tuntap add mode tun user "$owner" name "$iface"
fi

ip addr flush dev "$iface"
ip addr add "$addr" dev "$iface"
ip link set "$iface" mtu "$mtu" up

echo "ok: $iface $addr mtu=$mtu owner=$owner"
