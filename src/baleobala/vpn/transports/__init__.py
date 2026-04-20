"""
Transport abstraction. A transport moves opaque byte frames between two
endpoints; the VPN tunnel core sits above it and handles ARQ/session.

Every transport must:
  * expose `mtu` — max payload bytes per send_bytes() call
  * expose `rate_hint` — rough bytes/sec for the router's probe logic
  * be thread-safe for one producer (send) + one consumer (recv)

The `InMemoryTransport` below is used by tests and by the `vpn loopback`
CLI mode to drive both endpoints in a single process.
"""

from __future__ import annotations

import queue
from typing import Protocol


class Transport(Protocol):
    mtu: int
    rate_hint: float  # bytes per second

    def send_bytes(self, data: bytes) -> None: ...

    def recv_bytes(self, timeout: float | None = None) -> bytes | None: ...

    def close(self) -> None: ...


class InMemoryTransport:
    """Paired in-process transport for loopback tests. Lossless by default."""

    def __init__(
        self,
        *,
        mtu: int = 1500,
        rate_hint: float = 1_000_000.0,
        loss: float = 0.0,
        rng_seed: int | None = None,
        send_delay: float = 0.0,
    ) -> None:
        self.mtu = mtu
        self.rate_hint = rate_hint
        self._loss = loss
        self._send_delay = send_delay
        self._rx: "queue.Queue[bytes]" = queue.Queue()
        self._peer: "InMemoryTransport | None" = None
        import random

        self._rng = random.Random(rng_seed)
        self._closed = False

    @staticmethod
    def pair(**kwargs) -> tuple["InMemoryTransport", "InMemoryTransport"]:
        a = InMemoryTransport(**kwargs)
        b = InMemoryTransport(**kwargs)
        a._peer = b
        b._peer = a
        return a, b

    def send_bytes(self, data: bytes) -> None:
        if self._closed or self._peer is None or self._peer._closed:
            return
        if len(data) > self.mtu:
            raise ValueError(f"frame {len(data)} > mtu {self.mtu}")
        if self._loss > 0 and self._rng.random() < self._loss:
            return  # dropped
        if self._send_delay > 0:
            import time as _t
            _t.sleep(self._send_delay)
        self._peer._rx.put(bytes(data))

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True
