from __future__ import annotations

import queue

import pytest

from baleobala.vpn.crypto import OVERHEAD, EncryptedTransport, derive_key
from baleobala.vpn.transports import InMemoryTransport
from baleobala.vpn.tunnel import Tunnel


def test_derive_key_is_deterministic_and_32_bytes():
    k1 = derive_key("hunter2")
    k2 = derive_key("hunter2")
    assert k1 == k2
    assert len(k1) == 32
    assert derive_key("other") != k1


def test_encrypted_roundtrip_over_in_memory_transport():
    a, b = InMemoryTransport.pair(mtu=500)
    key = derive_key("shared")
    ea = EncryptedTransport(a, key)
    eb = EncryptedTransport(b, key)
    ea.send_bytes(b"secret-payload")
    got = eb.recv_bytes(timeout=1)
    assert got == b"secret-payload"


def test_wrong_key_rejects_frames():
    a, b = InMemoryTransport.pair(mtu=500)
    ea = EncryptedTransport(a, derive_key("k1"))
    eb = EncryptedTransport(b, derive_key("k2"))
    ea.send_bytes(b"nope")
    assert eb.recv_bytes(timeout=0.3) is None


def test_tampered_frame_rejected():
    a, b = InMemoryTransport.pair(mtu=500)
    key = derive_key("tamper")
    ea = EncryptedTransport(a, key)
    eb = EncryptedTransport(b, key)

    # Intercept the outbound bytes, flip a byte, inject into b's rx.
    class _Spy:
        def __init__(self, peer): self.peer = peer; self.mtu = 500
        def send_bytes(self, data):
            tampered = bytearray(data); tampered[-1] ^= 0xFF
            self.peer._rx.put(bytes(tampered))
        def recv_bytes(self, timeout=None): return None
        def close(self): pass
    ea_tamper = EncryptedTransport(_Spy(b), key)
    ea_tamper.send_bytes(b"hello")
    assert eb.recv_bytes(timeout=0.3) is None


def test_mtu_reduced_by_overhead():
    a, _ = InMemoryTransport.pair(mtu=1500)
    e = EncryptedTransport(a, derive_key("k"))
    assert e.mtu == 1500 - OVERHEAD


def test_encrypted_tunnel_end_to_end():
    """Stack Tunnel on top of EncryptedTransport and round-trip an IP
    packet. Confirms crypto overhead flows cleanly through MTU
    negotiation and ARQ."""
    a, b = InMemoryTransport.pair(mtu=400)
    key = derive_key("tunnel-psk")
    ea = EncryptedTransport(a, key)
    eb = EncryptedTransport(b, key)

    ta = Tunnel(ea, sess_id=1, ack_timeout=0.1, mtu_override=ea.mtu)
    tb = Tunnel(eb, sess_id=1, ack_timeout=0.1, mtu_override=eb.mtu)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        tb.start(on_packet=rx.put)
        ta.start(on_packet=lambda _: None)
        big = bytes((i * 13) & 0xFF for i in range(1200))  # > transport mtu
        ta.send_packet(big)
        assert rx.get(timeout=3) == big
    finally:
        ta.stop(); tb.stop()
        a.close(); b.close()
