from __future__ import annotations

import threading
import time

import pytest

from userbot_bale.bale.mtproto import Frame, MtpRpcClient, PlainSessionCodec, read_frame
from userbot_bale.bale.mtproto.framing import MAX_FRAME_LEN, write_frame


class _ScriptedStream:
    def __init__(self) -> None:
        self._incoming = bytearray()
        self._sent: list[bytes] = []
        self._closed = False
        self._cv = threading.Condition()

    @property
    def sent(self) -> list[bytes]:
        return self._sent

    def push(self, data: bytes) -> None:
        with self._cv:
            self._incoming.extend(data)
            self._cv.notify_all()

    def recv(self, n: int) -> bytes:
        with self._cv:
            deadline = time.time() + 1.0
            while not self._incoming and not self._closed:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return b""
                self._cv.wait(timeout=remaining)
            if not self._incoming:
                return b""
            chunk = bytes(self._incoming[:n])
            del self._incoming[:n]
            return chunk

    def sendall(self, data: bytes) -> None:
        self._sent.append(data)

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class _Conn:
    def __init__(self, stream: _ScriptedStream) -> None:
        self.stream = stream

    def close(self) -> None:
        self.stream.close()


def test_frame_roundtrip() -> None:
    frame = Frame(seq=7, message_type=2, body=b"hello")
    decoded = Frame.decode(frame.encode())
    assert decoded == frame


def test_read_frame_reads_streamed_bytes() -> None:
    stream = _ScriptedStream()
    conn = _Conn(stream)
    encoded = Frame(seq=9, message_type=3, body=b"payload").encode()
    stream.push(encoded[:5])
    stream.push(encoded[5:])
    frame = read_frame(conn)
    assert frame.seq == 9
    assert frame.message_type == 3
    assert frame.body == b"payload"


def test_rpc_client_dispatches_response_and_updates() -> None:
    stream = _ScriptedStream()
    conn = _Conn(stream)
    client = MtpRpcClient(conn, codec=PlainSessionCodec())
    updates: list[bytes] = []
    client.subscribe(updates.append)

    def _server() -> None:
        while not stream.sent:
            time.sleep(0.01)
        request = Frame.decode(stream.sent[0])
        stream.push(Frame(seq=0, message_type=3, body=b"update-1").encode())
        stream.push(Frame(seq=request.seq, message_type=2, body=b"reply:" + request.body).encode())

    t = threading.Thread(target=_server, daemon=True)
    t.start()
    payload = client.call(b"ping", timeout=1.0)
    assert payload == b"reply:ping"
    t.join(timeout=1.0)
    client.close()
    assert updates == [b"update-1"]


def test_large_payload_roundtrip_under_max_frame_len() -> None:
    """Frames near MAX_FRAME_LEN must round-trip cleanly. Plan §4 called
    out that no test exercised payloads >900KiB; this covers it."""
    body = bytes((i * 31) & 0xFF for i in range(900 * 1024))
    encoded = Frame(seq=1234, message_type=7, body=body).encode()
    # Streamed in two chunks to exercise read_frame's loop.
    stream = _ScriptedStream()
    conn = _Conn(stream)
    stream.push(encoded[:512 * 1024])
    stream.push(encoded[512 * 1024:])
    frame = read_frame(conn)
    assert frame.seq == 1234
    assert frame.body == body


def test_oversized_frame_is_rejected() -> None:
    """A length prefix beyond MAX_FRAME_LEN must be refused before we
    allocate, preventing trivial DoS via giant length prefixes."""
    import struct
    stream = _ScriptedStream()
    conn = _Conn(stream)
    # Declare a frame ~2x the cap. read_frame should refuse without
    # waiting for the (never-arriving) body.
    stream.push(struct.pack("<I", MAX_FRAME_LEN + 1))
    with pytest.raises(ValueError, match="oversize"):
        read_frame(conn)


def test_write_frame_writes_encoded_bytes() -> None:
    stream = _ScriptedStream()
    conn = _Conn(stream)
    write_frame(conn, Frame(seq=1, message_type=2, body=b"x"))
    assert stream.sent == [Frame(seq=1, message_type=2, body=b"x").encode()]
