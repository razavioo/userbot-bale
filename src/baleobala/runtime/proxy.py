"""SOCKS5 and TCP relay helpers built on top of the tunnel transport."""

from __future__ import annotations

import hmac
import hashlib
import logging
import os
import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass
from enum import IntEnum

from baleobala.runtime.interfaces import TunnelTransport

log = logging.getLogger(__name__)

MAGIC = b"PX"
VERSION = 1
MAX_HOST_LEN = 180
MAX_DATA_LEN = 180
NONCE_SIZE = 8
TAG_SIZE = 16
FLAG_ENCRYPTED = 0x01
HEADER_FMT = f"<2sBBB I H {NONCE_SIZE}s {TAG_SIZE}s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class ProxyPacketType(IntEnum):
    HELLO = 0
    OPEN = 1
    OPEN_OK = 2
    DATA = 3
    CLOSE = 4
    ERROR = 5
    HELLO_ACK = 6


@dataclass(frozen=True)
class ProxyPacket:
    """Small control/data packet carried over the tunnel."""

    packet_type: ProxyPacketType
    conn_id: int
    host: str = ""
    port: int = 0
    payload: bytes = b""

    def encode(self, secret: bytes | None = None) -> bytes:
        host_bytes = self.host.encode("utf-8") if self.host else b""
        if len(host_bytes) > MAX_HOST_LEN:
            raise ValueError(f"proxy host too long: {len(host_bytes)}")
        if len(self.payload) > MAX_DATA_LEN:
            raise ValueError(f"proxy payload too large: {len(self.payload)}")
        if self.packet_type != ProxyPacketType.OPEN and host_bytes:
            raise ValueError("only OPEN packets may carry a host")
        if not (0 <= self.conn_id <= 0xFFFFFFFF):
            raise ValueError("connection id out of range")
        if not (0 <= self.port <= 0xFFFF):
            raise ValueError("port out of range")
        body = self._encode_body(host_bytes, self.payload, self.port)
        nonce = b"\x00" * NONCE_SIZE
        flags = 0
        if secret:
            flags |= FLAG_ENCRYPTED
            nonce = os.urandom(NONCE_SIZE)
            body = _xor_bytes(body, _keystream(secret, nonce, len(body)))
        header_wo_tag = struct.pack(
            HEADER_FMT,
            MAGIC,
            VERSION,
            flags,
            int(self.packet_type) & 0xFF,
            self.conn_id,
            len(body),
            nonce,
            b"\x00" * TAG_SIZE,
        )
        tag = _auth_tag(secret, header_wo_tag, body) if secret else b"\x00" * TAG_SIZE
        return struct.pack(
            HEADER_FMT,
            MAGIC,
            VERSION,
            flags,
            int(self.packet_type) & 0xFF,
            self.conn_id,
            len(body),
            nonce,
            tag,
        ) + body

    @classmethod
    def decode(cls, buf: bytes, secret: bytes | None = None) -> "ProxyPacket | None":
        if len(buf) < HEADER_SIZE:
            return None
        magic, version, flags, packet_type, conn_id, body_len, nonce, tag = struct.unpack(
            HEADER_FMT, buf[:HEADER_SIZE]
        )
        if magic != MAGIC or version != VERSION:
            return None
        body = buf[HEADER_SIZE:]
        if len(body) != body_len:
            return None
        if flags & FLAG_ENCRYPTED:
            if secret is None:
                return None
            header_wo_tag = struct.pack(
                HEADER_FMT,
                MAGIC,
                VERSION,
                flags,
                packet_type,
                conn_id,
                body_len,
                nonce,
                b"\x00" * TAG_SIZE,
            )
            expected = _auth_tag(secret, header_wo_tag, body)
            if not hmac.compare_digest(tag, expected):
                return None
            body = _xor_bytes(body, _keystream(secret, nonce, len(body)))
        elif secret:
            return None
        try:
            host_bytes, data, port = cls._decode_body(body)
        except ValueError:
            return None
        try:
            host = host_bytes.decode("utf-8") if host_bytes else ""
            kind = ProxyPacketType(packet_type)
        except (UnicodeDecodeError, ValueError):
            return None
        return cls(packet_type=kind, conn_id=conn_id, host=host, port=port, payload=data)

    @staticmethod
    def _encode_body(host_bytes: bytes, payload: bytes, port: int = 0) -> bytes:
        return (
            bytes([len(host_bytes)])
            + host_bytes
            + struct.pack(">H", port)
            + struct.pack(">H", len(payload))
            + payload
        )

    @staticmethod
    def _decode_body(body: bytes) -> tuple[bytes, bytes, int]:
        if len(body) < 5:
            raise ValueError("proxy body too small")
        host_len = body[0]
        if len(body) < 1 + host_len + 4:
            raise ValueError("proxy body truncated")
        host_bytes = body[1 : 1 + host_len]
        port = struct.unpack(">H", body[1 + host_len : 3 + host_len])[0]
        payload_len = struct.unpack(">H", body[3 + host_len : 5 + host_len])[0]
        payload = body[5 + host_len :]
        if len(payload) != payload_len:
            raise ValueError("proxy payload truncated")
        return host_bytes, payload, port


def _keystream(secret: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(secret, nonce + struct.pack("<I", counter), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor_bytes(data: bytes, key_stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, key_stream))


def _auth_tag(secret: bytes, header_wo_tag: bytes, body: bytes) -> bytes:
    return hmac.new(secret, header_wo_tag + body, hashlib.sha256).digest()[:TAG_SIZE]


def _chunk_bytes(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise EOFError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def _socks5_reply(sock: socket.socket, code: int, host: str = "0.0.0.0", port: int = 0) -> None:
    try:
        addr = socket.inet_aton(host)
        atyp = 0x01
    except OSError:
        addr = b"\x00\x00\x00\x00"
        atyp = 0x01
    sock.sendall(b"\x05" + bytes([code, 0x00, atyp]) + addr + struct.pack(">H", port))


def _parse_socks5_target(sock: socket.socket) -> tuple[str, int]:
    ver, cmd, _rsv, atyp = _recv_exact(sock, 4)
    if ver != 0x05:
        raise ValueError("unsupported SOCKS version")
    if cmd != 0x01:
        raise ValueError("only SOCKS5 CONNECT is supported")
    if atyp == 0x01:
        host = socket.inet_ntoa(_recv_exact(sock, 4))
    elif atyp == 0x03:
        host_len = _recv_exact(sock, 1)[0]
        host = _recv_exact(sock, host_len).decode("utf-8")
    elif atyp == 0x04:
        host = socket.inet_ntop(socket.AF_INET6, _recv_exact(sock, 16))
    else:
        raise ValueError("unsupported address type")
    port = struct.unpack(">H", _recv_exact(sock, 2))[0]
    return host, port


def _read_http_connect_target(client: socket.socket, first_byte: bytes) -> tuple[str, int]:
    buf = bytearray(first_byte)
    while b"\r\n\r\n" not in buf:
        chunk = client.recv(4096)
        if not chunk:
            raise EOFError("connection closed before HTTP CONNECT completed")
        buf.extend(chunk)
        if len(buf) > 8192:
            raise ValueError("HTTP CONNECT request too large")
    header_blob = bytes(buf)
    head, _rest = header_blob.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    if not lines:
        raise ValueError("empty HTTP CONNECT request")
    method, target, _version = lines[0].split(b" ", 2)
    if method.upper() != b"CONNECT":
        raise ValueError("only HTTP CONNECT is supported")
    host, port_text = target.rsplit(b":", 1)
    return host.decode("utf-8"), int(port_text)


def _http_connect_reply(client: socket.socket, status: int, reason: str = "OK") -> None:
    client.sendall(
        f"HTTP/1.1 {status} {reason}\r\nProxy-Agent: baleobala\r\n\r\n".encode("ascii")
    )


class QueuedTunnelTransport:
    """Serialize transport writes onto one worker thread."""

    def __init__(self, transport: TunnelTransport) -> None:
        self._transport = transport
        self._queue: "queue.Queue[bytes | object]" = queue.Queue()
        self._closed = False
        self._sentinel = object()
        self._writer = threading.Thread(target=self._pump, daemon=True)
        self._writer.start()

    def _pump(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._sentinel:
                break
            try:
                self._transport.send(bytes(item))
            except Exception:
                break
        try:
            self._transport.close()
        except Exception:
            pass

    def send(self, data: bytes) -> None:
        if self.closed:
            raise RuntimeError("transport closed")
        self._queue.put(bytes(data))

    def recv(self, timeout: float | None = None) -> bytes | None:
        return self._transport.recv(timeout=timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(self._sentinel)
        self._writer.join(timeout=1.0)

    @property
    def closed(self) -> bool:
        return self._closed or getattr(self._transport, "closed", False)


class ProxyHub:
    """Fan-in/fan-out router for proxy packets over one tunnel transport."""

    def __init__(self, transport: TunnelTransport, *, secret: bytes | None = None) -> None:
        self._transport = transport
        self._secret = secret
        self._send_lock = threading.Lock()
        self._queues: dict[int, "queue.Queue[ProxyPacket | object]"] = {}
        self._general: "queue.Queue[ProxyPacket | object]" = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._sentinel = object()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        while not self._stop.is_set():
            raw = self._transport.recv(timeout=0.25)
            if raw is None:
                if getattr(self._transport, "closed", False):
                    break
                continue
            packet = ProxyPacket.decode(raw, self._secret)
            if packet is None:
                continue
            if packet.packet_type == ProxyPacketType.HELLO:
                self.send(ProxyPacket(packet_type=ProxyPacketType.HELLO_ACK, conn_id=0, payload=packet.payload))
                continue
            if packet.packet_type == ProxyPacketType.HELLO_ACK:
                self._ready.set()
                continue
            with self._send_lock:
                queue_for_conn = self._queues.get(packet.conn_id)
            if queue_for_conn is not None:
                queue_for_conn.put(packet)
            elif packet.packet_type == ProxyPacketType.OPEN:
                self._general.put(packet)
        self._drain()

    def _drain(self) -> None:
        with self._send_lock:
            for q in self._queues.values():
                q.put(self._sentinel)
        self._general.put(self._sentinel)

    def send(self, packet: ProxyPacket) -> None:
        if self.closed:
            raise RuntimeError("proxy hub closed")
        with self._send_lock:
            self._transport.send(packet.encode(self._secret))

    def negotiate(self, timeout: float = 3.0) -> bool:
        if self.closed:
            return False
        self._ready.clear()
        deadline = time.monotonic() + timeout
        while not self.closed and time.monotonic() < deadline:
            self.send(
                ProxyPacket(
                    packet_type=ProxyPacketType.HELLO,
                    conn_id=0,
                    payload=b"proxy-v1",
                )
            )
            remaining = max(0.0, deadline - time.monotonic())
            if self._ready.wait(timeout=min(0.5, remaining)):
                return True
        return self._ready.is_set()

    def register(self, conn_id: int) -> "queue.Queue[ProxyPacket | object]":
        with self._send_lock:
            q = self._queues.get(conn_id)
            if q is None:
                q = queue.Queue()
                self._queues[conn_id] = q
            return q

    def unregister(self, conn_id: int) -> None:
        with self._send_lock:
            self._queues.pop(conn_id, None)

    def recv(self, conn_id: int, timeout: float | None = None) -> ProxyPacket | None:
        q = self.register(conn_id)
        try:
            item = q.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is self._sentinel:
            return None
        return item

    def accept_open(self, timeout: float | None = None) -> ProxyPacket | None:
        while not self.closed:
            try:
                item = self._general.get(timeout=timeout)
            except queue.Empty:
                return None
            if item is self._sentinel:
                return None
            packet = item
            if packet.packet_type == ProxyPacketType.OPEN:
                self.register(packet.conn_id)
                return packet
        return None

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._drain()
        self._reader.join(timeout=1.0)

    @property
    def closed(self) -> bool:
        return self._stop.is_set() or getattr(self._transport, "closed", False)


class Socks5ProxyServer:
    """Local SOCKS5 CONNECT endpoint backed by a tunnel transport."""

    def __init__(
        self,
        transport: TunnelTransport,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1080,
        handshake_timeout: float = 15.0,
        max_chunk_size: int = MAX_DATA_LEN,
        max_active_connections: int = 128,
        secret: bytes | None = None,
        on_listen=None,
    ) -> None:
        self._hub = ProxyHub(transport, secret=secret)
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._handshake_timeout = handshake_timeout
        self._max_chunk_size = max_chunk_size
        self._on_listen = on_listen
        self._client_slots = threading.BoundedSemaphore(max_active_connections)
        self._stop = threading.Event()
        self._conn_lock = threading.Lock()
        self._conn_seq = 0
        self.bound_host: str | None = None
        self.bound_port: int | None = None
        self._listen_backlog = 16

    def stop(self) -> None:
        self._stop.set()
        self._hub.close()

    def serve_once(self) -> None:
        if not self._hub.negotiate(timeout=self._handshake_timeout):
            raise TimeoutError("proxy handshake failed")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._listen_host, self._listen_port))
            self.bound_host, self.bound_port = listener.getsockname()[:2]
            self._emit_listen_ready()
            listener.listen(self._listen_backlog)
            listener.settimeout(0.25)
            while not self._stop.is_set():
                try:
                    client, _addr = listener.accept()
                except socket.timeout:
                    continue
                with client:
                    self._handle_client(client)
                return

    def serve_forever(self) -> None:
        if not self._hub.negotiate(timeout=self._handshake_timeout):
            raise TimeoutError("proxy handshake failed")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._listen_host, self._listen_port))
            self.bound_host, self.bound_port = listener.getsockname()[:2]
            self._emit_listen_ready()
            listener.listen(self._listen_backlog)
            listener.settimeout(0.25)
            while not self._stop.is_set():
                try:
                    client, _addr = listener.accept()
                except socket.timeout:
                    if self._transport_closed():
                        break
                    continue
                if not self._client_slots.acquire(blocking=False):
                    log.warning("proxy_client_rejected reason=too_many_connections")
                    client.close()
                    continue
                threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _transport_closed(self) -> bool:
        return self._hub.closed

    def _emit_listen_ready(self) -> None:
        if self._on_listen is None:
            return
        self._on_listen(self.bound_host, self.bound_port)

    def _handle_client(self, client: socket.socket) -> None:
        try:
            first = _recv_exact(client, 1)
            if first == b"\x05":
                self._handle_socks5(client)
            else:
                self._handle_http_connect(client, first)
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                self._client_slots.release()
            except ValueError:
                pass

    def _open_proxy_stream(self, client: socket.socket, target_host: str, target_port: int) -> int | None:
        with self._conn_lock:
            self._conn_seq += 1
            conn_id = self._conn_seq
        self._hub.register(conn_id)
        self._hub.send(
            ProxyPacket(
                packet_type=ProxyPacketType.OPEN,
                conn_id=conn_id,
                host=target_host,
                port=target_port,
            )
        )
        ok, _reason = self._wait_open_result(conn_id)
        if not ok:
            self._hub.unregister(conn_id)
            return None
        return conn_id

    def _handle_socks5(self, client: socket.socket) -> None:
        nmethods = _recv_exact(client, 1)[0]
        methods = _recv_exact(client, nmethods)
        if 0x00 not in methods:
            client.sendall(b"\x05\xff")
            return
        client.sendall(b"\x05\x00")

        target_host, target_port = _parse_socks5_target(client)
        conn_id = self._open_proxy_stream(client, target_host, target_port)
        if conn_id is None:
            _socks5_reply(client, 0x01)
            return
        _socks5_reply(client, 0x00)

        stop = threading.Event()

        def pump_client_to_transport() -> None:
            try:
                while not stop.is_set():
                    data = client.recv(4096)
                    if not data:
                        break
                    for chunk in _chunk_bytes(data, self._max_chunk_size):
                        self._hub.send(
                            ProxyPacket(
                                packet_type=ProxyPacketType.DATA,
                                conn_id=conn_id,
                                payload=chunk,
                            )
                        )
            finally:
                stop.set()
                try:
                    self._hub.send(ProxyPacket(packet_type=ProxyPacketType.CLOSE, conn_id=conn_id))
                except RuntimeError:
                    pass

        worker = threading.Thread(target=pump_client_to_transport, daemon=True)
        worker.start()
        try:
            while not stop.is_set():
                packet = self._hub.recv(conn_id, timeout=0.25)
                if packet is None:
                    if self._transport_closed():
                        break
                    continue
                if packet.packet_type == ProxyPacketType.DATA and packet.payload:
                    client.sendall(packet.payload)
                elif packet.packet_type in (ProxyPacketType.CLOSE, ProxyPacketType.ERROR):
                    break
        finally:
            stop.set()
            worker.join(timeout=1.0)
            self._hub.unregister(conn_id)

    def _handle_http_connect(self, client: socket.socket, first_byte: bytes) -> None:
        target_host, target_port = _read_http_connect_target(client, first_byte)
        conn_id = self._open_proxy_stream(client, target_host, target_port)
        if conn_id is None:
            _http_connect_reply(client, 502, "Bad Gateway")
            return
        _http_connect_reply(client, 200, "Connection Established")

        stop = threading.Event()

        def pump_client_to_transport() -> None:
            try:
                while not stop.is_set():
                    data = client.recv(4096)
                    if not data:
                        break
                    for chunk in _chunk_bytes(data, self._max_chunk_size):
                        self._hub.send(
                            ProxyPacket(
                                packet_type=ProxyPacketType.DATA,
                                conn_id=conn_id,
                                payload=chunk,
                            )
                        )
            finally:
                stop.set()
                try:
                    self._hub.send(ProxyPacket(packet_type=ProxyPacketType.CLOSE, conn_id=conn_id))
                except RuntimeError:
                    pass

        worker = threading.Thread(target=pump_client_to_transport, daemon=True)
        worker.start()
        try:
            while not stop.is_set():
                packet = self._hub.recv(conn_id, timeout=0.25)
                if packet is None:
                    if self._transport_closed():
                        break
                    continue
                if packet.packet_type == ProxyPacketType.DATA and packet.payload:
                    client.sendall(packet.payload)
                elif packet.packet_type in (ProxyPacketType.CLOSE, ProxyPacketType.ERROR):
                    break
        finally:
            stop.set()
            worker.join(timeout=1.0)
            self._hub.unregister(conn_id)

    def _wait_open_result(self, conn_id: int) -> tuple[bool, str | None]:
        while not self._stop.is_set():
            packet = self._hub.recv(conn_id, timeout=0.25)
            if packet is None:
                if self._transport_closed():
                    return False, "closed"
                continue
            if packet.packet_type == ProxyPacketType.OPEN_OK:
                return True, None
            if packet.packet_type == ProxyPacketType.ERROR:
                return False, "error"
        return False, "closed"


class DirectSocks5Server:
    """SOCKS5 + HTTP CONNECT proxy that opens real outbound sockets.

    No tunnel, no remote relay — used by the macOS ``direct`` VPN backend to
    give a single machine a working system-wide proxy with no external
    dependencies.
    """

    def __init__(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1080,
        connect_timeout: float = 10.0,
        backlog: int = 64,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._connect_timeout = connect_timeout
        self._backlog = backlog
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.bound_host: str | None = None
        self.bound_port: int | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self._listen_host, self._listen_port))
        listener.listen(self._backlog)
        listener.settimeout(0.25)
        self._listener = listener
        self.bound_host, self.bound_port = listener.getsockname()[:2]
        self._ready.set()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout=timeout)

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._listener = None
        self._thread = None

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stop.is_set():
            try:
                client, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        try:
            first = _recv_exact(client, 1)
        except (OSError, EOFError):
            client.close()
            return
        try:
            if first == b"\x05":
                self._handle_socks5(client)
            else:
                self._handle_http_connect(client, first)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _handle_socks5(self, client: socket.socket) -> None:
        nmethods = _recv_exact(client, 1)[0]
        methods = _recv_exact(client, nmethods)
        if 0x00 not in methods:
            client.sendall(b"\x05\xff")
            return
        client.sendall(b"\x05\x00")

        try:
            target_host, target_port = _parse_socks5_target(client)
        except (ValueError, EOFError, OSError):
            _socks5_reply(client, 0x01)
            return

        upstream = self._connect_upstream(target_host, target_port)
        if upstream is None:
            _socks5_reply(client, 0x05)
            return
        _socks5_reply(client, 0x00)
        with upstream:
            self._splice(client, upstream)

    def _handle_http_connect(self, client: socket.socket, first_byte: bytes) -> None:
        try:
            target_host, target_port = _read_http_connect_target(client, first_byte)
        except (ValueError, EOFError, OSError):
            _http_connect_reply(client, 400, "Bad Request")
            return
        upstream = self._connect_upstream(target_host, target_port)
        if upstream is None:
            _http_connect_reply(client, 502, "Bad Gateway")
            return
        _http_connect_reply(client, 200, "Connection Established")
        with upstream:
            self._splice(client, upstream)

    def _connect_upstream(self, host: str, port: int) -> socket.socket | None:
        try:
            return socket.create_connection((host, port), timeout=self._connect_timeout)
        except OSError:
            return None

    @staticmethod
    def _splice(a: socket.socket, b: socket.socket) -> None:
        done = threading.Event()

        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while not done.is_set():
                    data = src.recv(8192)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                done.set()
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t1 = threading.Thread(target=pump, args=(a, b), daemon=True)
        t2 = threading.Thread(target=pump, args=(b, a), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()


class TunnelTcpRelay:
    """Remote TCP relay that serves the other end of the tunnel."""

    def __init__(
        self,
        transport: TunnelTransport,
        *,
        connect_timeout: float = 5.0,
        handshake_timeout: float = 15.0,
        max_chunk_size: int = MAX_DATA_LEN,
        max_active_connections: int = 64,
        secret: bytes | None = None,
    ) -> None:
        self._hub = ProxyHub(transport, secret=secret)
        self._connect_timeout = connect_timeout
        self._handshake_timeout = handshake_timeout
        self._max_chunk_size = max_chunk_size
        self._slots = threading.BoundedSemaphore(max_active_connections)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        self._hub.close()

    def serve_once(self) -> None:
        if not self._hub.negotiate(timeout=self._handshake_timeout):
            raise TimeoutError("proxy handshake failed")
        while not self._stop.is_set():
            packet = self._hub.accept_open(timeout=0.25)
            if packet is None:
                if self._transport_closed():
                    return
                continue
            if packet.packet_type != ProxyPacketType.OPEN:
                continue
            self._handle_connection(packet)
            return

    def serve_forever(self) -> None:
        if not self._hub.negotiate(timeout=self._handshake_timeout):
            raise TimeoutError("proxy handshake failed")
        while not self._stop.is_set():
            if self._transport_closed():
                return
            packet = self._hub.accept_open(timeout=0.25)
            if packet is None:
                continue
            if packet.packet_type != ProxyPacketType.OPEN:
                continue
            threading.Thread(target=self._handle_connection, args=(packet,), daemon=True).start()

    def _transport_closed(self) -> bool:
        return self._hub.closed

    def _handle_connection(self, packet: ProxyPacket) -> None:
        if not self._slots.acquire(blocking=False):
            log.warning("proxy_open_rejected target=%s:%s conn_id=%s reason=too_many_connections", packet.host, packet.port, packet.conn_id)
            try:
                self._hub.send(
                    ProxyPacket(
                        packet_type=ProxyPacketType.ERROR,
                        conn_id=packet.conn_id,
                        payload=b"relay overloaded",
                    )
                )
            finally:
                self._hub.unregister(packet.conn_id)
            return
        log.info("proxy_open target=%s:%s conn_id=%s", packet.host, packet.port, packet.conn_id)
        try:
            sock = socket.create_connection((packet.host, packet.port), timeout=self._connect_timeout)
        except OSError as exc:
            log.warning("proxy_open_failed target=%s:%s conn_id=%s error=%s", packet.host, packet.port, packet.conn_id, exc)
            try:
                self._hub.send(
                    ProxyPacket(
                        packet_type=ProxyPacketType.ERROR,
                        conn_id=packet.conn_id,
                        payload=str(exc).encode("utf-8", errors="replace"),
                    )
                )
            finally:
                self._hub.unregister(packet.conn_id)
                self._slots.release()
            return

        with sock:
            sock.settimeout(0.25)
            log.info("proxy_open_ok target=%s:%s conn_id=%s", packet.host, packet.port, packet.conn_id)
            self._hub.send(ProxyPacket(packet_type=ProxyPacketType.OPEN_OK, conn_id=packet.conn_id))
            stop = threading.Event()

            def pump_socket_to_transport() -> None:
                try:
                    while not stop.is_set():
                        try:
                            data = sock.recv(4096)
                        except TimeoutError:
                            continue
                        except OSError:
                            break
                        if not data:
                            break
                        for chunk in _chunk_bytes(data, self._max_chunk_size):
                            self._hub.send(
                                ProxyPacket(
                                    packet_type=ProxyPacketType.DATA,
                                    conn_id=packet.conn_id,
                                    payload=chunk,
                                )
                            )
                finally:
                    stop.set()
                    try:
                        self._hub.send(ProxyPacket(packet_type=ProxyPacketType.CLOSE, conn_id=packet.conn_id))
                    except RuntimeError:
                        pass

            worker = threading.Thread(target=pump_socket_to_transport, daemon=True)
            worker.start()
            try:
                while not stop.is_set():
                    incoming = self._hub.recv(packet.conn_id, timeout=0.25)
                    if incoming is None:
                        if self._transport_closed():
                            break
                        continue
                    if incoming.packet_type == ProxyPacketType.DATA and incoming.payload:
                        sock.sendall(incoming.payload)
                    elif incoming.packet_type in (ProxyPacketType.CLOSE, ProxyPacketType.ERROR):
                        break
            finally:
                stop.set()
                worker.join(timeout=1.0)
                self._hub.unregister(packet.conn_id)
                self._slots.release()
