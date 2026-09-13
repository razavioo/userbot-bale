"""
Fake TUN device for tests and on-machine end-to-end validation.

Implements the minimal `TunDevice` surface that `VpnRunner` depends on
(`name`, `read_packet`, `write_packet`, `close`) backed by two thread-
safe queues. Packets injected via `inject()` become readable from the
runner; packets the runner writes via `write_packet()` become readable
via `pop_written()`.

This is the linchpin for multi-process end-to-end tests: pair one
FakeTun with one VpnRunner; pair another FakeTun with another VpnRunner;
hook their transports together (InMemoryTransport.pair or real LiveKit);
then simulate IP packets entering the client side and observe them
emerge at the exit-node side.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional


class FakeTun:
    def __init__(self, name: str = "fake0") -> None:
        self._name = name
        self._outbound: "queue.Queue[bytes]" = queue.Queue()   # kernel→runner
        self._inbound: "queue.Queue[bytes]" = queue.Queue()    # runner→kernel
        self._closed = threading.Event()

    @property
    def name(self) -> str:
        return self._name

    # --- runner-facing (matches TunDevice) ---

    def read_packet(self, bufsize: int = 2048) -> Optional[bytes]:
        while not self._closed.is_set():
            try:
                return self._outbound.get(timeout=0.25)
            except queue.Empty:
                continue
        return None

    def write_packet(self, packet: bytes) -> int:
        if self._closed.is_set():
            return 0
        self._inbound.put(bytes(packet))
        return len(packet)

    def close(self) -> None:
        self._closed.set()
        # Wake any blocked read_packet with a sentinel.
        try:
            self._outbound.put_nowait(b"")
        except queue.Full:
            pass

    # --- test-harness helpers ---

    def inject(self, packet: bytes) -> None:
        """Simulate a packet arriving from the kernel (userspace app)."""
        self._outbound.put(bytes(packet))

    def pop_written(self, timeout: float = 1.0) -> Optional[bytes]:
        """Drain one packet the runner wrote (would go to the kernel)."""
        try:
            return self._inbound.get(timeout=timeout)
        except queue.Empty:
            return None
