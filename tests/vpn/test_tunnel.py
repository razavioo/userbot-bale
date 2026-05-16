from __future__ import annotations

import queue
import random
import threading
import time

import pytest

from baleobala.vpn.framing_vpn import VpnFlag, VpnFrame
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


def test_orphan_split_packet_is_gc_after_ttl():
    """SPLIT fragment without LAST must not leak reassembly state forever."""
    from baleobala.vpn.framing_vpn import VpnFlag, VpnFrame

    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    b = Tunnel(
        b_tx,
        sess_id=1,
        ack_timeout=0.05,
        max_retries=1,
        reassembly_timeout=0.1,
    )
    events: list[tuple[str, dict]] = []
    b.add_event_handler(lambda e, p: events.append((e, p)))
    try:
        b.start(on_packet=lambda _: None)
        # Inject a SPLIT-only frame directly via the paired transport.
        orphan = VpnFrame(
            sess_id=1, seq=42, flags=VpnFlag.SPLIT, payload=b"orphan"
        )
        a_tx.send_bytes(orphan.encode())
        # Wait long enough for the retry loop to GC.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e == "reassembly_dropped" for e, _ in events):
                break
            time.sleep(0.05)
        assert any(
            e == "reassembly_dropped" and p.get("reason") == "ttl"
            for e, p in events
        ), f"expected ttl drop event; got {events}"
    finally:
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


# --- property / scenario tests ----------------------------------------------


@pytest.mark.parametrize("seed,loss", [(0, 0.0), (1, 0.1), (2, 0.3), (3, 0.5)])
def test_random_single_frame_traffic_survives_loss(seed, loss):
    """Scenario sweep: random single-frame packets at varying loss
    rates all eventually arrive intact. ARQ + dedup carry the
    invariant. Multi-frame packets are exercised separately because
    the reassembly algorithm requires the first fragment to arrive
    before the rest of its packet."""
    rng = random.Random(seed)
    a_tx, b_tx = InMemoryTransport.pair(mtu=200, loss=loss, rng_seed=seed)
    a = Tunnel(a_tx, sess_id=0xC0DE, ack_timeout=0.05, max_retries=80, window=4)
    b = Tunnel(b_tx, sess_id=0xC0DE, ack_timeout=0.05, max_retries=80, window=4)
    try:
        rx: "queue.Queue[bytes]" = queue.Queue()
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)
        # Each packet ≤ mtu-header so split_packet always returns 1 frame.
        packets = []
        for i in range(20):
            size = rng.choice([8, 30, 80, 150, 190])
            packets.append(bytes((i + j) & 0xFF for j in range(size)))
        for p in packets:
            a.send_packet(p)
        got = []
        deadline = time.monotonic() + 15
        while len(got) < len(packets) and time.monotonic() < deadline:
            try:
                got.append(rx.get(timeout=1.0))
            except queue.Empty:
                continue
        assert sorted(got) == sorted(packets), (
            f"missing {len(packets) - len(got)} packets at loss={loss}"
        )
    finally:
        a.stop()
        b.stop()
        a_tx.close()
        b_tx.close()


def test_reassembly_handles_out_of_order_middle_fragments():
    """Reassembly tolerates middle fragments arriving in any order, as
    long as the first fragment (lowest seq) arrives first to open the
    bucket. This matches the receiver's `_find_or_open_start` heuristic
    that joins a frame to an existing bucket only when start ≤ seq."""
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    b = Tunnel(b_tx, sess_id=42, ack_timeout=0.05)
    try:
        rx: "queue.Queue[bytes]" = queue.Queue()
        b.start(on_packet=rx.put)

        payload = b"".join(bytes([i]) * 40 for i in range(5))  # 200 bytes
        chunks = [payload[i * 40 : (i + 1) * 40] for i in range(5)]
        frames = []
        for i, chunk in enumerate(chunks):
            flags = VpnFlag.SPLIT
            if i == len(chunks) - 1:
                flags |= VpnFlag.LAST
            frames.append(
                VpnFrame(sess_id=42, seq=1000 + i, flags=flags, payload=chunk)
            )

        # Send the start (lowest seq) first, then shuffle the rest.
        first, rest = frames[0], frames[1:]
        random.Random("middle-shuffle").shuffle(rest)
        a_tx.send_bytes(first.encode())
        for f in rest:
            a_tx.send_bytes(f.encode())

        assert rx.get(timeout=2.0) == payload
    finally:
        b.stop()
        a_tx.close()
        b_tx.close()


def test_retransmit_does_not_double_deliver():
    """Dedup: if our ACK is lost, the peer retransmits the same seq.
    The frame must not be delivered twice."""
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    b = Tunnel(b_tx, sess_id=7, ack_timeout=0.05)
    try:
        rx: "queue.Queue[bytes]" = queue.Queue()
        b.start(on_packet=rx.put)
        frame = VpnFrame(
            sess_id=7, seq=5, flags=VpnFlag.LAST, payload=b"once"
        )
        # Send the same frame three times (simulating ACK loss + retry).
        for _ in range(3):
            a_tx.send_bytes(frame.encode())
        first = rx.get(timeout=1.0)
        assert first == b"once"
        with pytest.raises(queue.Empty):
            rx.get(timeout=0.3)  # no duplicate delivery
    finally:
        b.stop()
        a_tx.close()
        b_tx.close()


def test_max_open_packets_drops_oldest_partial_reassembly():
    """A peer that floods SPLIT-without-LAST fragments must not blow up
    the receiver's reassembly buffer. The bound is `max_open_packets`;
    once exceeded the oldest open packet is evicted with an event.

    Seq gaps between packets must exceed the 256-window used by
    `_find_or_open_start`; otherwise a new SPLIT frame joins the
    existing bucket rather than opening its own."""
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    b = Tunnel(
        b_tx,
        sess_id=11,
        ack_timeout=0.5,
        reassembly_timeout=10.0,
        max_open_packets=4,
    )
    events: list[tuple[str, dict]] = []
    b.add_event_handler(lambda e, p: events.append((e, p)))
    try:
        b.start(on_packet=lambda _: None)
        # 6 distinct SPLIT-only packets; gap=1000 > 256 so each opens
        # its own bucket.
        for i in range(6):
            f = VpnFrame(
                sess_id=11, seq=1000 + i * 1000, flags=VpnFlag.SPLIT,
                payload=b"open-" + str(i).encode(),
            )
            a_tx.send_bytes(f.encode())
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            cap_drops = [
                e for e in events
                if e[0] == "reassembly_dropped"
                and e[1].get("reason") == "max_open_packets"
            ]
            if len(cap_drops) >= 2:
                break
            time.sleep(0.05)
        cap_drops = [
            e for e in events
            if e[0] == "reassembly_dropped"
            and e[1].get("reason") == "max_open_packets"
        ]
        assert len(cap_drops) >= 2, (
            f"expected ≥2 cap-based drops; got {[e for e in events]}"
        )
    finally:
        b.stop()
        a_tx.close()
        b_tx.close()


def test_idle_timeout_fires_exactly_once_until_traffic_resumes():
    """`tunnel_idle` is one-shot: it fires when rx has been silent
    longer than idle_timeout, and stays quiet until a new frame
    arrives (which re-arms the detector)."""
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    b = Tunnel(
        b_tx, sess_id=99, ack_timeout=0.05, idle_timeout=0.15,
    )
    idle_events: list[dict] = []
    b.add_event_handler(
        lambda e, p: idle_events.append(p) if e == "tunnel_idle" else None
    )
    try:
        b.start(on_packet=lambda _: None)
        # Wait long enough to fire once.
        time.sleep(0.4)
        first_count = len(idle_events)
        assert first_count == 1, f"expected one idle event, got {first_count}"
        # No additional events while still idle.
        time.sleep(0.3)
        assert len(idle_events) == 1, (
            "tunnel_idle re-fired while still idle — should be one-shot"
        )
        # A frame re-arms the detector; subsequent idleness fires again.
        f = VpnFrame(
            sess_id=99, seq=1, flags=VpnFlag.LAST, payload=b"alive"
        )
        a_tx.send_bytes(f.encode())
        time.sleep(0.4)
        assert len(idle_events) == 2, (
            "tunnel_idle did not re-arm after traffic resumed"
        )
    finally:
        b.stop()
        a_tx.close()
        b_tx.close()


def test_swap_transport_keeps_pending_frames_in_flight():
    """When the active carrier fails over, the retry thread re-sends
    pending frames on the new transport. The receiver on the new
    transport must see them."""
    # Build A and two B-side transports (the failover target receives the retry).
    a_tx_old, b_tx_old = InMemoryTransport.pair(mtu=200)
    a_tx_new, b_tx_new = InMemoryTransport.pair(mtu=200)
    a = Tunnel(
        a_tx_old, sess_id=88,
        ack_timeout=0.05, max_retries=20,
        mtu_override=200,
    )
    b = Tunnel(b_tx_new, sess_id=88, ack_timeout=0.05)
    try:
        rx: "queue.Queue[bytes]" = queue.Queue()
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)
        # Send while no peer is listening on the old transport — frame
        # goes pending without an ACK.
        a.send_packet(b"after-swap")
        # Now hot-swap; the retry thread re-sends on the new transport.
        a.swap_transport(a_tx_new)
        assert rx.get(timeout=3.0) == b"after-swap"
    finally:
        a.stop()
        b.stop()
        a_tx_old.close()
        b_tx_old.close()
        a_tx_new.close()
        b_tx_new.close()
