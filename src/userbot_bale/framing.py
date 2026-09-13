"""
Application-level framing on top of GGWave's 140-byte payload.

GGWave already provides Reed-Solomon FEC and packet boundaries. This layer adds:

  * Multi-fragment reassembly (messages larger than one GGWave packet).
  * Per-message sequencing for ordering and duplicate detection.
  * A magic byte + version so the decoder can cleanly reject noise that
    happens to survive RS but was not produced by this protocol.
  * A short CRC-8 over the header to guard against rare RS false-accepts.

Header layout (8 bytes), followed by up to 132 payload bytes:

    offset  size  field       notes
    ------  ----  ---------   -----------------------------------------
      0      1    magic       0xBA  (userbot-bale)
      1      1    version     0x01
      2      1    flags       bit0 START, bit1 END, bit2 SINGLE
      3      2    msg_id      u16 little-endian, wraps
      5      1    frag_idx    0..total-1
      6      1    frag_total  1..255
      7      1    hdr_crc8    CRC-8/SMBUS over bytes [0..6]

A SINGLE-flagged frame carries an entire message in one fragment
(frag_idx=0, frag_total=1). This is the hot path for short chat messages.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from enum import IntFlag
from typing import Iterator

log = logging.getLogger(__name__)

MAGIC = 0xBA
VERSION = 0x01
HEADER_SIZE = 8
GGWAVE_MAX_PAYLOAD = 140
MAX_FRAGMENT_PAYLOAD = GGWAVE_MAX_PAYLOAD - HEADER_SIZE  # 132
MAX_FRAGMENTS = 255
MAX_MESSAGE_SIZE = MAX_FRAGMENT_PAYLOAD * MAX_FRAGMENTS  # ~33 KiB


class FrameFlag(IntFlag):
    START = 0x01
    END = 0x02
    SINGLE = 0x04  # START | END implied; kept distinct for fast-path


def _crc8(data: bytes) -> int:
    """CRC-8/SMBUS (poly 0x07, init 0x00). Small, sufficient for an 8-byte header."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


@dataclass(frozen=True)
class Frame:
    msg_id: int
    frag_idx: int
    frag_total: int
    flags: FrameFlag
    payload: bytes

    def encode(self) -> bytes:
        if not (0 <= self.msg_id <= 0xFFFF):
            raise ValueError("msg_id out of range")
        if not (1 <= self.frag_total <= MAX_FRAGMENTS):
            raise ValueError("frag_total out of range")
        if not (0 <= self.frag_idx < self.frag_total):
            raise ValueError("frag_idx out of range")
        if len(self.payload) > MAX_FRAGMENT_PAYLOAD:
            raise ValueError(
                f"payload {len(self.payload)} > {MAX_FRAGMENT_PAYLOAD}"
            )
        header = struct.pack(
            "<BBBHBB",
            MAGIC,
            VERSION,
            int(self.flags) & 0xFF,
            self.msg_id,
            self.frag_idx,
            self.frag_total,
        )
        header += bytes([_crc8(header)])
        return header + self.payload

    @classmethod
    def decode(cls, buf: bytes) -> "Frame | None":
        if len(buf) < HEADER_SIZE:
            return None
        magic, version, flags, msg_id, frag_idx, frag_total, crc = struct.unpack(
            "<BBBHBBB", buf[:HEADER_SIZE]
        )
        if magic != MAGIC:
            return None
        if version != VERSION:
            log.debug("frame version mismatch: got %d", version)
            return None
        if crc != _crc8(buf[: HEADER_SIZE - 1]):
            log.debug("frame header CRC mismatch")
            return None
        if frag_total == 0 or frag_idx >= frag_total:
            return None
        payload = buf[HEADER_SIZE:]
        if len(payload) > MAX_FRAGMENT_PAYLOAD:
            return None
        return cls(
            msg_id=msg_id,
            frag_idx=frag_idx,
            frag_total=frag_total,
            flags=FrameFlag(flags),
            payload=bytes(payload),
        )


def fragment(message: bytes, msg_id: int) -> Iterator[Frame]:
    """Split a logical message into ordered Frames. One frame per yield."""
    if len(message) > MAX_MESSAGE_SIZE:
        raise ValueError(f"message exceeds {MAX_MESSAGE_SIZE} bytes")
    msg_id &= 0xFFFF

    if len(message) <= MAX_FRAGMENT_PAYLOAD:
        yield Frame(
            msg_id=msg_id,
            frag_idx=0,
            frag_total=1,
            flags=FrameFlag.SINGLE | FrameFlag.START | FrameFlag.END,
            payload=bytes(message),
        )
        return

    chunks = [
        message[i : i + MAX_FRAGMENT_PAYLOAD]
        for i in range(0, len(message), MAX_FRAGMENT_PAYLOAD)
    ]
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        flags = FrameFlag(0)
        if i == 0:
            flags |= FrameFlag.START
        if i == total - 1:
            flags |= FrameFlag.END
        yield Frame(
            msg_id=msg_id,
            frag_idx=i,
            frag_total=total,
            flags=flags,
            payload=bytes(chunk),
        )


class Reassembler:
    """
    Stateful reassembler. Feed Frames, get completed messages back.

    Tracks up to `max_pending` in-flight multi-fragment messages. A message
    is evicted if a newer msg_id wraps around into its slot — correct for
    real-time streaming where partial losses should not stall the display.
    """

    def __init__(self, max_pending: int = 16) -> None:
        self._pending: dict[int, dict[int, bytes]] = {}
        self._totals: dict[int, int] = {}
        self._order: list[int] = []
        self._max_pending = max_pending
        self._last_emitted: set[int] = set()
        self._last_emitted_order: list[int] = []

    def push(self, frame: Frame) -> bytes | None:
        """
        Returns the fully-assembled message bytes when `frame` completes one,
        else None. SINGLE frames return immediately without touching state.
        """
        if FrameFlag.SINGLE in frame.flags or frame.frag_total == 1:
            if self._seen_recently(frame.msg_id):
                return None
            self._mark_emitted(frame.msg_id)
            return frame.payload

        slot = self._pending.setdefault(frame.msg_id, {})
        if frame.msg_id not in self._totals:
            self._totals[frame.msg_id] = frame.frag_total
            self._order.append(frame.msg_id)
            self._evict_if_needed()
        elif self._totals[frame.msg_id] != frame.frag_total:
            log.debug("frag_total mismatch for msg_id=%d", frame.msg_id)
            return None

        slot[frame.frag_idx] = frame.payload
        if len(slot) == frame.frag_total:
            ordered = b"".join(slot[i] for i in range(frame.frag_total))
            self._drop(frame.msg_id)
            self._mark_emitted(frame.msg_id)
            return ordered
        return None

    def _evict_if_needed(self) -> None:
        while len(self._order) > self._max_pending:
            victim = self._order.pop(0)
            self._pending.pop(victim, None)
            self._totals.pop(victim, None)
            log.debug("evicted incomplete msg_id=%d", victim)

    def _drop(self, msg_id: int) -> None:
        self._pending.pop(msg_id, None)
        self._totals.pop(msg_id, None)
        try:
            self._order.remove(msg_id)
        except ValueError:
            pass

    def _seen_recently(self, msg_id: int) -> bool:
        return msg_id in self._last_emitted

    def _mark_emitted(self, msg_id: int) -> None:
        if msg_id in self._last_emitted:
            return
        self._last_emitted.add(msg_id)
        self._last_emitted_order.append(msg_id)
        while len(self._last_emitted_order) > 64:
            old = self._last_emitted_order.pop(0)
            self._last_emitted.discard(old)
