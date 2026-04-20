from __future__ import annotations

import queue
import threading
import time

import pytest

from baleobala.vpn.transports import InMemoryTransport
from baleobala.vpn.tunnel import Tunnel


@pytest.fixture
def tunnels():
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    a = Tunnel(a_tx, sess_id=0xABCD, ack_timeout=0.1, max_retries=5)
    b = Tunnel(b_tx, sess_id=0xABCD, ack_timeout=0.1, max_retries=5)
    yield a, b, a_tx, b_tx
    a.stop()
    b.stop()
    a_tx.close()
    b_tx.close()


def _collect(tunnel):
    q: "queue.Queue[bytes]" = queue.Queue()
    tunnel.start(on_packet=q.put)
    return q


def test_single_packet_roundtrip(tunnels):
    a, b, *_ = tunnels
    rx = _collect(b)
    a.start(on_packet=lambda _: None)
    a.send_packet(b"hello world")
    pkt = rx.get(timeout=2)
    assert pkt == b"hello world"


def test_large_packet_split_and_reassemble(tunnels):
    a, b, *_ = tunnels
    rx = _collect(b)
    a.start(on_packet=lambda _: None)
    payload = bytes((i * 7) & 0xFF for i in range(1400))  # > mtu
    a.send_packet(payload)
    pkt = rx.get(timeout=3)
    assert pkt == payload


def test_many_packets_in_order(tunnels):
    a, b, *_ = tunnels
    rx = _collect(b)
    a.start(on_packet=lambda _: None)
    packets = [f"pkt-{i}".encode() * 3 for i in range(50)]
    for p in packets:
        a.send_packet(p)
    got = [rx.get(timeout=3) for _ in packets]
    assert got == packets


def test_arq_recovers_from_loss():
    a_tx, b_tx = InMemoryTransport.pair(mtu=200, loss=0.3, rng_seed=1)
    a = Tunnel(a_tx, sess_id=1, ack_timeout=0.05, max_retries=40, window=4)
    b = Tunnel(b_tx, sess_id=1, ack_timeout=0.05, max_retries=40, window=4)
    try:
        rx: "queue.Queue[bytes]" = queue.Queue()
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)
        packets = [f"loss-{i}".encode() for i in range(20)]
        for p in packets:
            a.send_packet(p)
        got = sorted(rx.get(timeout=10) for _ in packets)
        assert got == sorted(packets)
    finally:
        a.stop()
        b.stop()
        a_tx.close()
        b_tx.close()


def test_session_id_mismatch_is_dropped():
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    a = Tunnel(a_tx, sess_id=1, ack_timeout=0.05)
    b = Tunnel(b_tx, sess_id=2, ack_timeout=0.05)  # mismatched
    try:
        rx: "queue.Queue[bytes]" = queue.Queue()
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)
        a.send_packet(b"nope")
        with pytest.raises(queue.Empty):
            rx.get(timeout=0.5)
    finally:
        a.stop()
        b.stop()
        a_tx.close()
        b_tx.close()
