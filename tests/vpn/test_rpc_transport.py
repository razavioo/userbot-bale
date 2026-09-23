"""RpcTransport over any Bale messaging backend — including full E2E shape."""

from __future__ import annotations

import base64
import threading

import pytest

from userbot_bale.vpn.transports.rpc_transport import (
    MSG_PREFIX,
    RpcTransport,
    RpcTransportNotReady,
)


class _StubApi:
    def __init__(self) -> None:
        self.sent: list[tuple[int, bytes]] = []
        self._cb = None
        self._peer = None

    def send_message(self, peer_id: int, body: bytes) -> None:
        self.sent.append((peer_id, body))

    def listen_messages(self, peer_id: int, cb) -> None:
        self._peer = peer_id
        self._cb = cb

    def simulate_inbound(self, body: bytes) -> None:
        assert self._cb is not None
        self._cb(body)


class _FullBackend:
    """Implements the complete MessagingBackend protocol (start/stop too)."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.sent: list[tuple[int, bytes, int]] = []
        self._listeners: dict[int, object] = {}
        self._lock = threading.Lock()

    def start(self, timeout: float = 15.0) -> None:
        del timeout
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        self.started = False

    def send_message(self, peer_id: int, body: bytes, *, peer_type: int = 1) -> None:
        self.sent.append((peer_id, body, peer_type))

    def listen_messages(self, peer_id: int, callback) -> None:  # noqa: ANN001
        with self._lock:
            self._listeners[peer_id] = callback

    def deliver(self, peer_id: int, body: bytes) -> None:
        with self._lock:
            cb = self._listeners.get(peer_id)
        assert cb is not None, f"no listener for peer {peer_id}"
        cb(body)


def test_roundtrip_via_stub_api():
    api = _StubApi()
    t = RpcTransport(api, peer_id=42)
    t.send_bytes(b"\x01\x02\x03hello")
    assert len(api.sent) == 1
    peer, body = api.sent[0]
    assert peer == 42
    # feed the same body back as if the peer echoed it
    api.simulate_inbound(body)
    got = t.recv_bytes(timeout=0.5)
    assert got == b"\x01\x02\x03hello"


def test_e2e_roundtrip_via_full_messaging_backend():
    backend = _FullBackend()
    backend.start()
    try:
        left = RpcTransport(backend, peer_id=7)
        right = RpcTransport(backend, peer_id=7)

        payload = bytes(range(256)) * 4  # 1024B binary frame
        left.send_bytes(payload)
        assert len(backend.sent) == 1
        peer, body, peer_type = backend.sent[0]
        assert peer == 7
        assert peer_type == 1
        assert body.decode("utf-8").startswith(MSG_PREFIX)

        # deliver to the "other" side's listener
        backend.deliver(7, body)
        got = right.recv_bytes(timeout=1.0)
        assert got == payload
    finally:
        backend.stop()
        assert backend.stopped


def test_e2e_multiple_frames_preserve_order():
    backend = _FullBackend()
    sender = RpcTransport(backend, peer_id=9)
    receiver = RpcTransport(backend, peer_id=9)

    frames = [f"frame-{i}".encode("ascii") for i in range(5)]
    for frame in frames:
        sender.send_bytes(frame)
    for _, body, _ in backend.sent:
        backend.deliver(9, body)

    received = [receiver.recv_bytes(timeout=0.5) for _ in frames]
    assert received == frames


def test_e2e_rejects_oversize_frame_before_send():
    backend = _FullBackend()
    t = RpcTransport(backend, peer_id=1)
    oversize = b"x" * (RpcTransport.MTU + 1)
    with pytest.raises(ValueError, match="rpc MTU"):
        t.send_bytes(oversize)
    assert backend.sent == []


def test_e2e_stats_track_malformed_and_plain_chat():
    backend = _FullBackend()
    t = RpcTransport(backend, peer_id=3)

    # plain chat (no VPN prefix)
    backend.deliver(3, "salam chetori".encode("utf-8"))
    # VPN-prefixed but invalid base64
    backend.deliver(3, (MSG_PREFIX + "!!!not-base64!!!").encode("utf-8"))
    # non-utf8 body
    backend.deliver(3, b"\xff\xfe\x00")

    stats = t.stats
    assert stats["unexpected_prefix"] == 1
    assert stats["b64_failures"] == 1
    assert stats["decode_failures"] == 1
    assert t.recv_bytes(timeout=0.05) is None


def test_e2e_after_close_sends_are_ignored():
    backend = _FullBackend()
    t = RpcTransport(backend, peer_id=1)
    t.close()
    t.send_bytes(b"should-not-appear")
    assert backend.sent == []
    assert t.recv_bytes(timeout=0.01) is None


def test_ignores_unrelated_chat_messages():
    api = _StubApi()
    t = RpcTransport(api, peer_id=1)
    api.simulate_inbound("hello how are you".encode("utf-8"))
    assert t.recv_bytes(timeout=0.1) is None


def test_raises_when_send_unimplemented():
    class Bare:
        pass

    t = RpcTransport(Bare(), peer_id=1)
    with pytest.raises(RpcTransportNotReady):
        t.send_bytes(b"x")


def test_close_is_idempotent() -> None:
    api = _StubApi()
    t = RpcTransport(api, peer_id=1)
    t.close()
    t.close()
    assert t.recv_bytes(timeout=0.01) is None


def test_msg_prefix_is_invisible_chat_tag() -> None:
    # Zero-width space + readable tag so real chat never collides.
    assert MSG_PREFIX.startswith("​")
    assert MSG_PREFIX.endswith("bb-vpn:")
    encoded = MSG_PREFIX + base64.b64encode(b"vpn").decode("ascii")
    assert encoded.startswith(MSG_PREFIX)
