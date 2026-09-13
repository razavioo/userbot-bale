"""RpcTransport over any Bale messaging backend."""

from __future__ import annotations

import pytest

from userbot_bale.vpn.transports.rpc_transport import (
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
