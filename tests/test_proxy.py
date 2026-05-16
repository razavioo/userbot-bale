from __future__ import annotations

import os
import random
import socket
import threading
import time

import pytest

from baleobala.runtime import MemoryByteChannel, NullSecurityProvider, QueuedTunnelTransport, TunnelRole, TunnelSession
from baleobala.runtime.proxy import (
    DirectFirstSocks5ProxyServer,
    ProxyHub,
    ProxyPacket,
    ProxyPacketType,
    Socks5ProxyServer,
    TunnelTcpRelay,
)
from baleobala.runtime.proxy import HEADER_SIZE, MAGIC, MAX_DATA_LEN, MAX_HOST_LEN

PROXY_SECRET = b"proxy-secret"


class _FakeTransport:
    def __init__(self, mtu: int | None = None) -> None:
        if mtu is not None:
            self.mtu = mtu

    def send(self, data: bytes) -> None:
        pass

    def recv(self, timeout: float | None = None) -> bytes | None:
        return None

    def close(self) -> None:
        pass


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise EOFError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def _start_echo_server() -> tuple[int, threading.Thread]:
    ready = threading.Event()
    holder: dict[str, int] = {}

    def run() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            holder["port"] = server.getsockname()[1]
            ready.set()
            conn, _addr = server.accept()
            with conn:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        return
                    conn.sendall(data)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)
    return holder["port"], thread


def _start_socks_client(proxy_port: int, target_port: int, payload: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=2.0) as sock:
        sock.sendall(b"\x05\x01\x00")
        assert _recv_exact(sock, 2) == b"\x05\x00"
        host = b"127.0.0.1"
        sock.sendall(
            b"\x05\x01\x00\x03"
            + bytes([len(host)])
            + host
            + target_port.to_bytes(2, "big")
        )
        reply = _recv_exact(sock, 10)
        assert reply[:2] == b"\x05\x00"
        sock.sendall(payload)
        return _recv_exact(sock, len(payload))


def _wait_for_empty_proxy_queues(*hubs: ProxyHub) -> None:
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if all(not hub._queues for hub in hubs):  # noqa: SLF001
            return
        time.sleep(0.01)
    assert all(not hub._queues for hub in hubs)  # noqa: SLF001


def test_proxy_packet_roundtrip() -> None:
    packet = ProxyPacket(
        packet_type=ProxyPacketType.OPEN,
        conn_id=7,
        host="example.com",
        port=443,
    )
    assert ProxyPacket.decode(packet.encode()) == packet


def test_proxy_packet_roundtrip_with_secret() -> None:
    packet = ProxyPacket(
        packet_type=ProxyPacketType.DATA,
        conn_id=8,
        payload=b"encrypted",
    )
    encoded = packet.encode(PROXY_SECRET)
    assert ProxyPacket.decode(encoded, PROXY_SECRET) == packet
    assert ProxyPacket.decode(encoded, None) is None


def test_proxy_server_derives_large_chunks_from_transport_mtu() -> None:
    server = Socks5ProxyServer(_FakeTransport(mtu=14 * 1024))
    assert server._max_chunk_size > 13 * 1024  # noqa: SLF001


def test_proxy_server_uses_legacy_chunks_without_transport_mtu() -> None:
    server = Socks5ProxyServer(_FakeTransport())
    assert server._max_chunk_size == 180  # noqa: SLF001


def test_relay_idle_timeout_tracks_recent_activity() -> None:
    relay = TunnelTcpRelay(_FakeTransport(), idle_timeout=0.01)
    assert not relay._idle_expired()  # noqa: SLF001
    relay._mark_connection_started()  # noqa: SLF001
    time.sleep(0.02)
    assert relay._idle_expired()  # noqa: SLF001
    relay._touch_activity()  # noqa: SLF001
    assert not relay._idle_expired()  # noqa: SLF001
    relay._mark_connection_finished()  # noqa: SLF001
    time.sleep(0.02)
    assert relay._idle_expired()  # noqa: SLF001
    relay.stop()


def test_proxy_hub_handshake() -> None:
    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_hub = ProxyHub(left)
    right_hub = ProxyHub(right)
    assert left_hub.negotiate(timeout=1.0)
    assert right_hub.negotiate(timeout=1.0)

    left_hub.close()
    right_hub.close()
    left.close()
    right.close()


def test_socks5_proxy_roundtrip_over_memory_tunnel() -> None:
    echo_port, _echo_thread = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_once, daemon=True)
    relay_thread.start()

    server = Socks5ProxyServer(left_transport, listen_host="127.0.0.1", listen_port=0, secret=PROXY_SECRET)
    server_thread = threading.Thread(target=server.serve_once, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_host == "127.0.0.1"
    assert server.bound_port is not None

    with socket.create_connection(("127.0.0.1", server.bound_port), timeout=2.0) as sock:
        sock.sendall(b"\x05\x01\x00")
        assert _recv_exact(sock, 2) == b"\x05\x00"
        host = b"127.0.0.1"
        sock.sendall(
            b"\x05\x01\x00\x03"
            + bytes([len(host)])
            + host
            + echo_port.to_bytes(2, "big")
        )
        reply = _recv_exact(sock, 10)
        assert reply[:2] == b"\x05\x00"
        sock.sendall(b"proxy hello")
        assert _recv_exact(sock, len(b"proxy hello")) == b"proxy hello"

    _wait_for_empty_proxy_queues(server._hub, relay._hub)  # noqa: SLF001

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)


def test_direct_first_proxy_uses_direct_socket_when_available() -> None:
    echo_port, _echo_thread = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()

    server = DirectFirstSocks5ProxyServer(
        left_transport,
        listen_host="127.0.0.1",
        listen_port=0,
        secret=PROXY_SECRET,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_port is not None

    assert _start_socks_client(server.bound_port, echo_port, b"direct hello") == b"direct hello"
    assert not relay._hub._queues  # noqa: SLF001

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)


def test_direct_first_proxy_falls_back_to_tunnel_when_direct_fails() -> None:
    echo_port, _echo_thread = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()

    server = DirectFirstSocks5ProxyServer(
        left_transport,
        listen_host="127.0.0.1",
        listen_port=0,
        secret=PROXY_SECRET,
    )
    server._connect_direct = lambda _host, _port: None  # type: ignore[method-assign]  # noqa: SLF001
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_port is not None

    assert _start_socks_client(server.bound_port, echo_port, b"fallback hello") == b"fallback hello"

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)


def test_direct_first_proxy_respects_direct_only_domains() -> None:
    echo_port, _echo_thread = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()

    server = DirectFirstSocks5ProxyServer(
        left_transport,
        listen_host="127.0.0.1",
        listen_port=0,
        secret=PROXY_SECRET,
        direct_only_domains=["example.test"],
    )
    server._connect_direct = lambda _host, _port: None  # type: ignore[method-assign]  # noqa: SLF001
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_port is not None

    with socket.create_connection(("127.0.0.1", server.bound_port), timeout=2.0) as sock:
        sock.sendall(b"\x05\x01\x00")
        assert _recv_exact(sock, 2) == b"\x05\x00"
        host = b"shop.example.test"
        sock.sendall(
            b"\x05\x01\x00\x03"
            + bytes([len(host)])
            + host
            + echo_port.to_bytes(2, "big")
        )
        reply = _recv_exact(sock, 10)
        assert reply[:2] == b"\x05\x05"

    assert not relay._hub._queues  # noqa: SLF001

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)


def test_direct_first_proxy_respects_tunnel_only_domains() -> None:
    echo_port, _echo_thread = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()

    direct_attempts = {"count": 0}
    server = DirectFirstSocks5ProxyServer(
        left_transport,
        listen_host="127.0.0.1",
        listen_port=0,
        secret=PROXY_SECRET,
        tunnel_only_domains=["127.0.0.1"],
    )

    def count_direct(_host, _port):  # noqa: ANN001
        direct_attempts["count"] += 1
        return None

    server._connect_direct = count_direct  # type: ignore[method-assign]  # noqa: SLF001
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_port is not None

    assert _start_socks_client(server.bound_port, echo_port, b"tunnel only") == b"tunnel only"
    assert direct_attempts["count"] == 0

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)


def test_proxy_policy_parses_iran_and_gfw_domain_lists() -> None:
    from baleobala.runtime.proxy_policy import parse_domain_lines, parse_gfwlist_domains

    iran_domains = parse_domain_lines(
        """
        # comment
        digikala.com
        https://www.shaparak.ir/path
        *.example.ir
        """
    )
    assert {"digikala.com", "www.shaparak.ir", "example.ir"} <= iran_domains

    gfw_domains = parse_gfwlist_domains(
        """
        ! comment
        ||blocked.example^
        |https://www.youtube.com/path
        @@||allowed.example^
        """
    )
    assert {"blocked.example", "www.youtube.com"} <= gfw_domains
    assert "allowed.example" not in gfw_domains


def test_proxy_policy_direct_domains_win_over_tunnel_domains(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_PROXY_DIRECT_ONLY_DOMAIN", "digikala.com")
    monkeypatch.setenv("BALEOBALA_PROXY_TUNNEL_ONLY_DOMAIN", "digikala.com,blocked.example")
    monkeypatch.setenv("BALEOBALA_IRAN_HOSTED_DOMAINS_URL", "file:///missing")
    monkeypatch.setenv("BALEOBALA_GFWLIST_URL", "file:///missing")

    from baleobala.runtime.proxy_policy import load_proxy_domain_policy

    policy = load_proxy_domain_policy(timeout=0.01)

    assert "digikala.com" in policy.direct_only_domains
    assert "digikala.com" not in policy.tunnel_only_domains
    assert "blocked.example" in policy.tunnel_only_domains


def test_proxy_policy_loads_bundled_lists_without_network(monkeypatch) -> None:
    from baleobala.runtime import proxy_policy

    monkeypatch.setattr(proxy_policy, "_fetch_text", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")))

    policy = proxy_policy.load_proxy_domain_policy(refresh=False, timeout=0.01)

    assert "ir" in policy.direct_only_domains
    assert "digikala.com" in policy.direct_only_domains
    assert "youtube.com" in policy.tunnel_only_domains


def test_socks5_proxy_multiplexes_multiple_connections() -> None:
    echo_a, _thread_a = _start_echo_server()
    echo_b, _thread_b = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()

    server = Socks5ProxyServer(left_transport, listen_host="127.0.0.1", listen_port=0, secret=PROXY_SECRET)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_port is not None

    results: list[bytes | None] = [None, None]

    def run_client(idx: int, target_port: int, payload: bytes) -> None:
        results[idx] = _start_socks_client(server.bound_port, target_port, payload)

    t1 = threading.Thread(target=run_client, args=(0, echo_a, b"alpha"), daemon=True)
    t2 = threading.Thread(target=run_client, args=(1, echo_b, b"bravo"), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert results == [b"alpha", b"bravo"]

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)


# --- ProxyPacket adversarial + property scenarios ----------------------------
#
# These exercise the wire format directly rather than going through SOCKS5.
# Each test enumerates many cases through pytest.parametrize so a single
# scenario stands in for a class of behaviors.


_PACKET_TYPES_NO_HOST = [
    ProxyPacketType.HELLO,
    ProxyPacketType.OPEN_OK,
    ProxyPacketType.DATA,
    ProxyPacketType.CLOSE,
    ProxyPacketType.ERROR,
    ProxyPacketType.HELLO_ACK,
]


@pytest.mark.parametrize("ptype", _PACKET_TYPES_NO_HOST)
@pytest.mark.parametrize("secret", [None, PROXY_SECRET])
def test_proxy_packet_roundtrips_every_type_and_secret_mode(ptype, secret) -> None:
    """Encode/decode is the identity for every packet type, with or
    without encryption, for arbitrary conn_id/port/payload values."""
    rng = random.Random(f"{ptype}-{secret!r}")
    for _ in range(8):
        payload = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 4096)))
        port = rng.randrange(0, 0xFFFF + 1) if ptype == ProxyPacketType.DATA else 0
        packet = ProxyPacket(
            packet_type=ptype,
            conn_id=rng.randrange(0, 0x1_0000_0000),
            port=port,
            payload=payload,
        )
        decoded = ProxyPacket.decode(packet.encode(secret), secret)
        assert decoded == packet


@pytest.mark.parametrize("secret", [None, PROXY_SECRET])
def test_proxy_packet_open_carries_host(secret) -> None:
    """OPEN packets must roundtrip their host string; non-OPEN must not."""
    rng = random.Random(f"open-{secret!r}")
    for _ in range(8):
        host_len = rng.randrange(1, MAX_HOST_LEN + 1)
        host = "".join(chr(rng.randrange(0x21, 0x7E)) for _ in range(host_len))
        packet = ProxyPacket(
            packet_type=ProxyPacketType.OPEN,
            conn_id=rng.randrange(0, 0x1_0000_0000),
            host=host,
            port=rng.randrange(1, 0x10000),
        )
        decoded = ProxyPacket.decode(packet.encode(secret), secret)
        assert decoded is not None
        assert decoded.host == host
        assert decoded.port == packet.port


@pytest.mark.parametrize("ptype", _PACKET_TYPES_NO_HOST)
def test_proxy_packet_non_open_rejects_host(ptype) -> None:
    """encode() guards: only OPEN may carry a host string."""
    with pytest.raises(ValueError, match="only OPEN"):
        ProxyPacket(packet_type=ptype, conn_id=1, host="evil.example").encode()


def test_proxy_packet_oversized_fields_are_rejected() -> None:
    """Length-prefix overflow guards: encode() refuses anything that
    couldn't legally fit on the wire."""
    with pytest.raises(ValueError, match="host too long"):
        ProxyPacket(
            packet_type=ProxyPacketType.OPEN,
            conn_id=1,
            host="x" * (MAX_HOST_LEN + 1),
            port=80,
        ).encode()
    with pytest.raises(ValueError, match="payload too large"):
        ProxyPacket(
            packet_type=ProxyPacketType.DATA,
            conn_id=1,
            payload=b"x" * (MAX_DATA_LEN + 1),
        ).encode()
    with pytest.raises(ValueError, match="connection id"):
        ProxyPacket(packet_type=ProxyPacketType.DATA, conn_id=-1).encode()
    with pytest.raises(ValueError, match="connection id"):
        ProxyPacket(packet_type=ProxyPacketType.DATA, conn_id=1 << 32).encode()
    with pytest.raises(ValueError, match="port"):
        ProxyPacket(
            packet_type=ProxyPacketType.OPEN,
            conn_id=1,
            host="a",
            port=1 << 16,
        ).encode()


def test_proxy_packet_decode_rejects_short_or_corrupt_buffers() -> None:
    """Decoders are silent — they return None instead of raising — so a
    flaky transport never crashes the pump loop."""
    assert ProxyPacket.decode(b"") is None
    assert ProxyPacket.decode(b"\x00" * (HEADER_SIZE - 1)) is None
    # Right size, wrong magic.
    blob = bytearray(HEADER_SIZE)
    blob[0:2] = b"XY"
    assert ProxyPacket.decode(bytes(blob)) is None
    # Right magic, header claims a body that wasn't appended.
    valid = ProxyPacket(
        packet_type=ProxyPacketType.DATA, conn_id=1, payload=b"abc"
    ).encode()
    assert ProxyPacket.decode(valid[:HEADER_SIZE]) is None  # body missing


def test_proxy_packet_rejects_tampered_hmac() -> None:
    """A single bitflip anywhere in the encrypted blob invalidates the
    HMAC and the packet is silently dropped (returns None)."""
    rng = random.Random("tamper")
    original = ProxyPacket(
        packet_type=ProxyPacketType.DATA,
        conn_id=99,
        payload=b"sensitive payload" * 4,
    ).encode(PROXY_SECRET)
    # Tamper at several byte offsets and assert each one fails to decode.
    for _ in range(16):
        offset = rng.randrange(0, len(original))
        bit = 1 << rng.randrange(8)
        tampered = bytearray(original)
        tampered[offset] ^= bit
        if bytes(tampered) == original:  # no-op flip (impossible but defensive)
            continue
        assert ProxyPacket.decode(bytes(tampered), PROXY_SECRET) is None


def test_proxy_packet_psk_mismatch_is_silent_drop() -> None:
    """Mismatched PSK is rejected as if the packet were never sent —
    no exception, no partial decode."""
    encoded = ProxyPacket(
        packet_type=ProxyPacketType.DATA, conn_id=1, payload=b"x"
    ).encode(PROXY_SECRET)
    assert ProxyPacket.decode(encoded, b"wrong-secret") is None
    assert ProxyPacket.decode(encoded, None) is None  # encrypted but no secret given
    # And the other direction: plaintext packets are dropped if a secret
    # was expected.
    plaintext = ProxyPacket(
        packet_type=ProxyPacketType.DATA, conn_id=1, payload=b"x"
    ).encode()
    assert ProxyPacket.decode(plaintext, PROXY_SECRET) is None


def test_proxy_packet_encrypted_payload_does_not_appear_in_ciphertext() -> None:
    """Sanity: the keystream actually XORs the body. If encryption is
    accidentally disabled, the plaintext would be visible."""
    plaintext_marker = b"ATTACK_AT_DAWN"
    encoded = ProxyPacket(
        packet_type=ProxyPacketType.DATA,
        conn_id=1,
        payload=plaintext_marker * 8,
    ).encode(PROXY_SECRET)
    assert plaintext_marker not in encoded


def test_proxy_packet_encrypted_uses_fresh_nonce_each_call() -> None:
    """Two encrypts of the same packet must produce different ciphertext
    (otherwise replay attackers see structure leaks)."""
    pkt = ProxyPacket(
        packet_type=ProxyPacketType.DATA, conn_id=1, payload=b"same payload"
    )
    a = pkt.encode(PROXY_SECRET)
    b = pkt.encode(PROXY_SECRET)
    assert a != b
    assert ProxyPacket.decode(a, PROXY_SECRET) == pkt
    assert ProxyPacket.decode(b, PROXY_SECRET) == pkt


def test_http_connect_proxy_roundtrip_over_memory_tunnel() -> None:
    echo_port, _echo_thread = _start_echo_server()

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))
    left.open()
    right.open()

    left_transport = QueuedTunnelTransport(left)
    right_transport = QueuedTunnelTransport(right)

    relay = TunnelTcpRelay(right_transport, secret=PROXY_SECRET)
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()

    server = Socks5ProxyServer(left_transport, listen_host="127.0.0.1", listen_port=0, secret=PROXY_SECRET)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    deadline = time.time() + 2.0
    while server.bound_port is None and time.time() < deadline:
        time.sleep(0.01)
    assert server.bound_port is not None

    with socket.create_connection(("127.0.0.1", server.bound_port), timeout=2.0) as sock:
        target = f"127.0.0.1:{echo_port}".encode("ascii")
        request = b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n\r\n"
        sock.sendall(request)
        response = sock.recv(128)
        assert b"200 Connection Established" in response
        sock.sendall(b"http hello")
        assert _recv_exact(sock, len(b"http hello")) == b"http hello"

    server.stop()
    relay.stop()
    left_transport.close()
    right_transport.close()
    left.close()
    right.close()
    server_thread.join(timeout=2.0)
    relay_thread.join(timeout=2.0)
