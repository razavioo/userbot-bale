"""
End-to-end on-box test: stack a full client runner against a full
exit-node runner with a paired InMemoryTransport (optionally AEAD-
wrapped) between them. Inject IP packets into one FakeTun and verify
they emerge at the peer's FakeTun unchanged.

This is the Phase 9 acceptance proxy when a real two-host deployment
isn't available: everything above the Bale transport layer is the same
as in production, so if packets round-trip through THIS stack they'll
round-trip through a real Bale call too (modulo transport latency/MTU,
which the tests explicitly parameterize).
"""

from __future__ import annotations

import struct
import time

import pytest

from baleobala.vpn.crypto import EncryptedTransport, derive_key
from baleobala.vpn.fake_tun import FakeTun
from baleobala.vpn.runner import RunnerConfig, VpnRunner
from baleobala.vpn.transports import InMemoryTransport


def _make_icmp_echo(
    *, src: str = "10.77.0.2", dst: str = "10.77.0.1",
    identifier: int = 0x1234, seq: int = 1, payload: bytes = b"ping-body",
) -> bytes:
    """Minimal IPv4+ICMP echo-request packet. Good enough for tunnel
    round-trip tests — kernel checksums are not revalidated."""
    import ipaddress
    src_b = ipaddress.IPv4Address(src).packed
    dst_b = ipaddress.IPv4Address(dst).packed
    # ICMP: type=8 (echo), code=0, checksum=0, id, seq, payload
    icmp = struct.pack("!BBHHH", 8, 0, 0, identifier, seq) + payload
    total_len = 20 + len(icmp)
    ip = struct.pack(
        "!BBHHHBBH", 0x45, 0, total_len, 0xABCD, 0, 64, 1, 0
    ) + src_b + dst_b
    return ip + icmp


def _build_pair(
    *, psk: str | None = None, mtu: int = 300, window: int = 16,
    send_delay: float = 0.0,
):
    """Return (client_runner, exit_runner, client_tun, exit_tun, cleanups)."""
    tx_a, tx_b = InMemoryTransport.pair(mtu=mtu, send_delay=send_delay)
    if psk:
        key = derive_key(psk)
        tx_a = EncryptedTransport(tx_a, key)
        tx_b = EncryptedTransport(tx_b, key)
    mtu_floor = tx_a.mtu  # after any encryption overhead

    client_tun = FakeTun("client0")
    exit_tun = FakeTun("exit0")

    cfg = RunnerConfig(
        sess_id=0x9999, ack_timeout=0.15, window=window, mtu_override=mtu_floor,
    )
    client = VpnRunner(client_tun, tx_a, cfg)
    exit_ = VpnRunner(exit_tun, tx_b, cfg)
    return client, exit_, client_tun, exit_tun, (tx_a, tx_b)


@pytest.mark.parametrize("psk", [None, "shared-secret"])
def test_icmp_echo_round_trips_through_tunnel(psk):
    client, exit_, ctun, etun, txs = _build_pair(psk=psk)
    try:
        client.start(); exit_.start()
        pkt = _make_icmp_echo(payload=b"hello-from-client")
        ctun.inject(pkt)
        got = etun.pop_written(timeout=3.0)
        assert got == pkt, "packet arrived corrupted or missing"

        # And the reverse direction: exit-node forwards a reply back.
        reply = _make_icmp_echo(
            src="10.77.0.1", dst="10.77.0.2", identifier=0x1234, seq=2,
            payload=b"pong-back",
        )
        etun.inject(reply)
        got_r = ctun.pop_written(timeout=3.0)
        assert got_r == reply
    finally:
        client.stop(); exit_.stop()
        for t in txs:
            t.close()


def test_large_packet_fragments_and_reassembles_across_tunnel():
    # MTU=200 forces every 1400-byte packet into ~7 ARQ frames.
    client, exit_, ctun, etun, txs = _build_pair(psk="k", mtu=200)
    try:
        client.start(); exit_.start()
        big_payload = bytes((i * 13) & 0xFF for i in range(1400 - 28))  # -28 for IP+ICMP hdrs
        pkt = _make_icmp_echo(payload=big_payload)
        ctun.inject(pkt)
        got = etun.pop_written(timeout=5.0)
        assert got == pkt
    finally:
        client.stop(); exit_.stop()
        for t in txs:
            t.close()


def test_qos_prioritizes_dns_over_bulk_under_load():
    """With window=1 (stop-and-wait — the production config on audio/rpc
    transports), the QoS queue is the only ordering mechanism in play.
    DNS must jump ahead of queued bulk packets even under load."""
    # 5ms per-frame send delay simulates real transport latency (Bale's
    # SFU is ~20-50ms typically). Combined with window=1 (stop-and-wait),
    # the effective tunnel throughput is ~1 packet / (2 frames × 5ms) =
    # ~100 pps — slow enough that a QoS-jumped DNS frame can overtake
    # queued bulk packets that arrived before it.
    client, exit_, ctun, etun, txs = _build_pair(
        psk="k", mtu=200, window=1, send_delay=0.005,
    )
    try:
        client.start(); exit_.start()

        # 30 bulk TCP data packets (classified as LEVEL_BULK in qos.py).
        def tcp_bulk(i: int) -> bytes:
            payload = b"X" * 200
            tcp_hdr = struct.pack("!HHIIBBHHH",
                                  12345, 443, 0, 0,
                                  (5 << 4), 0x10,  # data-off=5, flags=ACK
                                  8192, 0, 0)
            ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + 20 + len(payload),
                             i & 0xFFFF, 0, 64, 6, 0)
            import ipaddress
            ip += ipaddress.IPv4Address("10.77.0.2").packed
            ip += ipaddress.IPv4Address("1.1.1.1").packed
            return ip + tcp_hdr + payload

        # DNS UDP to port 53 → classified LEVEL_CONTROL.
        def dns_query() -> bytes:
            payload = b"\x00\x11\x01\x20example-dns-query"
            import ipaddress
            udp = struct.pack("!HHHH", 54321, 53, 8 + len(payload), 0) + payload
            ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + 8 + len(payload),
                             0x9999, 0, 64, 17, 0)
            ip += ipaddress.IPv4Address("10.77.0.2").packed
            ip += ipaddress.IPv4Address("8.8.8.8").packed
            return ip + udp

        # Flood the tunnel with bulks first (they fill the QoS queue at
        # LEVEL_BULK), then inject the DNS AFTER the tunnel is busy.
        for i in range(30):
            ctun.inject(tcp_bulk(i))
        time.sleep(0.02)  # let a couple of bulks start draining
        dns_pkt = dns_query()
        ctun.inject(dns_pkt)

        # Collect emerging packets. With send_delay=5ms × 2 frames per
        # packet × 30 packets, total drain ≈ 300ms; we wait up to 10s.
        emerged: list[bytes] = []
        deadline = time.monotonic() + 10.0
        while len(emerged) < 31 and time.monotonic() < deadline:
            p = etun.pop_written(timeout=0.5)
            if p is None: continue
            emerged.append(p)

        assert dns_pkt in emerged, "DNS packet never made it through"
        dns_pos = emerged.index(dns_pkt)
        # Under FIFO the DNS would arrive at index 30 (last, after all
        # 30 bulks). With QoS the DNS must jump ahead of the bulks that
        # were still in the QoS queue when it arrived. We injected DNS
        # after ~20ms (maybe 2-4 bulks drained), so DNS should land
        # somewhere in positions 2-10, NOT 30.
        assert dns_pos <= 10, (
            f"QoS failed to prioritize DNS: landed at #{dns_pos} of "
            f"{len(emerged)} (FIFO would be #30)"
        )
    finally:
        client.stop(); exit_.stop()
        for t in txs:
            t.close()
