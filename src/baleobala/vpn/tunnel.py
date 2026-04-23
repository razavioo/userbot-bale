"""
Tunnel core: moves IP packets over a Transport with session-id framing,
selective-repeat ARQ, and MTU-split reassembly.

Usage:
    tunnel = Tunnel(transport, sess_id=0x1234)
    tunnel.start(on_packet=handle_inbound_ip_packet)
    tunnel.send_packet(ip_packet_bytes)
    ...
    tunnel.stop()

on_packet is invoked from the receive thread with a reassembled IP
packet. It MUST return quickly; do its own queueing if it needs to
block.

ARQ model
---------
Selective-repeat. Each outbound frame carries a 24-bit `seq`. The
peer replies with ACK frames (empty payload, flags=ACK, seq=acked
seq). Unacked frames are retransmitted after `ack_timeout` up to
`max_retries` times, then dropped. This is good enough for a VPN
where upper-layer TCP will itself retry; the ARQ exists mainly to
hide short bursts of transport loss (voice-call hiccups) from TCP.

On low-bandwidth transports (audio), set `window=1` to degrade to
stop-and-wait; the logic is identical.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .framing_vpn import (
    HEADER_SIZE,
    SEQ_MODULO,
    VpnFlag,
    VpnFrame,
    split_packet,
)
from .transports import Transport

log = logging.getLogger(__name__)

OnPacket = Callable[[bytes], None]
OnEvent = Callable[[str, dict[str, object]], None]


@dataclass
class _Pending:
    frame: VpnFrame
    sent_at: float
    retries: int = 0


@dataclass
class _RxPacket:
    parts: dict[int, bytes] = field(default_factory=dict)
    got_last: bool = False


class Tunnel:
    def __init__(
        self,
        transport: Transport,
        *,
        sess_id: int,
        window: int = 32,
        ack_timeout: float = 0.5,
        max_retries: int = 8,
        recv_timeout: float = 0.2,
        mtu_override: int | None = None,
    ) -> None:
        self._tx = transport
        self._tx_lock = threading.Lock()  # protects swaps
        self._sess_id = sess_id & 0xFFFF
        self._window = max(1, window)
        self._ack_timeout = ack_timeout
        self._max_retries = max_retries
        self._recv_timeout = recv_timeout
        # When mtu_override is set, the tunnel frame cap is fixed and
        # independent of transport.mtu — essential for hot-swap: if we
        # fragmented a packet into 14 KiB frames on DC, we can't stuff
        # them down audio's 128 B pipe on failover. The runner computes
        # the min across all candidate transports and passes it here.
        self._mtu_override = mtu_override

        self._seq_next = 0
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._send_slot = threading.Semaphore(self._window)

        self._on_packet: OnPacket | None = None
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._retry_thread: threading.Thread | None = None

        # Receive-side packet reassembly. Keyed by the seq of the first
        # SPLIT fragment in a packet; LAST closes the packet.
        self._rx_buf: dict[int, _RxPacket] = {}
        self._rx_expected_start: int | None = None

        # Dedup: remember last N delivered seqs so a retransmit from the
        # peer after our ACK is dropped doesn't double-deliver.
        self._seen: set[int] = set()
        self._seen_order: list[int] = []
        self._seen_cap = 4096
        self._event_handlers: list[OnEvent] = []

    @property
    def sess_id(self) -> int:
        return self._sess_id

    # ---- public API -------------------------------------------------------

    def start(self, on_packet: OnPacket) -> None:
        if self._rx_thread is not None:
            raise RuntimeError("tunnel already started")
        self._on_packet = on_packet
        self._stop.clear()
        self._rx_thread = threading.Thread(
            target=self._run_rx, name="vpn-tun-rx", daemon=True
        )
        self._retry_thread = threading.Thread(
            target=self._run_retry, name="vpn-tun-retry", daemon=True
        )
        self._rx_thread.start()
        self._retry_thread.start()

    def add_event_handler(self, handler: OnEvent) -> None:
        self._event_handlers.append(handler)

    def stop(self) -> None:
        self._stop.set()
        # release any send that's waiting on window
        for _ in range(self._window):
            try:
                self._send_slot.release()
            except ValueError:
                pass
        if self._rx_thread:
            self._rx_thread.join(timeout=2)
        if self._retry_thread:
            self._retry_thread.join(timeout=2)

    def send_packet(self, packet: bytes) -> None:
        """Split `packet` into one or more frames and transmit with ARQ."""
        effective_mtu = self._mtu_override if self._mtu_override is not None else self._tx.mtu
        max_payload = max(1, effective_mtu - HEADER_SIZE)
        with self._pending_lock:
            start = self._seq_next
            frames = split_packet(
                packet,
                sess_id=self._sess_id,
                start_seq=start,
                max_payload=max_payload,
            )
            self._seq_next = (start + len(frames)) % SEQ_MODULO

        for f in frames:
            # Block until window has room. stop() releases these slots.
            self._send_slot.acquire()
            if self._stop.is_set():
                return
            with self._pending_lock:
                self._pending[f.seq] = _Pending(frame=f, sent_at=time.monotonic())
            try:
                with self._tx_lock:
                    self._tx.send_bytes(f.encode())
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    "transport_send_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    seq=f.seq,
                )
                raise

    # ---- internals --------------------------------------------------------

    def _run_rx(self) -> None:
        while not self._stop.is_set():
            with self._tx_lock:
                tx = self._tx
            buf = tx.recv_bytes(timeout=self._recv_timeout)
            if buf is None:
                continue
            frame = VpnFrame.decode(buf)
            if frame is None:
                continue
            if frame.sess_id != self._sess_id:
                log.debug("drop frame from sess_id=%d (expected %d)",
                          frame.sess_id, self._sess_id)
                continue
            if VpnFlag.ACK in frame.flags:
                self._handle_ack(frame.seq)
                continue
            # Data frame — ACK it first (cheap), then process.
            self._send_ack(frame.seq)
            if frame.seq in self._seen:
                continue
            self._remember_seen(frame.seq)
            self._deliver(frame)

    def _run_retry(self) -> None:
        while not self._stop.is_set():
            time.sleep(self._ack_timeout / 2)
            now = time.monotonic()
            to_retry: list[_Pending] = []
            to_drop: list[int] = []
            with self._pending_lock:
                for seq, p in list(self._pending.items()):
                    if now - p.sent_at < self._ack_timeout:
                        continue
                    if p.retries >= self._max_retries:
                        to_drop.append(seq)
                        continue
                    p.retries += 1
                    p.sent_at = now
                    to_retry.append(p)
            for seq in to_drop:
                with self._pending_lock:
                    self._pending.pop(seq, None)
                self._send_slot.release()
                log.warning("vpn: dropping seq=%d after max retries", seq)
                self._emit("frame_dropped", seq=seq, reason="max_retries")
            for p in to_retry:
                retry_frame = VpnFrame(
                    sess_id=p.frame.sess_id,
                    seq=p.frame.seq,
                    flags=p.frame.flags | VpnFlag.RETRY,
                    payload=p.frame.payload,
                )
                try:
                    with self._tx_lock:
                        self._tx.send_bytes(retry_frame.encode())
                except Exception as exc:  # noqa: BLE001
                    self._emit(
                        "transport_retry_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                        seq=p.frame.seq,
                        retries=p.retries,
                    )

    def _handle_ack(self, seq: int) -> None:
        with self._pending_lock:
            if self._pending.pop(seq, None) is None:
                return
        self._send_slot.release()

    def _send_ack(self, seq: int) -> None:
        ack = VpnFrame(
            sess_id=self._sess_id, seq=seq, flags=VpnFlag.ACK, payload=b""
        )
        with self._tx_lock:
            self._tx.send_bytes(ack.encode())

    def swap_transport(self, new_transport: Transport) -> Transport:
        """Atomically swap the underlying byte carrier. Pending ARQ
        frames are re-transmitted on the new transport by the retry
        thread on its next tick. Returns the old transport so the
        caller can close it.

        Only safe when the tunnel's frame MTU was fixed at startup
        (mtu_override). Otherwise pending frames may exceed the new
        transport's MTU and the retry thread will raise on send."""
        with self._tx_lock:
            old = self._tx
            self._tx = new_transport
        log.info("swapped tunnel transport: %s → %s",
                 type(old).__name__, type(new_transport).__name__)
        self._emit(
            "transport_swapped",
            old_transport=type(old).__name__,
            new_transport=type(new_transport).__name__,
        )
        return old

    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def _deliver(self, frame: VpnFrame) -> None:
        cb = self._on_packet
        if cb is None:
            return
        if VpnFlag.SPLIT not in frame.flags:
            # single-frame packet
            try:
                cb(frame.payload)
            except Exception:  # pragma: no cover - user cb
                log.exception("on_packet raised")
            return
        # Multi-frame packet. Find its start seq by walking back until we
        # hit a LAST (of the previous packet) or the beginning of our
        # buffer. Simpler heuristic: a SPLIT frame belongs to the open
        # packet whose start seq is <= this seq; if none open, this frame
        # itself starts a new packet.
        start = self._find_or_open_start(frame.seq)
        pkt = self._rx_buf.setdefault(start, _RxPacket())
        pkt.parts[frame.seq] = frame.payload
        if VpnFlag.LAST in frame.flags:
            pkt.got_last = True
        if pkt.got_last:
            self._try_emit(start, cb)

    def _find_or_open_start(self, seq: int) -> int:
        # If there's already an open packet whose start is <= seq and
        # seq is contiguous within it, use it. Otherwise open a new one.
        for start, pkt in self._rx_buf.items():
            if start <= seq and seq - start < 256:  # sanity
                return start
        self._rx_buf[seq] = _RxPacket()
        return seq

    def _try_emit(self, start: int, cb: OnPacket) -> None:
        pkt = self._rx_buf.get(start)
        if pkt is None or not pkt.got_last:
            return
        # Determine last seq: the max key in parts.
        last = max(pkt.parts)
        expected = last - start + 1
        if len(pkt.parts) != expected:
            return  # still waiting for a middle fragment
        try:
            data = b"".join(pkt.parts[start + i] for i in range(expected))
        except KeyError:
            return
        self._rx_buf.pop(start, None)
        try:
            cb(data)
        except Exception:  # pragma: no cover
            log.exception("on_packet raised")

    def _remember_seen(self, seq: int) -> None:
        self._seen.add(seq)
        self._seen_order.append(seq)
        while len(self._seen_order) > self._seen_cap:
            old = self._seen_order.pop(0)
            self._seen.discard(old)

    def _emit(self, event: str, **payload: object) -> None:
        for handler in list(self._event_handlers):
            try:
                handler(event, payload)
            except Exception:  # pragma: no cover
                log.exception("tunnel event handler raised")
