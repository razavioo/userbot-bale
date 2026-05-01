"""
Tests for MeshCoordinator — the multi-party room manager.

Covers:
  - Peers auto-routed into rooms with capacity tracking
  - Room overflow creates new sessions
  - Peer removal and cleanup
  - Single peer joining multiple coordinator instances (multi-room)
  - Idempotent add_peer
  - shutdown lifecycle
  - Integration with MeshExitNode
"""

from __future__ import annotations

import queue
import threading

import pytest

from baleobala.vpn.mesh.multiparty import MeshCoordinator, DEFAULT_ROOM_CAPACITY
from baleobala.vpn.fake_tun import FakeTun
from baleobala.vpn.mesh.exit_node import MeshExitNode
from baleobala.vpn.transports import InMemoryTransport
from baleobala.vpn.tunnel import Tunnel


# ---------------------------------------------------------------------------
# Fake session factory for the coordinator
# ---------------------------------------------------------------------------

class FakeDataChannel:
    """Minimal DataChannel stub that satisfies Transport protocol."""

    def __init__(self, session, topic, reliable):
        self._session = session
        self._topic = topic
        self._reliable = reliable
        self._closed = False
        self.mtu = 14 * 1024
        self.rate_hint = 200_000.0

    @property
    def closed(self):
        return self._closed

    def send_bytes(self, data):
        if self._closed:
            raise RuntimeError("closed")
        peer = self._session._peer
        if peer:
            with peer._lock:
                q = peer._q.setdefault(self._topic, queue.Queue())
            q.put(bytes(data))

    def recv_bytes(self, timeout=None):
        with self._session._lock:
            q = self._session._q.get(self._topic)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self._closed = True


class FakeSession:
    """Minimal session for coordinator tests."""

    def __init__(self, room_name="fake"):
        self.room_name = room_name
        self._q: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._peer: FakeSession | None = None
        self._stopped = False
        self._channels: list[FakeDataChannel] = []

    def data_channel(self, topic="vpn", *, reliable=True):
        with self._lock:
            self._q.setdefault(topic, queue.Queue())
        ch = FakeDataChannel(self, topic, reliable)
        self._channels.append(ch)
        return ch

    def stop(self):
        self._stopped = True


def _make_factory():
    """Returns (factory, created_sessions_list)."""
    created: list[FakeSession] = []

    def factory(room_name):
        s = FakeSession(room_name)
        created.append(s)
        return s

    return factory, created


# ===================================================================
# COORDINATOR CORE TESTS
# ===================================================================

class TestMeshCoordinator:

    def test_add_peer_creates_room(self):
        factory, created = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=4)
        transport = coord.add_peer(peer_id=1)
        assert transport is not None
        assert coord.peer_count() == 1
        assert coord.room_count() == 1
        assert len(created) == 1
        coord.shutdown()

    def test_peers_share_room_until_capacity(self):
        factory, created = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=3)
        for i in range(3):
            coord.add_peer(peer_id=i)
        assert coord.room_count() == 1  # all fit in one room
        assert len(created) == 1
        coord.shutdown()

    def test_overflow_creates_new_room(self):
        factory, created = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=2)
        coord.add_peer(1)
        coord.add_peer(2)
        assert coord.room_count() == 1
        coord.add_peer(3)  # overflow → new room
        assert coord.room_count() == 2
        assert len(created) == 2
        snap = coord.snapshot()
        assert snap[1] == snap[2]  # same room
        assert snap[3] != snap[1]  # different room
        coord.shutdown()

    def test_remove_peer(self):
        factory, _ = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=4)
        coord.add_peer(1)
        coord.add_peer(2)
        coord.remove_peer(1)
        assert coord.peer_count() == 1
        assert 1 not in coord.snapshot()
        assert 2 in coord.snapshot()
        coord.shutdown()

    def test_remove_unknown_peer_is_noop(self):
        factory, _ = _make_factory()
        coord = MeshCoordinator(session_factory=factory)
        coord.remove_peer(999)  # no crash
        assert coord.peer_count() == 0

    def test_add_peer_is_idempotent(self):
        factory, created = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=4)
        t1 = coord.add_peer(1)
        t2 = coord.add_peer(1)
        assert t1 is t2  # same transport returned
        assert coord.peer_count() == 1
        assert len(created) == 1
        coord.shutdown()

    def test_shutdown_stops_all_sessions(self):
        factory, created = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=2)
        coord.add_peer(1)
        coord.add_peer(2)
        coord.add_peer(3)
        coord.shutdown()
        assert all(s._stopped for s in created)
        assert coord.peer_count() == 0
        assert coord.room_count() == 0

    def test_removed_peer_frees_room_capacity(self):
        factory, _ = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=2)
        coord.add_peer(1)
        coord.add_peer(2)
        assert coord.room_count() == 1
        coord.remove_peer(1)
        # Room now has 1 free slot; next peer should fill it, not create new room.
        coord.add_peer(3)
        assert coord.room_count() == 1
        coord.shutdown()


# ===================================================================
# SCALING: 8 PEERS PER ROOM
# ===================================================================

class TestMeshScaling:

    def test_eight_peers_fit_in_one_room(self):
        factory, created = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=8)
        for i in range(8):
            coord.add_peer(i)
        assert coord.room_count() == 1
        assert coord.peer_count() == 8
        # 9th peer overflows.
        coord.add_peer(99)
        assert coord.room_count() == 2
        coord.shutdown()

    def test_sixteen_peers_across_two_rooms(self):
        factory, _ = _make_factory()
        coord = MeshCoordinator(session_factory=factory, room_capacity=8)
        for i in range(16):
            coord.add_peer(i)
        assert coord.room_count() == 2
        assert coord.peer_count() == 16
        coord.shutdown()


# ===================================================================
# SINGLE USER → MULTIPLE COORDINATORS (multi-tunnel fan-out)
# ===================================================================

class TestSingleUserMultiCoordinator:

    def test_same_peer_in_different_coordinators(self):
        """A single peer_id can appear in different coordinator instances
        representing different exit nodes / rooms — the coordinators
        don't interfere."""
        f1, _ = _make_factory()
        f2, _ = _make_factory()
        c1 = MeshCoordinator(session_factory=f1, room_capacity=4)
        c2 = MeshCoordinator(session_factory=f2, room_capacity=4)
        t1 = c1.add_peer(42)
        t2 = c2.add_peer(42)
        assert t1 is not t2
        assert c1.peer_count() == 1
        assert c2.peer_count() == 1
        c1.shutdown(); c2.shutdown()


# ===================================================================
# INTEGRATION: COORDINATOR + MESHEXITNODE
# ===================================================================

class TestCoordinatorWithExitNode:

    def test_coordinator_transports_integrate_with_exit_node(self):
        """MeshExitNode.accept_client works with a transport returned
        by the coordinator (both satisfy the Transport protocol)."""
        tun = FakeTun("mesh0")
        mesh = MeshExitNode(tun, pool_cidr="10.77.0.0/24")
        mesh.start()
        try:
            srv, cli = InMemoryTransport.pair(mtu=200)
            a = mesh.accept_client(42, srv, mtu_override=200)
            assert 42 in mesh.snapshot()
            assert a.client.startswith("10.77.0.")
            mesh.drop_client(42)
        finally:
            mesh.stop(); tun.close()
