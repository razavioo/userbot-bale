"""
MTProto frame read/write on top of an EndpointConnection.

Status: **pre-auth path implemented and unit-tested**; post-auth crypto
and Bale-specific `message_type` constants still need capture validation
(see docs/CAPTURE.md). The layout below matches Actor Platform's upstream
MTProto v2; real wire bytes will either confirm it or point at exact
deltas.

Pre-auth frame layout (from Actor Platform reference):
    uint32_le  length           length of [seq..message_body]
    uint32_le  seq              ever-increasing per-connection seq
    uint32_le  message_type     constant per message type
    bytes      message_body     BSER or protobuf payload

Post-auth frame layout (envelope shown; body is encrypted):
    bytes(8)   auth_key_id      first 8 bytes of sha1(auth_key)
    bytes(16)  msg_key          sha1(payload)[4:20]
    bytes(N)   encrypted_payload  AES-256-IGE(aux_key(msg_key, auth_key))

The plain pre-auth frame shape is concrete and covered by offline tests,
which lets the session/RPC machinery stop depending on placeholders for
that half of the stack.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_LEN = 12
MAX_FRAME_LEN = 1 << 20


@dataclass(frozen=True)
class Frame:
    seq: int
    message_type: int
    body: bytes

    def encode(self) -> bytes:
        """Encode a plain MTProto frame with a uint32 length prefix."""
        body_len = 8 + len(self.body)
        return struct.pack("<III", body_len, self.seq, self.message_type) + self.body

    @classmethod
    def decode(cls, buf: bytes) -> "Frame":
        if len(buf) < HEADER_LEN:
            raise ValueError(f"short frame: {len(buf)} < {HEADER_LEN}")
        total_len, seq, message_type = struct.unpack("<III", buf[:HEADER_LEN])
        expected = 4 + total_len
        if total_len < 8:
            raise ValueError(f"invalid frame length: {total_len}")
        if len(buf) != expected:
            raise ValueError(f"frame length mismatch: declared {expected}, got {len(buf)}")
        return cls(seq=seq, message_type=message_type, body=buf[HEADER_LEN:])


def _read_exact(conn, n: int) -> bytes:
    """Read exactly n bytes from the connection's socket. Used by
    higher layers as soon as frame decoding is live."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.stream.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed while reading frame")
        buf.extend(chunk)
    return bytes(buf)


def _write_all(conn, data: bytes) -> None:
    conn.stream.sendall(data)


def read_frame(conn, *, max_len: int = MAX_FRAME_LEN) -> Frame:
    raw = _read_exact(conn, 4)
    (n,) = struct.unpack("<I", raw)
    if n < 8:
        raise ValueError(f"undersize frame: {n}")
    if n > max_len:
        raise ValueError(f"oversize frame: {n}")
    return Frame.decode(raw + _read_exact(conn, n))


def write_frame(conn, frame: Frame) -> None:
    _write_all(conn, frame.encode())


# Convenience for the live smoketest: read a length-prefixed frame
# with no interpretation, just so we can prove bytes flow. Safe to
# call pre-capture because it doesn't assume a protocol layout.
def read_length_prefixed(conn, max_len: int = 1 << 20) -> bytes:
    raw = _read_exact(conn, 4)
    (n,) = struct.unpack("<I", raw)
    if n > max_len:
        raise ValueError(f"oversize frame: {n}")
    return _read_exact(conn, n)
