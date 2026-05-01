from __future__ import annotations

import queue

import pytest

from baleobala.vpn.transports.bonded import BondedTransport


class _FakeChannel:
    mtu = 1400
    rate_hint = 100.0

    def __init__(self) -> None:
        self.closed = False
        self._rx: "queue.Queue[bytes]" = queue.Queue()

    def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise RuntimeError("channel closed")
        self._rx.put(bytes(data))

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        if self.closed:
            return None
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True


def test_bonded_transport_reports_closed_when_all_channels_are_terminal() -> None:
    channels = [_FakeChannel(), _FakeChannel()]
    transport = BondedTransport(channels)
    try:
        assert not transport.closed
        channels[0].closed = True
        assert not transport.closed
        channels[1].closed = True
        assert transport.closed
        assert transport.recv_bytes(timeout=0.01) is None
        with pytest.raises(RuntimeError, match="bonded transport closed"):
            transport.send_bytes(b"hello")
    finally:
        transport.close()


def test_bonded_transport_skips_closed_channels_on_send() -> None:
    closed = _FakeChannel()
    live = _FakeChannel()
    closed.closed = True
    transport = BondedTransport([closed, live])
    try:
        transport.send_bytes(b"hello")
        assert live.recv_bytes(timeout=0.5) == b"hello"
    finally:
        transport.close()
