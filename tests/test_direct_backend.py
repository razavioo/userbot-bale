"""End-to-end tests for the ``direct`` macOS VPN backend.

Exercises:
- ``DirectSocks5Server`` actually forwards TCP bytes via SOCKS5 CONNECT and
  HTTP CONNECT against a real local TCP echo server.
- ``DirectProxyBackend`` starts the listener before flipping the system
  proxy, and unwinds cleanly if the session fails.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from userbot_bale.control.backend import DirectProxyBackend
from userbot_bale.control.vpn import VpnProfile
from userbot_bale.runtime.proxy import DirectSocks5Server


def _start_echo_server() -> tuple[str, int, threading.Event, socket.socket]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.25)
        while not stop.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            def handle(c: socket.socket) -> None:
                try:
                    while True:
                        data = c.recv(4096)
                        if not data:
                            break
                        c.sendall(data)
                except OSError:
                    pass
                finally:
                    c.close()

            threading.Thread(target=handle, args=(client,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    host, port = listener.getsockname()[:2]
    return host, port, stop, listener


def _socks5_connect(proxy_host: str, proxy_port: int, target_host: str, target_port: int) -> socket.socket:
    s = socket.create_connection((proxy_host, proxy_port), timeout=5.0)
    s.sendall(b"\x05\x01\x00")  # greeting: no-auth
    assert s.recv(2) == b"\x05\x00"
    host_bytes = target_host.encode("ascii")
    req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(">H", target_port)
    s.sendall(req)
    reply = s.recv(10)
    assert reply[:2] == b"\x05\x00", f"SOCKS5 reply: {reply!r}"
    return s


def test_direct_socks5_server_forwards_bytes_end_to_end() -> None:
    target_host, target_port, stop_echo, echo_listener = _start_echo_server()
    server = DirectSocks5Server(listen_host="127.0.0.1", listen_port=0)
    server.start()
    try:
        assert server.wait_ready(timeout=2.0)
        assert server.bound_port is not None and server.bound_port > 0
        client = _socks5_connect("127.0.0.1", server.bound_port, target_host, target_port)
        try:
            client.sendall(b"ping-42")
            got = client.recv(32)
            assert got == b"ping-42"
        finally:
            client.close()
    finally:
        server.stop()
        stop_echo.set()
        echo_listener.close()


def test_direct_socks5_server_http_connect_forwards_bytes() -> None:
    target_host, target_port, stop_echo, echo_listener = _start_echo_server()
    server = DirectSocks5Server(listen_host="127.0.0.1", listen_port=0)
    server.start()
    try:
        assert server.wait_ready(timeout=2.0)
        assert server.bound_port is not None
        client = socket.create_connection(("127.0.0.1", server.bound_port), timeout=5.0)
        try:
            client.sendall(
                f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}\r\n\r\n".encode()
            )
            # read status line
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = client.recv(4096)
                if not chunk:
                    break
                buf += chunk
            assert b"200" in buf.split(b"\r\n", 1)[0]
            client.sendall(b"hello")
            got = client.recv(32)
            assert got == b"hello"
        finally:
            client.close()
    finally:
        server.stop()
        stop_echo.set()
        echo_listener.close()


class _OrderingSession:
    """Fake proxy session that records the order of start/stop vs the server."""

    def __init__(self, events: list[str], fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    def start(self) -> None:
        if self._fail:
            self._events.append("session_start_fail")
            raise RuntimeError("networksetup boom")
        self._events.append("session_start")

    def stop(self) -> None:
        self._events.append("session_stop")

    def status(self) -> dict[str, str]:
        return {"proxy": "127.0.0.1:0"}


class _RecordingServer:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.bound_host = "127.0.0.1"
        self.bound_port = 1080

    def start(self) -> None:
        self._events.append("server_start")

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return True

    def stop(self) -> None:
        self._events.append("server_stop")


def test_direct_backend_starts_server_before_system_proxy(tmp_path) -> None:
    events: list[str] = []
    backend = DirectProxyBackend(
        listen_port=0,
        state_path=tmp_path / "direct.json",
        server_factory=lambda: _RecordingServer(events),
        session_factory=lambda: _OrderingSession(events),
    )
    profile = VpnProfile(profile_id="default", name="default", backend="direct")
    status = backend.up(profile)
    assert events == ["server_start", "session_start"]
    assert status["control_ready"] == "yes"
    assert status["data_path_ready"] == "yes"
    backend.down()
    assert events == ["server_start", "session_start", "session_stop", "server_stop"]


def test_direct_backend_rolls_back_server_if_session_fails(tmp_path) -> None:
    events: list[str] = []
    backend = DirectProxyBackend(
        listen_port=0,
        state_path=tmp_path / "direct.json",
        server_factory=lambda: _RecordingServer(events),
        session_factory=lambda: _OrderingSession(events, fail=True),
    )
    profile = VpnProfile(profile_id="default", name="default", backend="direct")
    with pytest.raises(RuntimeError):
        backend.up(profile)
    # server must be stopped, session was never successfully started
    assert events == ["server_start", "session_start_fail", "server_stop"]


def test_packet_tunnel_is_default_backend_on_darwin(monkeypatch) -> None:
    monkeypatch.delenv("USERBOT_BALE_VPN_BACKEND", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    from userbot_bale.control.backend import default_backend_name
    from userbot_bale.control.vpn import default_vpn_backend

    assert default_backend_name() == "packet-tunnel"
    assert default_vpn_backend() == "packet-tunnel"


def test_windows_proxy_is_default_backend_on_win32(monkeypatch) -> None:
    monkeypatch.delenv("USERBOT_BALE_VPN_BACKEND", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    from userbot_bale.control.backend import default_backend_name
    from userbot_bale.control.vpn import default_vpn_backend

    assert default_backend_name() == "windows-proxy"
    assert default_vpn_backend() == "windows-proxy"
