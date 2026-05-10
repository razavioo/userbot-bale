from __future__ import annotations

import queue
import threading

import pytest

pytest.importorskip("livekit")

from baleobala.bale.livekit_backend import LiveKitDataChannel
from baleobala.vpn.tunnel import Tunnel


class _FakeSession:
    """Minimal stand-in for LiveKitSession. Two instances pair up like
    InMemoryTransport.pair() — publish_data on one lands in the other's
    recv queue, keyed by topic."""

    def __init__(self) -> None:
        self._q_by_topic: dict[str, "queue.Queue[bytes | None]"] = {}
        self.peer: "_FakeSession | None" = None
        self._lock = threading.Lock()

    def data_channel(self, topic: str = "vpn", *, reliable: bool = True):
        with self._lock:
            self._q_by_topic.setdefault(topic, queue.Queue())
        return LiveKitDataChannel(self, topic=topic, reliable=reliable)  # type: ignore[arg-type]

    # Methods LiveKitDataChannel expects on the session:
    def _submit_data(self, payload: bytes, *, topic: str, reliable: bool) -> None:
        assert self.peer is not None
        with self.peer._lock:
            q = self.peer._q_by_topic.setdefault(topic, queue.Queue())
        q.put(bytes(payload))

    def _recv_data(self, topic: str, timeout):
        with self._lock:
            q = self._q_by_topic.get(topic)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _close_data(self, topic: str) -> None:
        with self._lock:
            q = self._q_by_topic.pop(topic, None)
        if q is not None:
            q.put(None)

    def is_terminal(self) -> bool:
        return False


def _pair() -> tuple[_FakeSession, _FakeSession]:
    a, b = _FakeSession(), _FakeSession()
    a.peer = b
    b.peer = a
    return a, b


def test_datachannel_transport_tunnels_a_packet():
    a, b = _pair()
    tx_a = a.data_channel(topic="vpn")
    tx_b = b.data_channel(topic="vpn")

    # LiveKitDataChannel already satisfies the Transport protocol.
    ta = Tunnel(tx_a, sess_id=5, ack_timeout=0.1)
    tb = Tunnel(tx_b, sess_id=5, ack_timeout=0.1)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        tb.start(on_packet=rx.put)
        ta.start(on_packet=lambda _: None)
        ta.send_packet(b"\x45\x00\x00\x20hello-ip-packet")
        got = rx.get(timeout=2)
        assert got == b"\x45\x00\x00\x20hello-ip-packet"
    finally:
        ta.stop()
        tb.stop()
        tx_a.close()
        tx_b.close()


def test_topic_isolation():
    a, b = _pair()
    ch_vpn = a.data_channel(topic="vpn")
    ch_ctl = a.data_channel(topic="ctl")
    # peer only listens on ctl:
    b.data_channel(topic="ctl")
    ch_vpn.send_bytes(b"should-not-arrive-on-ctl")
    # drain the 'vpn' landing queue on b (where it actually landed)
    ch_b_vpn = b.data_channel(topic="vpn")
    assert ch_b_vpn.recv_bytes(timeout=0.2) == b"should-not-arrive-on-ctl"
    # ctl channel saw nothing
    ch_b_ctl_q = b._q_by_topic["ctl"]
    assert ch_b_ctl_q.qsize() == 0
    # cleanup
    for c in (ch_vpn, ch_ctl, ch_b_vpn):
        c.close()
