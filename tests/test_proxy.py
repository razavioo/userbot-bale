from __future__ import annotations

import socket
import threading
import time

from baleobala.runtime import MemoryByteChannel, NullSecurityProvider, QueuedTunnelTransport, TunnelRole, TunnelSession
from baleobala.runtime.proxy import ProxyHub, ProxyPacket, ProxyPacketType, Socks5ProxyServer, TunnelTcpRelay

PROXY_SECRET = b"proxy-secret"


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
