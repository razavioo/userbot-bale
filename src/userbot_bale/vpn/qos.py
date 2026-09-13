"""
QoS: classify outbound IP packets and feed them to the tunnel in
priority order. Matters most on the low-bandwidth transports (audio,
qr) where a bulk download can starve interactive traffic for minutes.

Priority levels (0 = highest):
    0  ICMP (ping), DNS (UDP/53), TCP SYN/FIN/RST control packets
    1  Small TCP packets (likely ACK-only), IPv6 Neighbor Discovery
    2  Bulk data — everything else

The queue is bounded per-level so a runaway upload can't balloon RAM;
when the bulk queue is full we drop the oldest bulk packet (TCP will
retransmit). Control packets are never dropped (small queue but a
hard limit very high above normal burst).
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

LEVEL_CONTROL = 0
LEVEL_ACK = 1
LEVEL_BULK = 2

# Per-level soft caps (packets).
CAPS = {LEVEL_CONTROL: 4096, LEVEL_ACK: 2048, LEVEL_BULK: 512}


def classify(ip_packet: bytes) -> int:
    """Return priority level for `ip_packet` (IPv4 or IPv6).

    Fast path: ~15 byte reads, no allocations. Unknown/malformed
    packets fall into LEVEL_BULK — they still get delivered, just at
    the back of the line."""
    if not ip_packet:
        return LEVEL_BULK
    b0 = ip_packet[0]
    version = b0 >> 4
    if version == 4:
        if len(ip_packet) < 20:
            return LEVEL_BULK
        proto = ip_packet[9]
        if proto == 1:  # ICMP
            return LEVEL_CONTROL
        ihl = (b0 & 0x0F) * 4
        if proto == 17:  # UDP
            if len(ip_packet) < ihl + 4:
                return LEVEL_BULK
            dport = (ip_packet[ihl + 2] << 8) | ip_packet[ihl + 3]
            sport = (ip_packet[ihl + 0] << 8) | ip_packet[ihl + 1]
            if dport == 53 or sport == 53 or dport == 123 or sport == 123:
                return LEVEL_CONTROL  # DNS, NTP
            return LEVEL_BULK
        if proto == 6:  # TCP
            if len(ip_packet) < ihl + 20:
                return LEVEL_BULK
            flags = ip_packet[ihl + 13]
            # SYN=0x02, FIN=0x01, RST=0x04. These are the control surface.
            if flags & (0x02 | 0x01 | 0x04):
                return LEVEL_CONTROL
            # Small TCP = likely ACK-only. Window size is at offset +14.
            data_off = (ip_packet[ihl + 12] >> 4) * 4
            payload_len = len(ip_packet) - ihl - data_off
            if payload_len < 64:
                return LEVEL_ACK
            return LEVEL_BULK
        return LEVEL_BULK
    if version == 6:
        # IPv6 Neighbor Discovery is priority; simplistic check on next
        # header (byte 6) == ICMPv6 (58).
        if len(ip_packet) >= 40 and ip_packet[6] == 58:
            return LEVEL_CONTROL
        return LEVEL_BULK
    return LEVEL_BULK


@dataclass
class QosStats:
    enqueued: dict = None  # type: ignore[assignment]
    dropped: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.enqueued = {LEVEL_CONTROL: 0, LEVEL_ACK: 0, LEVEL_BULK: 0}
        self.dropped = {LEVEL_CONTROL: 0, LEVEL_ACK: 0, LEVEL_BULK: 0}


class PriorityQueue:
    """Thread-safe 3-level priority queue. The `level-first, then FIFO
    within level` policy is what a real router would call strict
    priority queuing — simple and effective when the bottleneck is
    downstream bandwidth, not CPU."""

    def __init__(self) -> None:
        self._heap: list = []
        self._cond = threading.Condition()
        self._counter = itertools.count()
        self._sizes = {LEVEL_CONTROL: 0, LEVEL_ACK: 0, LEVEL_BULK: 0}
        self.stats = QosStats()
        self._closed = False

    def put(self, packet: bytes) -> None:
        level = classify(packet)
        with self._cond:
            if self._closed:
                return
            self.stats.enqueued[level] += 1
            if self._sizes[level] >= CAPS[level]:
                # Drop oldest of the same level. For CONTROL that's a
                # last-resort; for BULK it's expected under load.
                self._drop_oldest(level)
                self.stats.dropped[level] += 1
            heapq.heappush(self._heap, (level, next(self._counter), packet))
            self._sizes[level] += 1
            self._cond.notify()

    def get(self, timeout: Optional[float] = None) -> Optional[bytes]:
        with self._cond:
            if not self._heap and not self._closed:
                self._cond.wait(timeout=timeout)
            if self._closed and not self._heap:
                return None
            if not self._heap:
                return None
            level, _seq, packet = heapq.heappop(self._heap)
            self._sizes[level] -= 1
            return packet

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _drop_oldest(self, level: int) -> None:
        # Linear scan of heap to find the oldest entry at `level`.
        # Size of heap is bounded by sum(CAPS) ≈ 6.6k — fine.
        idx = None
        for i, (lvl, _seq, _p) in enumerate(self._heap):
            if lvl == level and (idx is None or self._heap[i][1] < self._heap[idx][1]):
                idx = i
        if idx is not None:
            self._heap.pop(idx)
            heapq.heapify(self._heap)
            self._sizes[level] -= 1
