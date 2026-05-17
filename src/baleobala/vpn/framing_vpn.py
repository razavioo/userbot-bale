"""
VPN framing. Distinct from baleobala.framing: that layer is tuned for
GGWave's 140-byte payload + RS FEC; this layer carries IP packets over
any pluggable transport and needs a wider sequence space and an ACK bit
for ARQ.

Header layout (8 bytes), followed by payload up to transport.mtu - 8:

    offset  size  field      notes
    ------  ----  -------    ---------------------------------------
      0      1    magic      0xBB  (distinct from baleobala 0xBA)
      1      1    version    0x01
      2      2    sess_id    u16 LE; rolled per tunnel restart
      4      3    seq        24-bit LE; wraps at 2^24
      7      1    flags      bit0 ACK, bit1 RETRY, bit2 LAST,
                             bit3 SPLIT (one IP packet spans >1 frame)

An IP packet larger than the transport payload cap is split into N
frames with the same seq space; only the final one has LAST. The
simple rule: start-of-packet is the first frame whose predecessor was
LAST (or was not SPLIT). Losing any SPLIT frame drops the whole packet;
ARQ retransmits the individual frame, not the packet.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntFlag

MAGIC = 0xBB
VERSION = 0x01
HEADER_SIZE = 8
SEQ_MODULO = 1 << 24
SESS_MODULO = 1 << 16


class VpnFlag(IntFlag):
    ACK = 0x01
    RETRY = 0x02
    LAST = 0x04
    SPLIT = 0x08


@dataclass(frozen=True)
class VpnFrame:
    sess_id: int
    seq: int
    flags: VpnFlag
    payload: bytes

    def encode(self) -> bytes:
        if not 0 <= self.sess_id < SESS_MODULO:
            raise ValueError("sess_id out of range")
        if not 0 <= self.seq < SEQ_MODULO:
            raise ValueError("seq out of range")
        seq = self.seq
        header = struct.pack(
            "<BBHBBBB",
            MAGIC,
            VERSION,
            self.sess_id,
            seq & 0xFF,
            (seq >> 8) & 0xFF,
            (seq >> 16) & 0xFF,
            int(self.flags) & 0xFF,
        )
        return header + self.payload

    @classmethod
    def decode(cls, buf: bytes) -> "VpnFrame | None":
        if len(buf) < HEADER_SIZE:
            return None
        magic, version, sess_id, s0, s1, s2, flags = struct.unpack(
            "<BBHBBBB", buf[:HEADER_SIZE]
        )
        if magic != MAGIC or version != VERSION:
            return None
        seq = s0 | (s1 << 8) | (s2 << 16)
        return cls(
            sess_id=sess_id,
            seq=seq,
            flags=VpnFlag(flags),
            payload=bytes(buf[HEADER_SIZE:]),
        )


def split_packet(
    packet: bytes, *, sess_id: int, start_seq: int, max_payload: int
) -> list[VpnFrame]:
    """Split one IP packet into 1..N frames using consecutive seq numbers."""
    if max_payload <= 0:
        raise ValueError("max_payload must be positive")
    if len(packet) <= max_payload:
        return [
            VpnFrame(
                sess_id=sess_id,
                seq=start_seq % SEQ_MODULO,
                flags=VpnFlag.LAST,
                payload=bytes(packet),
            )
        ]
    chunks = [packet[i : i + max_payload] for i in range(0, len(packet), max_payload)]
    out: list[VpnFrame] = []
    for i, chunk in enumerate(chunks):
        flags = VpnFlag.SPLIT
        if i == len(chunks) - 1:
            flags |= VpnFlag.LAST
        out.append(
            VpnFrame(
                sess_id=sess_id,
                seq=(start_seq + i) % SEQ_MODULO,
                flags=flags,
                payload=bytes(chunk),
            )
        )
    return out
