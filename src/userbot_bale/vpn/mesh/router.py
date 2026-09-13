"""
PacketRouter: maps an outbound IP packet (destination address) to the
tunnel whose client owns that address.

On the exit node, we have ONE TUN device through which all clients'
traffic flows. When the internet returns a reply, the IP destination
is one of the client /30 addresses. The router dispatches that packet
to the correct per-client Tunnel for encapsulation.
"""

from __future__ import annotations

import ipaddress
import logging
import struct
import threading

log = logging.getLogger(__name__)


class PacketRouter:
    """Thread-safe destination-IP → Tunnel map.

    O(1) lookup on exact client /32 address. We don't do longest-prefix
    routing (the mesh assigns one host per tunnel, so /32 lookups are
    sufficient and cheap)."""

    def __init__(self) -> None:
        self._by_ip: dict[int, object] = {}  # packed u32 → tunnel
        self._lock = threading.Lock()

    def attach(self, client_ip: str, tunnel) -> None:  # type: ignore[no-untyped-def]
        key = self._pack(client_ip)
        with self._lock:
            self._by_ip[key] = tunnel
        log.info("router: attached %s", client_ip)

    def detach(self, client_ip: str) -> None:
        key = self._pack(client_ip)
        with self._lock:
            self._by_ip.pop(key, None)
        log.info("router: detached %s", client_ip)

    def dispatch(self, ip_packet: bytes) -> bool:
        """Send `ip_packet` to the tunnel responsible for its destination.
        Returns True if a tunnel accepted it; False if dst is unknown
        (caller should write to the real TUN as normal)."""
        if len(ip_packet) < 20:
            return False
        if (ip_packet[0] >> 4) != 4:
            return False  # IPv6 not supported in this router yet
        dst_key = (ip_packet[16] << 24) | (ip_packet[17] << 16) | (ip_packet[18] << 8) | ip_packet[19]
        with self._lock:
            tunnel = self._by_ip.get(dst_key)
        if tunnel is None:
            return False
        try:
            tunnel.send_packet(ip_packet)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.exception("router: tunnel.send_packet failed for %s",
                          self._unpack(dst_key))
            return False
        return True

    @staticmethod
    def _pack(ip: str) -> int:
        return struct.unpack("!I", ipaddress.IPv4Address(ip).packed)[0]

    @staticmethod
    def _unpack(key: int) -> str:
        return str(ipaddress.IPv4Address(key))
