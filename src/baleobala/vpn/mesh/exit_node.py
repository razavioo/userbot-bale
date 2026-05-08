"""
MeshExitNode: glue between the IP allocator, the per-client tunnels,
and the single shared TUN device.

Design
------
The exit node opens ONE TUN (`mesh0`, say `10.77.0.1/16`). Every
connecting client gets a `/30` inside the same `/16`, so Linux's
routing table sees all client addresses as directly-connected via
`mesh0`. NAT/MASQUERADE on the VPS then handles egress to the internet.

Per-connection lifecycle:
    1. Client calls us over Bale. `accept_client(peer_id, session)`
       is invoked by the Bale call handler.
    2. We allocate a /30, build the per-client Transport (DC / audio /
       etc), wrap it in a Tunnel, and register the client IP in the
       PacketRouter.
    3. TUN reads are dispatched: if dst matches a client, route via
       that client's tunnel; else it's (likely) an outbound packet
       the client is initiating — route via PacketRouter's *source*
       lookup to send the response back to the right client.

Concurrent-accept limit
-----------------------
Today `BaleApiClient.listen_incoming_calls` fires its callback on each
inbound call; the handler here accepts all of them. No RPC-level cap
is documented, but per-account Bale likely throttles rapid rings. Run
one MeshExitNode per dedicated Bale account.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from ..tun import TunDevice
from ..tunnel import Tunnel
from .allocator import Assignment, IpAllocator
from .router import PacketRouter

log = logging.getLogger(__name__)


@dataclass
class ClientSlot:
    assignment: Assignment
    tunnel: Tunnel | None
    transport: object  # holds close()
    peer_name: str | None = None
    active: bool = False
    closed: bool = False
    on_drop: Callable[[int], None] | None = None  # fired with peer_id before cleanup


class MeshExitNode:
    def __init__(
        self,
        tun: TunDevice,
        *,
        pool_cidr: str = "10.77.0.0/16",
        sess_id_base: int = 0x1111,
    ) -> None:
        self._tun = tun
        self._alloc = IpAllocator(pool_cidr)
        self._router = PacketRouter()
        self._clients: dict[int, ClientSlot] = {}
        self._lock = threading.Lock()
        self._sess_id_base = sess_id_base
        self._stop = threading.Event()
        self._tun_thread: Optional[threading.Thread] = None

    # ---- public API -------------------------------------------------------

    def start(self) -> None:
        self._tun_thread = threading.Thread(
            target=self._tun_rx_loop, name="mesh-tun-rx", daemon=True,
        )
        self._tun_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for slot in list(self._clients.values()):
                self._close_slot_locked(slot)
            self._clients.clear()
        try:
            self._tun.close()
        except Exception:  # noqa: BLE001
            pass
        if self._tun_thread is not None:
            self._tun_thread.join(timeout=2)

    def accept_client(
        self,
        peer_id: int,
        transport,  # type: ignore[no-untyped-def]
        *,
        ack_timeout: float = 0.5,
        window: int = 32,
        mtu_override: Optional[int] = None,
    ) -> Assignment:
        assignment = self._alloc.assign(peer_id)
        self.issue_client(peer_id, transport, assignment=assignment)
        self.activate_client(
            peer_id,
            ack_timeout=ack_timeout,
            window=window,
            mtu_override=mtu_override,
        )
        return assignment

    def issue_client(
        self,
        peer_id: int,
        transport,  # type: ignore[no-untyped-def]
        *,
        assignment: Assignment,
        peer_name: str | None = None,
        on_drop: Callable[[int], None] | None = None,
    ) -> Assignment:
        self._alloc.reserve(assignment.slot, peer_id)
        slot = ClientSlot(
            assignment=assignment,
            tunnel=None,
            transport=transport,
            peer_name=peer_name,
            on_drop=on_drop,
        )
        with self._lock:
            prior = self._clients.pop(peer_id, None)
            if prior is not None:
                self._close_slot_locked(prior)
            self._clients[peer_id] = slot
        return assignment

    def activate_client(
        self,
        peer_id: int,
        *,
        ack_timeout: float = 0.5,
        window: int = 32,
        mtu_override: Optional[int] = None,
    ) -> Assignment:
        with self._lock:
            slot = self._clients.get(peer_id)
        if slot is None:
            raise LookupError(f"peer {peer_id} has no pending slot")
        if slot.active and slot.tunnel is not None:
            return slot.assignment
        # The Android client hardcodes its tunnel sess_id to 0x1111
        # (DEFAULT_TUNNEL_SESS_ID in BaleVpnService.kt). Each VPN call has
        # its own dedicated LiveKit room, so per-peer disambiguation is
        # unnecessary at the frame layer. Use the base value verbatim so
        # both sides decode each other's frames; mismatched sess_ids
        # caused every Android frame to be silently dropped at the relay
        # and every relay ACK to be silently dropped at Android, producing
        # tunnel_dead within 6 s of tun up.
        tunnel = Tunnel(
            slot.transport,
            sess_id=self._sess_id_base & 0xFFFF,
            ack_timeout=ack_timeout,
            window=window,
            mtu_override=mtu_override,
        )

        def on_packet(pkt: bytes, _tun=self._tun) -> None:
            # Packet emerged from client — write to shared TUN so the
            # kernel routes it to the internet.
            try:
                _tun.write_packet(pkt)
            except OSError as e:
                log.warning("mesh: tun write failed for peer=%d: %s", peer_id, e)

        tunnel.start(on_packet=on_packet)
        with self._lock:
            current = self._clients.get(peer_id)
            if current is None:
                raise LookupError(f"peer {peer_id} disappeared before activation")
            current.tunnel = tunnel
            current.active = True
        self._router.attach(slot.assignment.client, tunnel)
        log.info("mesh: accepted peer=%d at %s", peer_id, slot.assignment.client)
        return slot.assignment

    def drop_client(self, peer_id: int) -> None:
        with self._lock:
            slot = self._clients.pop(peer_id, None)
        if slot is None:
            return
        if slot.on_drop is not None:
            try:
                slot.on_drop(peer_id)
            except Exception:  # noqa: BLE001
                log.exception("mesh: on_drop callback failed for peer=%d", peer_id)
        if slot.active:
            self._router.detach(slot.assignment.client)
        self._close_slot_locked(slot)
        self._alloc.release(peer_id)
        log.info("mesh: dropped peer=%d", peer_id)

    def snapshot(self) -> dict[int, Assignment]:
        with self._lock:
            return {pid: s.assignment for pid, s in self._clients.items()}

    # ---- internals --------------------------------------------------------

    def _tun_rx_loop(self) -> None:
        while not self._stop.is_set():
            pkt = self._tun.read_packet()
            if pkt is None:
                return
            if not pkt:
                continue
            if self._router.dispatch(pkt):
                continue
            # Unknown destination — probably the kernel's own traffic
            # (ARP, the exit-node's own outbound). Let it pass through
            # (no-op; kernel already routed it).

    def _close_slot_locked(self, slot: ClientSlot) -> None:
        if slot.closed:
            return
        slot.closed = True
        try:
            if slot.tunnel is not None:
                slot.tunnel.stop()
        except Exception:  # noqa: BLE001
            log.exception("mesh: tunnel.stop failed")
        try:
            close = getattr(slot.transport, "close", None)
            if close is not None:
                close()
        except Exception:  # noqa: BLE001
            log.exception("mesh: transport.close failed")
