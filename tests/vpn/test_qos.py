from __future__ import annotations

import struct

from baleobala.vpn.qos import (
    LEVEL_ACK,
    LEVEL_BULK,
    LEVEL_CONTROL,
    PriorityQueue,
    classify,
)


def _ipv4(proto: int, payload: bytes = b"", *, src_port: int = 0, dst_port: int = 0,
          tcp_flags: int = 0, tcp_payload_len: int = 0) -> bytes:
    """Forge a minimally-valid IPv4 packet for classifier testing."""
    ihl = 5
    ver_ihl = (4 << 4) | ihl
    total_len = 20 + len(payload) + (20 if proto == 6 else 8 if proto == 17 else 0) + tcp_payload_len
    ip = struct.pack(
        "!BBHHHBBHII",
        ver_ihl, 0, total_len, 0, 0, 64, proto, 0,
        0x0a000001, 0x0a000002,
    )
    if proto == 1:  # ICMP
        return ip + b"\x08\x00" + b"\x00" * 6 + payload
    if proto == 17:  # UDP
        udp = struct.pack("!HHHH", src_port, dst_port, 8 + len(payload), 0)
        return ip + udp + payload
    if proto == 6:  # TCP
        data_off = 5  # no options
        flags = tcp_flags
        tcp = struct.pack(
            "!HHIIBBHHH",
            src_port, dst_port, 0, 0,
            (data_off << 4), flags, 8192, 0, 0,
        )
        return ip + tcp + b"\x00" * tcp_payload_len
    return ip + payload


def test_classify_icmp_is_control():
    assert classify(_ipv4(1)) == LEVEL_CONTROL


def test_classify_dns_is_control():
    # UDP to port 53
    assert classify(_ipv4(17, dst_port=53)) == LEVEL_CONTROL
    assert classify(_ipv4(17, src_port=53)) == LEVEL_CONTROL


def test_classify_tcp_syn_is_control():
    assert classify(_ipv4(6, dst_port=443, tcp_flags=0x02)) == LEVEL_CONTROL


def test_classify_tcp_ack_only_is_ack_level():
    # Flags=ACK only, zero payload → small, LEVEL_ACK
    assert classify(_ipv4(6, dst_port=443, tcp_flags=0x10, tcp_payload_len=0)) == LEVEL_ACK


def test_classify_tcp_bulk_is_bulk():
    assert classify(_ipv4(6, dst_port=443, tcp_flags=0x10, tcp_payload_len=1200)) == LEVEL_BULK


def test_classify_malformed_is_bulk():
    assert classify(b"") == LEVEL_BULK
    assert classify(b"\x00" * 3) == LEVEL_BULK  # too short


def test_priority_queue_emits_control_first():
    q = PriorityQueue()
    bulk_a = _ipv4(6, dst_port=443, tcp_flags=0x10, tcp_payload_len=1200)
    bulk_b = _ipv4(6, dst_port=443, tcp_flags=0x10, tcp_payload_len=1200)
    ctrl = _ipv4(1)  # ICMP
    ack = _ipv4(6, dst_port=443, tcp_flags=0x10, tcp_payload_len=0)

    # Enqueue bulk first, then control+ack after, to verify strict
    # priority (not FIFO).
    q.put(bulk_a); q.put(bulk_b); q.put(ack); q.put(ctrl)
    order = [q.get(timeout=0.1) for _ in range(4)]
    assert order[0] == ctrl
    assert order[1] == ack
    assert order[2:] == [bulk_a, bulk_b]  # FIFO within level


def test_priority_queue_bulk_cap_drops_oldest():
    from baleobala.vpn.qos import CAPS
    q = PriorityQueue()
    bulk_cap = CAPS[LEVEL_BULK]
    # Push cap+10 bulk packets; expect 10 drops and queue size == cap.
    for i in range(bulk_cap + 10):
        q.put(_ipv4(6, dst_port=443, tcp_flags=0x10, tcp_payload_len=1200))
    assert q.stats.enqueued[LEVEL_BULK] == bulk_cap + 10
    assert q.stats.dropped[LEVEL_BULK] == 10
