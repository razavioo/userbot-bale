"""In-memory byte channel used for tests and local composition."""

from __future__ import annotations

import queue
from dataclasses import dataclass

from baleobala.runtime.interfaces import ByteChannel

_CLOSE = object()


@dataclass
class MemoryByteChannel:
    """A paired byte channel that behaves like a tiny transport."""

    _inbound: "queue.Queue[bytes | object]"
    _outbound: "queue.Queue[bytes | object]"
    _closed: bool = False

    @classmethod
    def pair(cls) -> tuple["MemoryByteChannel", "MemoryByteChannel"]:
        left_in: "queue.Queue[bytes | object]" = queue.Queue()
        right_in: "queue.Queue[bytes | object]" = queue.Queue()
        return (
            cls(_inbound=left_in, _outbound=right_in),
            cls(_inbound=right_in, _outbound=left_in),
        )

    def send(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("channel closed")
        self._outbound.put(bytes(data))

    def recv(self, timeout: float | None = None) -> bytes | None:
        if self._closed:
            return None
        try:
            item = self._inbound.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is _CLOSE:
            self._closed = True
            return None
        return bytes(item)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._outbound.put(_CLOSE)

