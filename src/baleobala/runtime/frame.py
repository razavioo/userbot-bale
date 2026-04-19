"""Small binary frames for the tunnel runtime."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

MAGIC = b"BT"
VERSION = 1
HEADER_FMT = "<2sBBBBIIH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_PAYLOAD = 256


class TunnelFrameType(IntEnum):
    OPEN = 1
    DATA = 2
    ACK = 3
    PING = 4
    CLOSE = 5
    RESET = 6


class TunnelRole(IntEnum):
    CLIENT = 1
    SERVER = 2


@dataclass(frozen=True)
class TunnelFrame:
    frame_type: TunnelFrameType
    seq: int
    ack: int = 0
    flags: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        if not (0 <= self.seq <= 0xFFFFFFFF):
            raise ValueError("seq out of range")
        if not (0 <= self.ack <= 0xFFFFFFFF):
            raise ValueError("ack out of range")
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload too large: {len(self.payload)}")
        header = struct.pack(
            HEADER_FMT,
            MAGIC,
            VERSION,
            int(self.frame_type) & 0xFF,
            self.flags & 0xFF,
            0,
            self.seq,
            self.ack,
            len(self.payload),
        )
        return header + self.payload


def encode_tunnel_frame(frame: TunnelFrame) -> bytes:
    return frame.encode()


def decode_tunnel_frame(buf: bytes) -> TunnelFrame | None:
    if len(buf) < HEADER_SIZE:
        return None
    magic, version, frame_type, flags, _reserved, seq, ack, payload_len = struct.unpack(
        HEADER_FMT, buf[:HEADER_SIZE]
    )
    if magic != MAGIC or version != VERSION:
        return None
    payload = buf[HEADER_SIZE:]
    if len(payload) != payload_len:
        return None
    try:
        return TunnelFrame(
            frame_type=TunnelFrameType(frame_type),
            seq=seq,
            ack=ack,
            flags=flags,
            payload=payload,
        )
    except ValueError:
        return None

