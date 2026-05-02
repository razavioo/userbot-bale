from __future__ import annotations

import queue

import pytest

from baleobala.vpn.mesh.allocator import IpAllocator
from baleobala.vpn.mesh.router import PacketRouter
from baleobala.vpn.provisioning import MeshProvisionMessage
from baleobala.vpn.transports import InMemoryTransport
from baleobala.vpn.tunnel import Tunnel


def test_allocator_assigns_distinct_slots():
    a = IpAllocator("10.77.0.0/28")  # only 4 /30s
    assigns = [a.assign(i) for i in range(4)]
    clients = {x.client for x in assigns}
    assert len(clients) == 4


def test_allocator_is_sticky_per_peer():
    a = IpAllocator("10.77.0.0/24")
    first = a.assign(1234)
    second = a.assign(1234)
    assert first == second


def test_allocator_releases_on_drop():
    a = IpAllocator("10.77.0.0/28")
    x = a.assign(1)
    a.release(1)
    # Releasing frees the slot; next assign with a new peer picks it up.
    y = a.assign(99)
    assert y.prefix == x.prefix


def test_allocator_can_reserve_persisted_slot():
    a = IpAllocator("10.77.0.0/28")
    reserved = a.reserve(2, 7)
    assert reserved.slot == 2
    assert a.assign(7) == reserved


def test_allocator_exhaustion():
    a = IpAllocator("10.77.0.0/28")  # 4 slots
    for i in range(4):
        a.assign(i)
    with pytest.raises(RuntimeError):
        a.assign(999)


def test_allocator_reaps_expired_leases():
    a = IpAllocator("10.77.0.0/28", lease_ttl=10.0)
    a.assign(1)
    a.assign(2)
    # Nothing expired yet.
    assert a.reap_expired(now=__import__("time").monotonic() + 5.0) == []
    # Both expired.
    released = a.reap_expired(now=__import__("time").monotonic() + 11.0)
    assert sorted(released) == [1, 2]
    # Slots reclaimed.
    assert a.free_count == a.total
    # Heartbeat keeps a peer alive.
    a.assign(7)
    assert a.heartbeat(7) is True
    assert a.heartbeat(999) is False  # unknown peer
    # After heartbeat, advance past original-but-not-renewed deadline.
    released = a.reap_expired(now=__import__("time").monotonic() + 9.5)
    assert released == []
    released = a.reap_expired(now=__import__("time").monotonic() + 11.0)
    assert released == [7]


def test_allocator_without_leases_is_unchanged():
    a = IpAllocator("10.77.0.0/28")  # no lease_ttl
    a.assign(1)
    # No-op when leases are disabled.
    assert a.reap_expired(now=10**9) == []
    assert a.free_count == a.total - 1


def _ipv4_to_bytes(dst: str, payload: bytes = b"") -> bytes:
    import ipaddress
    dst_bytes = ipaddress.IPv4Address(dst).packed
    # 20-byte IPv4 header with the destination we care about; fields
    # beyond version/ihl/length/dst are zero for this test.
    import struct
    header = struct.pack(
        "!BBHHHBBHII",
        0x45, 0, 20 + len(payload), 0, 0, 64, 17, 0,
        0x0a000001, struct.unpack("!I", dst_bytes)[0],
    )
    return header + payload


def test_router_dispatches_to_owning_tunnel():
    a_tx, b_tx = InMemoryTransport.pair(mtu=200)
    tunnel = Tunnel(a_tx, sess_id=1, ack_timeout=0.1, mtu_override=200)
    r = PacketRouter()
    r.attach("10.77.0.2", tunnel)
    pkt = _ipv4_to_bytes("10.77.0.2")
    assert r.dispatch(pkt) is True
    # Packet actually went through (receive on b_tx).
    assert b_tx.recv_bytes(timeout=1) is not None
    r.detach("10.77.0.2")
    tunnel.stop()
    a_tx.close(); b_tx.close()


def test_router_rejects_unknown_dst():
    r = PacketRouter()
    pkt = _ipv4_to_bytes("1.2.3.4")
    assert r.dispatch(pkt) is False


def test_mesh_provision_message_roundtrip():
    msg = MeshProvisionMessage(
        version=1,
        kind="assign",
        peer_id=5,
        session_id=0x1116,
        pool_cidr="10.77.0.0/24",
        prefix="10.77.0.4/30",
        gateway_ip="10.77.0.5",
        client_ip="10.77.0.6",
        tun_mtu=1400,
        transport="dc",
    )
    decoded = MeshProvisionMessage.decode(msg.encode())
    assert decoded == msg
