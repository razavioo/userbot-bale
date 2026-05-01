"""
Bonded transport: stripe traffic across N parallel DataChannels for N× speed.

Each Bale LiveKit DataChannel tops out at ~100-200 KB/s. By opening
several parallel calls (all from the same two accounts), we create
independent WebRTC DataChannels and round-robin sends across them.
Receives merge from all channels into a single queue.

Usage on RELAY side::

    transport = BondedTransport.from_sessions(sessions, topic="vpn")
    relay = TunnelTcpRelay(transport, secret=psk)
    relay.serve_forever()

Usage on CLIENT side::

    transport = BondedTransport.from_sessions(sessions, topic="vpn")
    server = Socks5ProxyServer(transport, ...)
    server.serve_forever()

The number of parallel channels (N) is configurable; default is 8
(the Bale per-room participant cap). Each LiveKitSession is a separate
Bale call. Since both accounts can be in multiple calls simultaneously,
this works with just 2 registered accounts.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Protocol

log = logging.getLogger(__name__)


class _TransportLike(Protocol):
    mtu: int
    rate_hint: float

    def send_bytes(self, data: bytes) -> None: ...
    def recv_bytes(self, timeout: float | None = None) -> bytes | None: ...
    def close(self) -> None: ...


class BondedTransport:
    """N parallel transports presented as one, with round-robin send
    and merged receive.

    Thread-safe for one producer thread (send) + one consumer thread
    (recv), which is the contract the proxy runtime expects.
    """

    def __init__(self, channels: list[_TransportLike]) -> None:
        if not channels:
            raise ValueError("BondedTransport requires at least one channel")
        self._channels = list(channels)
        self._send_idx = 0
        self._send_lock = threading.Lock()
        self._recv_queue: queue.Queue[bytes] = queue.Queue()
        self._closed = False
        self._stop = threading.Event()

        # Use the minimum MTU / sum of rate_hints.
        self.mtu = min(ch.mtu for ch in self._channels)
        self.rate_hint = sum(ch.rate_hint for ch in self._channels)

        # Start a reader thread per channel to merge into one queue.
        self._readers: list[threading.Thread] = []
        for i, ch in enumerate(self._channels):
            t = threading.Thread(
                target=self._read_loop, args=(ch,),
                name=f"bonded-rx-{i}", daemon=True,
            )
            self._readers.append(t)
            t.start()
        log.info(
            "bonded transport: %d channels, mtu=%d, rate_hint=%.0f B/s",
            len(self._channels), self.mtu, self.rate_hint,
        )

    @classmethod
    def from_sessions(
        cls,
        sessions: list,
        *,
        topic: str = "vpn",
        reliable: bool = True,
    ) -> "BondedTransport":
        """Build from a list of LiveKitSession objects."""
        channels = [s.data_channel(topic=topic, reliable=reliable) for s in sessions]
        return cls(channels)

    @property
    def closed(self) -> bool:
        return self._closed

    def send_bytes(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("bonded transport closed")
        with self._send_lock:
            # Round-robin across channels.
            ch = self._channels[self._send_idx]
            self._send_idx = (self._send_idx + 1) % len(self._channels)
        ch.send_bytes(data)

    # Alias for the proxy transport adapter interface.
    def send(self, data: bytes) -> None:
        self.send_bytes(data)

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        if self._closed:
            return None
        try:
            return self._recv_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # Alias for the proxy transport adapter interface.
    def recv(self, timeout: float | None = None) -> bytes | None:
        return self.recv_bytes(timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        for ch in self._channels:
            try:
                ch.close()
            except Exception:  # noqa: BLE001
                pass
        for t in self._readers:
            t.join(timeout=1)

    def _read_loop(self, ch: _TransportLike) -> None:
        while not self._stop.is_set():
            try:
                data = ch.recv_bytes(timeout=0.25)
            except Exception:  # noqa: BLE001
                if self._stop.is_set():
                    return
                continue
            if data is not None:
                self._recv_queue.put(data)
