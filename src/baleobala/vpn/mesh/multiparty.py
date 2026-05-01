"""
Multi-party mesh coordinator.

Enables many-to-many VPN connections by leveraging Bale's LiveKit rooms
which support up to 8 participants per call.  Instead of a single 1:1
connection, this module manages:

1. **Room-level multiplexing** — one LiveKit room can carry traffic for
   up to N peers simultaneously.  Each peer gets its own topic-scoped
   DataChannel inside the same room.

2. **Multi-room sessions** — a single exit node can participate in
   several rooms concurrently, each serving a group of peers.

3. **Session pool** — reuse LiveKitSession objects across calls to
   avoid repeated handshakes; tracks which sessions still have capacity.

Architecture
------------
::

    MeshCoordinator
      ├── SessionSlot(room="room-A", session=LiveKitSession, peers=[1,2,3])
      ├── SessionSlot(room="room-B", session=LiveKitSession, peers=[4,5])
      └── ...

Each SessionSlot wraps a LiveKitSession and tracks the peers joined
through it.  When a new peer arrives, the coordinator finds a slot with
room_capacity > len(peers) or creates a new one.

Topic partitioning
------------------
Within a single LiveKit room, each peer gets its own topic so that
DataChannel payloads are routed only to the correct tunnel:

    topic = f"vpn-{peer_id}"

The SFU broadcasts to all subscribers, but each peer's tunnel only
listens to its own topic — other peers' traffic is simply ignored.

Thread safety
-------------
All public methods are safe to call from any thread.  Internal state
is guarded by a single lock (the session count is small, so contention
is negligible).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Bale LiveKit rooms support up to 8 participants.
DEFAULT_ROOM_CAPACITY = 8


@dataclass
class PeerSlot:
    """One peer inside a session slot."""
    peer_id: int
    topic: str
    transport: object | None = None  # DataChannelTransport once activated
    active: bool = False


@dataclass
class SessionSlot:
    """Wraps a LiveKitSession and tracks the peers joined through it."""
    session: object  # LiveKitSession
    room_name: str
    capacity: int
    peers: dict[int, PeerSlot] = field(default_factory=dict)
    closed: bool = False

    @property
    def available(self) -> int:
        return max(0, self.capacity - len(self.peers))

    @property
    def full(self) -> bool:
        return len(self.peers) >= self.capacity


class MeshCoordinator:
    """Manages multiple LiveKitSession rooms to serve many peers.

    Usage::

        coord = MeshCoordinator(session_factory=make_session)
        transport = coord.add_peer(peer_id=42)
        # transport is a DataChannelTransport ready for a Tunnel
        ...
        coord.remove_peer(42)
        coord.shutdown()
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[str], object],
        room_capacity: int = DEFAULT_ROOM_CAPACITY,
        dc_topic_prefix: str = "vpn",
        dc_reliable: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        session_factory
            Callable(room_name) → LiveKitSession.  The coordinator calls
            this to create a new session when all existing rooms are full.
            The factory must call .start() on the session before returning.
        room_capacity
            Max peers per room (Bale supports at least 8).
        dc_topic_prefix
            Prefix for the DataChannel topic.  Each peer gets
            ``{prefix}-{peer_id}`` as its dedicated topic.
        dc_reliable
            Whether the DataChannel is reliable (ordered).
        """
        self._factory = session_factory
        self._capacity = room_capacity
        self._topic_prefix = dc_topic_prefix
        self._reliable = dc_reliable
        self._slots: list[SessionSlot] = []
        self._peer_to_slot: dict[int, SessionSlot] = {}
        self._lock = threading.Lock()
        self._next_room = 0

    # ---- public API -------------------------------------------------------

    def add_peer(self, peer_id: int) -> object:
        """Assign a peer to a room slot and return its DataChannel transport.

        If the peer is already registered, returns its existing transport.
        If all rooms are full, a new session is created via the factory.

        Returns an object that satisfies the Transport protocol
        (send_bytes / recv_bytes / close / mtu / rate_hint).
        """
        with self._lock:
            if peer_id in self._peer_to_slot:
                slot = self._peer_to_slot[peer_id]
                ps = slot.peers[peer_id]
                if ps.transport is not None:
                    return ps.transport

            # Find a slot with room.
            target: SessionSlot | None = None
            for s in self._slots:
                if not s.closed and s.available > 0:
                    target = s
                    break

            if target is None:
                # Create a new session.
                room_name = self._make_room_name()
                session = self._factory(room_name)
                target = SessionSlot(
                    session=session,
                    room_name=room_name,
                    capacity=self._capacity,
                )
                self._slots.append(target)
                log.info("mesh-coord: created room %s (cap=%d)", room_name, self._capacity)

            topic = f"{self._topic_prefix}-{peer_id}"
            dc = target.session.data_channel(topic=topic, reliable=self._reliable)  # type: ignore[attr-defined]
            ps = PeerSlot(peer_id=peer_id, topic=topic, transport=dc, active=True)
            target.peers[peer_id] = ps
            self._peer_to_slot[peer_id] = target
            log.info(
                "mesh-coord: peer %d → room %s (topic=%s, %d/%d)",
                peer_id, target.room_name, topic,
                len(target.peers), target.capacity,
            )
            return dc

    def remove_peer(self, peer_id: int) -> None:
        """Remove a peer from its room slot."""
        with self._lock:
            slot = self._peer_to_slot.pop(peer_id, None)
            if slot is None:
                return
            ps = slot.peers.pop(peer_id, None)
            if ps and ps.transport:
                try:
                    ps.transport.close()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    log.exception("mesh-coord: transport close failed for peer %d", peer_id)
            log.info(
                "mesh-coord: removed peer %d from room %s (%d remaining)",
                peer_id, slot.room_name, len(slot.peers),
            )

    def peer_count(self) -> int:
        with self._lock:
            return len(self._peer_to_slot)

    def room_count(self) -> int:
        with self._lock:
            return len(self._slots)

    def snapshot(self) -> dict[int, str]:
        """Return peer_id → room_name mapping."""
        with self._lock:
            return {pid: s.room_name for pid, s in self._peer_to_slot.items()}

    def shutdown(self) -> None:
        """Stop all sessions and clear state."""
        with self._lock:
            for slot in self._slots:
                slot.closed = True
                for ps in slot.peers.values():
                    if ps.transport:
                        try:
                            ps.transport.close()  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001
                            pass
                try:
                    slot.session.stop()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    log.exception("mesh-coord: session stop failed for %s", slot.room_name)
            self._slots.clear()
            self._peer_to_slot.clear()
            log.info("mesh-coord: shutdown complete")

    # ---- internals --------------------------------------------------------

    def _make_room_name(self) -> str:
        n = self._next_room
        self._next_room += 1
        return f"mesh-room-{n}"
