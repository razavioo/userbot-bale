#!/usr/bin/env bash
# Exit-node one-time setup: enable IPv4 forwarding and MASQUERADE packets
# that leave on the public interface. Run as root on the VPS.
#
# Usage:
#   sudo ./scripts/vpn-exit-node.sh <tun-iface> <wan-iface>
# Example:
#   sudo ./scripts/vpn-exit-node.sh vpn0 eth0
#
# To undo:
#   sudo ./scripts/vpn-exit-node.sh --undo <tun-iface> <wan-iface>
set -euo pipefail

undo=0
if [[ "${1:-}" == "--undo" ]]; then
  undo=1
  shift
fi

if [[ $# -ne 2 ]]; then
  echo "usage: $0 [--undo] <tun-iface> <wan-iface>" >&2
  exit 1
fi

tun=$1
wan=$2

if [[ $EUID -ne 0 ]]; then
  echo "must be run as root" >&2
  exit 2
fi

if [[ $undo -eq 1 ]]; then
  iptables -t nat -D POSTROUTING -o "$wan" -j MASQUERADE 2>/dev/null || true
  iptables -D FORWARD -i "$tun" -o "$wan" -j ACCEPT 2>/dev/null || true
  iptables -D FORWARD -i "$wan" -o "$tun" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
  sysctl -w net.ipv4.ip_forward=0 >/dev/null
  echo "ok: reverted exit-node NAT/forward rules"
  exit 0
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null

# idempotent add: check before insert
add_rule() {
  local table=$1; shift
  if ! iptables -t "$table" -C "$@" 2>/dev/null; then
    iptables -t "$table" -A "$@"
  fi
}

add_rule nat POSTROUTING -o "$wan" -j MASQUERADE
add_rule filter FORWARD -i "$tun" -o "$wan" -j ACCEPT
add_rule filter FORWARD -i "$wan" -o "$tun" -m state --state RELATED,ESTABLISHED -j ACCEPT

echo "ok: exit-node forwarding enabled ($tun <-> $wan)"
