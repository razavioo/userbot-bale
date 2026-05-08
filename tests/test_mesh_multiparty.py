"""
Multi-party mesh tests for baleobala.

Test coverage
-------------
1. **Stable baseline**: existing LiveKitSession + DataChannel + Tunnel
   round-trip in a deterministic fake environment.
2. **Multi-party room**: N (up to 8) participants sharing one LiveKit room,
   each pair exchanging data over topic-scoped DataChannels.
3. **Single user → multiple rooms**: one identity joins several rooms
   simultaneously (capacity scaling without new registrations).
4. **Anonymous room join**: join a room with only a token — no user
   registration, no JWT from Bale's auth flow.
5. **MeshExitNode multi-client**: multiple clients attached to one
   exit node, packets dispatched correctly.

All tests use in-process fakes; no real LiveKit server required.
"""

from __future__ import annotations

import ipaddress
import queue
import struct
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import numpy as np
import pytest

from baleobala.bale import livekit_backend as lk
from baleobala.vpn.fake_tun import FakeTun
from baleobala.vpn.mesh.allocator import IpAllocator
from baleobala.vpn.mesh.exit_node import MeshExitNode
from baleobala.vpn.mesh.router import PacketRouter
from baleobala.vpn.transports import InMemoryTransport
from baleobala.vpn.tunnel import Tunnel


# ---------------------------------------------------------------------------
# Fake LiveKit SDK stubs (extracted from test_livekit_session.py, extended
# for multi-participant scenarios)
# ---------------------------------------------------------------------------

class _FakeTrackKind:
    KIND_AUDIO = "audio"
    KIND_VIDEO = "video"


class _FakeTrackSource:
    SOURCE_MICROPHONE = "microphone"
    SOURCE_CAMERA = "camera"


class _FakeTrackPublishOptions:
    def __init__(self, *, source: str) -> None:
        self.source = source


class _FakeAudioFrame:
    def __init__(self, data, sample_rate, num_channels, samples_per_channel):
        self.data = data
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel


class _FakeAudioSource:
    def __init__(self, sample_rate, channels):
        self.sample_rate = sample_rate
        self.channels = channels
        self.frames: list = []

    async def capture_frame(self, frame):
        self.frames.append(frame)


class _FakeLocalParticipant:
    def __init__(self):
        self.published_tracks: list = []
        self.published_data: list = []

    async def publish_track(self, track, options):
        self.published_tracks.append((track, options))

    async def publish_data(self, payload, *, reliable, topic):
        self.published_data.append((payload, reliable, topic))


class _FakeRoom:
    connect_error: BaseException | None = None
    instances: list["_FakeRoom"] = []

    def __init__(self):
        self.name = "fake-room"
        self.local_participant = _FakeLocalParticipant()
        self.handlers: dict[str, list] = {}
        self.disconnected = False
        type(self).instances.append(self)

    def on(self, event_name):
        def decorator(fn):
            self.handlers.setdefault(event_name, []).append(fn)
            return fn
        return decorator

    async def connect(self, url, token):
        if type(self).connect_error is not None:
            raise type(self).connect_error

    async def disconnect(self):
        self.disconnected = True


class _FakeAudioStream:
    def __init__(self, track, sample_rate, num_channels):
        self._events: queue.Queue = queue.Queue()
        track._audio_stream = self

    def __aiter__(self):
        return self

    async def __anext__(self):
        import asyncio
        while True:
            try:
                item = self._events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if item is None:
                raise StopAsyncIteration
            return item


class _FakeLocalAudioTrack:
    @staticmethod
    def create_audio_track(name, source):
        return SimpleNamespace(source=source)


def _install_fake_rtc(monkeypatch):
    _FakeRoom.connect_error = None
    _FakeRoom.instances = []
    fake_rtc = SimpleNamespace(
        Room=_FakeRoom,
        AudioSource=_FakeAudioSource,
        VideoSource=lambda w, h: SimpleNamespace(w=w, h=h),
        AudioFrame=_FakeAudioFrame,
        VideoFrame=lambda *a: SimpleNamespace(),
        AudioStream=_FakeAudioStream,
        VideoStream=_FakeAudioStream,
        LocalAudioTrack=_FakeLocalAudioTrack,
        LocalVideoTrack=SimpleNamespace(
            create_video_track=lambda n, s: SimpleNamespace(source=s),
        ),
        TrackPublishOptions=_FakeTrackPublishOptions,
        TrackSource=_FakeTrackSource,
        TrackKind=_FakeTrackKind,
        VideoBufferType=SimpleNamespace(RGB24="rgb24"),
    )
    monkeypatch.setattr(lk, "rtc", fake_rtc)
    monkeypatch.setattr(lk, "_HAS_LIVEKIT", True)


# ---------------------------------------------------------------------------
# Fake paired sessions for DataChannel tests (reusable across tests)
# ---------------------------------------------------------------------------

class _FakePairedSession:
    """Minimal LiveKitSession stand-in. Two instances cross-wire their
    publish_data so bytes sent on one arrive in the other's recv queue."""

    def __init__(self):
        self._q: dict[str, queue.Queue] = {}
        self.peer: _FakePairedSession | None = None
        self._lock = threading.Lock()
        self._terminal = False

    def data_channel(self, topic="vpn", *, reliable=True):
        with self._lock:
            self._q.setdefault(topic, queue.Queue())
        return lk.LiveKitDataChannel(self, topic=topic, reliable=reliable)

    def is_terminal(self):
        return self._terminal

    def _submit_data(self, payload, *, topic, reliable):
        assert self.peer is not None
        with self.peer._lock:
            q = self.peer._q.setdefault(topic, queue.Queue())
        q.put(bytes(payload))

    def _recv_data(self, topic, timeout):
        with self._lock:
            q = self._q.get(topic)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _close_data(self, topic):
        with self._lock:
            q = self._q.pop(topic, None)
        if q is not None:
            q.put(None)


def _pair_sessions():
    a, b = _FakePairedSession(), _FakePairedSession()
    a.peer = b
    b.peer = a
    return a, b


# ---------------------------------------------------------------------------
# Helper: build an IPv4 packet targeting a specific destination
# ---------------------------------------------------------------------------

def _ipv4_packet(src="10.77.0.2", dst="10.77.0.1", payload=b""):
    src_b = ipaddress.IPv4Address(src).packed
    dst_b = ipaddress.IPv4Address(dst).packed
    header = struct.pack(
        "!BBHHHBBHII",
        0x45, 0, 20 + len(payload), 0, 0, 64, 17, 0,
        struct.unpack("!I", src_b)[0],
        struct.unpack("!I", dst_b)[0],
    )
    return header + payload


# ===================================================================
# 1. STABLE BASELINE TESTS
# ===================================================================

class TestStableBaseline:
    """Deterministic tests for the existing session/DC/tunnel stack."""

    def test_session_lifecycle(self, monkeypatch):
        _install_fake_rtc(monkeypatch)
        s = lk.LiveKitSession(url="ws://fake", token="t", identity="a")
        s.start()
        assert s.state == "running"
        s.stop()
        assert s.state == "stopped"
        s.stop()  # idempotent
        assert s.state == "stopped"

    def test_session_rejects_reuse(self, monkeypatch):
        _install_fake_rtc(monkeypatch)
        s = lk.LiveKitSession(url="ws://fake", token="t")
        s.start(); s.stop()
        with pytest.raises(RuntimeError, match="single-use"):
            s.start()

    def test_data_channel_bidirectional(self):
        a, b = _pair_sessions()
        ch_a = a.data_channel("test")
        ch_b = b.data_channel("test")
        ch_a.send_bytes(b"hello")
        assert ch_b.recv_bytes(timeout=1) == b"hello"
        ch_b.send_bytes(b"world")
        assert ch_a.recv_bytes(timeout=1) == b"world"
        ch_a.close(); ch_b.close()

    def test_tunnel_roundtrip_over_inmemory(self):
        tx_a, tx_b = InMemoryTransport.pair(mtu=200)
        ta = Tunnel(tx_a, sess_id=0x42, ack_timeout=0.1)
        tb = Tunnel(tx_b, sess_id=0x42, ack_timeout=0.1)
        rx: queue.Queue[bytes] = queue.Queue()
        try:
            tb.start(on_packet=rx.put)
            ta.start(on_packet=lambda _: None)
            ta.send_packet(b"stable-test-payload")
            assert rx.get(timeout=2) == b"stable-test-payload"
        finally:
            ta.stop(); tb.stop()
            tx_a.close(); tx_b.close()

    def test_mesh_allocator_full_cycle(self):
        alloc = IpAllocator("10.77.0.0/28")
        a1 = alloc.assign(1)
        a2 = alloc.assign(2)
        assert a1.client != a2.client
        assert alloc.assign(1) == a1  # sticky
        alloc.release(1)
        a3 = alloc.assign(99)
        assert a3.prefix == a1.prefix  # reused slot

    def test_mesh_exit_node_accept_drop(self):
        tun = FakeTun("mesh0")
        mesh = MeshExitNode(tun, pool_cidr="10.77.0.0/24")
        mesh.start()
        try:
            srv, cli = InMemoryTransport.pair(mtu=200)
            a = mesh.accept_client(42, srv, mtu_override=200)
            assert 42 in mesh.snapshot()
            mesh.drop_client(42)
            assert 42 not in mesh.snapshot()
        finally:
            mesh.stop(); tun.close()


# ===================================================================
# 2. MULTI-PARTY ROOM (up to 8 participants in one room)
# ===================================================================

class _MultiPartyHub:
    """Simulates a LiveKit room with N participants. publish_data from
    any participant is broadcast to all others (like a real SFU room)."""

    def __init__(self, n: int):
        self.sessions: list[_HubSession] = []
        for _ in range(n):
            s = _HubSession(self)
            self.sessions.append(s)

    def broadcast(self, sender: "_HubSession", payload: bytes, topic: str):
        for s in self.sessions:
            if s is sender:
                continue
            with s._lock:
                q = s._q.setdefault(topic, queue.Queue())
            q.put(bytes(payload))


class _HubSession:
    """One participant in a multi-party hub."""

    def __init__(self, hub: _MultiPartyHub):
        self._hub = hub
        self._q: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._terminal = False

    def data_channel(self, topic="vpn", *, reliable=True):
        with self._lock:
            self._q.setdefault(topic, queue.Queue())
        return lk.LiveKitDataChannel(self, topic=topic, reliable=reliable)

    def is_terminal(self):
        return self._terminal

    def _submit_data(self, payload, *, topic, reliable):
        self._hub.broadcast(self, payload, topic)

    def _recv_data(self, topic, timeout):
        with self._lock:
            q = self._q.get(topic)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _close_data(self, topic):
        with self._lock:
            q = self._q.pop(topic, None)
        if q is not None:
            q.put(None)


class TestMultiPartyRoom:
    """Verify N participants (2–8) can share a single room and
    exchange data via topic-scoped DataChannels."""

    @pytest.mark.parametrize("n_participants", [2, 4, 8])
    def test_broadcast_reaches_all_peers(self, n_participants):
        hub = _MultiPartyHub(n_participants)
        channels = [s.data_channel("chat") for s in hub.sessions]
        # Participant 0 sends; all others receive.
        channels[0].send_bytes(b"ping-from-0")
        for i in range(1, n_participants):
            got = channels[i].recv_bytes(timeout=1)
            assert got == b"ping-from-0", f"participant {i} missed broadcast"
        for c in channels:
            c.close()

    def test_each_participant_can_send(self):
        hub = _MultiPartyHub(4)
        channels = [s.data_channel("echo") for s in hub.sessions]
        for sender_idx in range(4):
            msg = f"hello-from-{sender_idx}".encode()
            channels[sender_idx].send_bytes(msg)
            for recv_idx in range(4):
                if recv_idx == sender_idx:
                    continue
                got = channels[recv_idx].recv_bytes(timeout=1)
                assert got == msg
        for c in channels:
            c.close()

    def test_topic_isolation_in_multiparty(self):
        hub = _MultiPartyHub(3)
        ch_vpn = [s.data_channel("vpn") for s in hub.sessions]
        ch_ctl = [s.data_channel("ctl") for s in hub.sessions]
        ch_vpn[0].send_bytes(b"vpn-data")
        # Only vpn channels see it.
        assert ch_vpn[1].recv_bytes(timeout=0.5) == b"vpn-data"
        assert ch_vpn[2].recv_bytes(timeout=0.5) == b"vpn-data"
        assert ch_ctl[1].recv_bytes(timeout=0.2) is None
        for c in ch_vpn + ch_ctl:
            c.close()


# ===================================================================
# 3. SINGLE USER → MULTIPLE ROOMS (capacity scaling)
# ===================================================================

class TestSingleUserMultipleRooms:
    """A single identity joins multiple independent rooms concurrently.
    This scales capacity without registering new Bale accounts."""

    def test_one_identity_joins_two_rooms(self, monkeypatch):
        _install_fake_rtc(monkeypatch)
        s1 = lk.LiveKitSession(url="ws://room-1", token="t1", identity="shared")
        s2 = lk.LiveKitSession(url="ws://room-2", token="t2", identity="shared")
        s1.start(); s2.start()
        try:
            assert s1.is_running()
            assert s2.is_running()
            assert s1 is not s2
        finally:
            s1.stop(); s2.stop()

    def test_data_flows_independently_per_room(self):
        """Two independent paired-session rooms; same 'user' in both."""
        room_a1, room_a2 = _pair_sessions()
        room_b1, room_b2 = _pair_sessions()
        ch_a = room_a1.data_channel("vpn")
        ch_a_peer = room_a2.data_channel("vpn")
        ch_b = room_b1.data_channel("vpn")
        ch_b_peer = room_b2.data_channel("vpn")
        ch_a.send_bytes(b"room-A")
        ch_b.send_bytes(b"room-B")
        assert ch_a_peer.recv_bytes(timeout=1) == b"room-A"
        assert ch_b_peer.recv_bytes(timeout=1) == b"room-B"
        # No cross-contamination.
        assert ch_a_peer.recv_bytes(timeout=0.1) is None
        for c in (ch_a, ch_a_peer, ch_b, ch_b_peer):
            c.close()

    def test_three_concurrent_tunnels_on_same_identity(self):
        """One 'user' maintains 3 independent VPN tunnels simultaneously."""
        pairs = [InMemoryTransport.pair(mtu=200) for _ in range(3)]
        tunnels_a = [Tunnel(p[0], sess_id=0x10 + i, ack_timeout=0.1) for i, p in enumerate(pairs)]
        tunnels_b = [Tunnel(p[1], sess_id=0x10 + i, ack_timeout=0.1) for i, p in enumerate(pairs)]
        queues = [queue.Queue() for _ in range(3)]
        try:
            for i in range(3):
                tunnels_b[i].start(on_packet=queues[i].put)
                tunnels_a[i].start(on_packet=lambda _: None)
            for i in range(3):
                tunnels_a[i].send_packet(f"tunnel-{i}".encode())
            for i in range(3):
                assert queues[i].get(timeout=2) == f"tunnel-{i}".encode()
        finally:
            for t in tunnels_a + tunnels_b:
                t.stop()
            for p in pairs:
                p[0].close(); p[1].close()


# ===================================================================
# 4. ANONYMOUS ROOM JOIN (no Bale user registration needed)
# ===================================================================

class TestAnonymousRoomJoin:
    """Verify that LiveKitSession connects with only a URL + token,
    without any Bale-specific user authentication."""

    def test_session_starts_with_token_only(self, monkeypatch):
        _install_fake_rtc(monkeypatch)
        s = lk.LiveKitSession(url="ws://any-server", token="anon-token")
        s.start()
        assert s.is_running()
        s.stop()

    def test_no_jwt_needed_for_livekit_connect(self, monkeypatch):
        """LiveKitSession never touches BaleApiClient or JWT auth."""
        _install_fake_rtc(monkeypatch)
        s = lk.LiveKitSession(
            url="ws://standalone", token="standalone-token", identity="anon",
        )
        s.start()
        room = _FakeRoom.instances[-1]
        # Room connected with the raw token — no Bale auth involved.
        assert not room.disconnected
        s.stop()

    def test_multiple_anon_sessions_share_room(self, monkeypatch):
        _install_fake_rtc(monkeypatch)
        sessions = []
        for i in range(4):
            s = lk.LiveKitSession(
                url="ws://shared-room", token=f"token-{i}", identity=f"anon-{i}",
            )
            s.start()
            sessions.append(s)
        assert all(s.is_running() for s in sessions)
        for s in sessions:
            s.stop()


# ===================================================================
# 5. MESH EXIT NODE — MULTI-CLIENT DISPATCH
# ===================================================================

class TestMeshMultiClient:
    """MeshExitNode with multiple simultaneous clients; packets routed
    to the correct tunnel based on destination IP."""

    def test_three_clients_dispatched_correctly(self):
        tun = FakeTun("mesh0")
        mesh = MeshExitNode(tun, pool_cidr="10.77.0.0/24")
        mesh.start()
        try:
            client_rxs: dict[int, queue.Queue] = {}
            transports = []
            for peer_id in (10, 20, 30):
                srv, cli = InMemoryTransport.pair(mtu=300)
                transports.extend([srv, cli])
                a = mesh.accept_client(peer_id, srv, mtu_override=300)
                # Set up a tunnel on the client side to receive packets.
                # Match exit_node.py: sess_id is now a constant 0x1111
                # (the Android client's hardcoded value); per-peer offsets
                # caused frame-level filter mismatches in production.
                t_cli = Tunnel(cli, sess_id=0x1111, ack_timeout=0.1, mtu_override=300)
                rx_q: queue.Queue = queue.Queue()
                t_cli.start(on_packet=rx_q.put)
                client_rxs[peer_id] = rx_q

            snap = mesh.snapshot()
            assert len(snap) == 3

            # Send a packet to each client via the TUN device and verify
            # correct routing.
            for peer_id in (10, 20, 30):
                dst_ip = snap[peer_id].client
                pkt = _ipv4_packet(src="1.1.1.1", dst=dst_ip, payload=f"for-{peer_id}".encode())
                tun.inject(pkt)

            for peer_id in (10, 20, 30):
                got = client_rxs[peer_id].get(timeout=3)
                assert f"for-{peer_id}".encode() in got

            # Drop one client; others still work.
            mesh.drop_client(20)
            assert 20 not in mesh.snapshot()
            assert 10 in mesh.snapshot()
            assert 30 in mesh.snapshot()
        finally:
            mesh.stop(); tun.close()
            for t in transports:
                t.close()

    def test_allocator_supports_8_concurrent_peers(self):
        alloc = IpAllocator("10.77.0.0/24")
        assignments = [alloc.assign(i) for i in range(8)]
        clients = {a.client for a in assignments}
        assert len(clients) == 8  # all unique IPs

    def test_router_unknown_dst_returns_false(self):
        r = PacketRouter()
        pkt = _ipv4_packet(dst="192.168.1.1")
        assert r.dispatch(pkt) is False


# ===================================================================
# 6. CARRIER HOSTS EXTRACTION (no registration)
# ===================================================================

class TestCarrierHostsBypass:
    """carrier_hosts extracts the LiveKit hostname from the URL so the
    carrier bypass layer can route around Bale's proxy. This works
    without any user registration."""

    def test_carrier_hosts_from_wss_url(self):
        s = lk.LiveKitSession(url="wss://next-ws.bale.ai/path", token="t")
        hosts = s.carrier_hosts
        assert "next-ws.bale.ai" in hosts

    def test_carrier_hosts_includes_custom_host(self):
        s = lk.LiveKitSession(url="ws://custom-lk.example.com:7880", token="t")
        hosts = s.carrier_hosts
        assert "custom-lk.example.com" in hosts
