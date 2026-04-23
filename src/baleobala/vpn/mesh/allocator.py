"""
IP allocator: hand out /30 slices of a /16 to connecting clients.

A /30 holds 4 addresses: network, gateway (exit node), client, broadcast.
With 10.77.0.0/16 we have 16384 /30 slots — more than enough for any
realistic Bale contact list.

Layout per slot:
    10.77.X.Y+0  network
    10.77.X.Y+1  exit node (gateway visible to this client)
    10.77.X.Y+2  client
    10.77.X.Y+3  broadcast

Allocation is sticky per-peer when possible: the same Bale user_id
reconnects to the same slot so static routes and fingerprint-based
firewalls on the client stay valid.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_POOL = "10.77.0.0/16"


@dataclass(frozen=True)
class Assignment:
    peer_id: int
    slot: int
    gateway: str   # exit-node side IP
    client: str    # client side IP
    prefix: str    # e.g. "10.77.0.0/30"
    pool_cidr: str


class IpAllocator:
    def __init__(self, pool_cidr: str = DEFAULT_POOL) -> None:
        self._pool = ipaddress.ip_network(pool_cidr, strict=True)
        if self._pool.prefixlen > 28:
            raise ValueError("pool must be /28 or larger to hold /30s")
        self._subnets = list(self._pool.subnets(new_prefix=30))
        self._free: set[int] = set(range(len(self._subnets)))
        self._by_peer: dict[int, int] = {}   # peer_id → slot index
        self._by_slot: dict[int, int] = {}   # slot index → peer_id
        self._lock = threading.Lock()

    @property
    def total(self) -> int:
        return len(self._subnets)

    @property
    def free_count(self) -> int:
        with self._lock:
            return len(self._free)

    def assign(self, peer_id: int) -> Assignment:
        """Idempotent: returns the same Assignment for a repeat peer_id."""
        with self._lock:
            if peer_id in self._by_peer:
                return self._build(self._by_peer[peer_id], peer_id)
            if not self._free:
                raise RuntimeError("IP pool exhausted")
            slot = min(self._free)  # deterministic = easier ops
            self._free.discard(slot)
            self._by_peer[peer_id] = slot
            self._by_slot[slot] = peer_id
            return self._build(slot, peer_id)

    def reserve(self, slot: int, peer_id: int) -> Assignment:
        with self._lock:
            if not 0 <= slot < len(self._subnets):
                raise ValueError("slot out of range")
            existing = self._by_slot.get(slot)
            if existing is not None and existing != peer_id:
                raise RuntimeError(f"slot {slot} already reserved by peer {existing}")
            prior = self._by_peer.get(peer_id)
            if prior is not None and prior != slot:
                raise RuntimeError(f"peer {peer_id} already assigned to slot {prior}")
            self._free.discard(slot)
            self._by_peer[peer_id] = slot
            self._by_slot[slot] = peer_id
            return self._build(slot, peer_id)

    def release(self, peer_id: int) -> None:
        with self._lock:
            slot = self._by_peer.pop(peer_id, None)
            if slot is None:
                return
            self._by_slot.pop(slot, None)
            self._free.add(slot)

    def _build(self, slot: int, peer_id: int) -> Assignment:
        net = self._subnets[slot]
        hosts = list(net.hosts())
        # For a /30, hosts() returns exactly 2 addresses: .1 (gateway)
        # and .2 (client).
        return Assignment(
            peer_id=peer_id,
            slot=slot,
            gateway=str(hosts[0]),
            client=str(hosts[1]),
            prefix=str(net),
            pool_cidr=str(self._pool),
        )
