"""
MTProto frame read/write on top of an EndpointConnection.

Status: **skeleton**. The frame format below matches Actor Platform's
upstream MTProto v2 but Nasim may have adjusted field sizes or added
Bale-specific variants. Finalising this requires at least one
mitmproxy capture of real client↔server traffic — see
docs/CAPTURE.md. The layout is captured here as a concrete starting
point; the real wire bytes will either confirm it or point at exact
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

The actual constants (message_type values, encryption variant —
IGE vs CBC — and the msg_key derivation) must be confirmed against a
capture before we can send a valid frame. The encode/decode functions
below raise with a pointer at docs/CAPTURE.md so callers know where
the gate sits.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    seq: int
    message_type: int
    body: bytes

    def encode(self) -> bytes:
        """Pre-auth frame encoder. Validated once a capture lands."""
        raise NotImplementedError(
            "MTProto frame encoding is gated on a live capture. "
            "See docs/CAPTURE.md for the mitmproxy+Frida workflow "
            "that produces the reference bytes."
        )

    @classmethod
    def decode(cls, buf: bytes) -> "Frame":
        raise NotImplementedError(
            "MTProto frame decoding is gated on a live capture. "
            "See docs/CAPTURE.md."
        )


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


# Convenience for the live smoketest: read a length-prefixed frame
# with no interpretation, just so we can prove bytes flow. Safe to
# call pre-capture because it doesn't assume a protocol layout.
def read_length_prefixed(conn, max_len: int = 1 << 20) -> bytes:
    raw = _read_exact(conn, 4)
    (n,) = struct.unpack("<I", raw)
    if n > max_len:
        raise ValueError(f"oversize frame: {n}")
    return _read_exact(conn, n)
