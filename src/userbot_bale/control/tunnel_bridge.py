"""Tunnel bridge helpers for the macOS packet-tunnel product path."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable

from userbot_bale.vpn.runner import VpnRunner


@dataclass
class _QueueTun:
    """Minimal TunDevice-compatible adapter for a running VpnRunner."""

    inbound: "queue.Queue[bytes]"
    outbound: "queue.Queue[bytes]"
    closed_event: threading.Event
    name: str = "vpn-bridge"

    def read_packet(self, bufsize: int = 2048) -> bytes | None:  # noqa: ARG002
        while not self.closed_event.is_set():
            try:
                return self.outbound.get(timeout=0.25)
            except queue.Empty:
                continue
        return None

    def write_packet(self, packet: bytes) -> int:
        if self.closed_event.is_set():
            return 0
        self.inbound.put(bytes(packet))
        return len(packet)

    def close(self) -> None:
        self.closed_event.set()
        try:
            self.outbound.put_nowait(b"")
        except queue.Full:
            pass


class VpnTunnelBridge:
    """Wrap a running :class:`VpnRunner` behind the byte-bridge protocol."""

    def __init__(self, runner: VpnRunner | None = None, *, tun_name: str = "vpn-bridge") -> None:
        self._runner = runner
        self._started = False
        self._closed = threading.Event()
        self._recv_queue: "queue.Queue[bytes]" = queue.Queue()
        self._send_queue: "queue.Queue[bytes]" = queue.Queue()
        self._tun = _QueueTun(
            inbound=self._recv_queue,
            outbound=self._send_queue,
            closed_event=self._closed,
            name=tun_name,
        )
        self._cleanup: Callable[[], None] | None = None

    @property
    def tun(self) -> _QueueTun:
        return self._tun

    def attach_runner(self, runner: VpnRunner) -> None:
        self._runner = runner

    def attach_cleanup(self, cleanup: Callable[[], None]) -> None:
        self._cleanup = cleanup

    def start(self) -> None:
        if self._started:
            return
        if self._runner is None:
            raise RuntimeError("vpn tunnel bridge has no runner attached")
        self._runner.start()
        self._started = True

    def send(self, data: bytes) -> int:
        if self._closed.is_set():
            raise RuntimeError("vpn tunnel bridge is closed")
        runner = self._runner
        if runner is None or runner.tunnel is None:
            raise RuntimeError("vpn tunnel bridge is not started")
        runner.tunnel.send_packet(bytes(data))
        return len(data)

    def recv(self, timeout: float | None = None) -> bytes | None:
        if self._closed.is_set() and self._recv_queue.empty():
            return None
        try:
            if timeout is None:
                return self._recv_queue.get()
            return self._recv_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        runner = self._runner
        self._runner = runner
        try:
            if runner is not None:
                runner.stop()
        finally:
            try:
                self._tun.close()
            finally:
                self._started = False
                if self._cleanup is not None:
                    try:
                        self._cleanup()
                    except Exception:  # noqa: BLE001
                        pass

    @property
    def closed(self) -> bool:
        return self._closed.is_set()
