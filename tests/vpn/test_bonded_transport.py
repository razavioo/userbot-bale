from __future__ import annotations

import queue
import threading
import time

import pytest

from baleobala.vpn.transports.bonded import BondedTransport


class _FakeChannel:
    """Fake DataChannel.

    send_bytes() appends to `sent_data` (for round-robin inspection) and
    also puts data into `_rx` so the BondedTransport reader thread can pick
    it up via recv_bytes() and forward it to the merged queue.

    For tests that inject data FROM the remote side (merge tests), call
    `inject(data)` directly — that puts data in `_rx` without recording
    it in `sent_data`.
    """

    mtu = 1400
    rate_hint = 100.0

    def __init__(self) -> None:
        self.closed = False
        self._rx: "queue.Queue[bytes]" = queue.Queue()
        self.sent_data: list[bytes] = []

    def inject(self, data: bytes) -> None:
        """Simulate remote-side data arriving on this channel."""
        self._rx.put(bytes(data))

    def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise RuntimeError("channel closed")
        self.sent_data.append(bytes(data))
        # Also put in _rx so the BondedTransport reader thread sees it and
        # forwards it to the bonded recv_queue (needed for merge tests).
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


class _StallChannel(_FakeChannel):
    """Channel that blocks send_bytes until explicitly unblocked."""

    def __init__(self) -> None:
        super().__init__()
        self._stall = threading.Event()

    def stall(self) -> None:
        self._stall.clear()

    def unstall(self) -> None:
        self._stall.set()

    def send_bytes(self, data: bytes) -> None:
        self._stall.wait()
        super().send_bytes(data)


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


def test_bonded_transport_round_robins_across_live_channels() -> None:
    """send_bytes distributes consecutive writes across all live channels.

    BondedTransport reader threads drain each channel's recv queue so we
    inspect `channel.sent_data` (set in send_bytes before enqueue) rather
    than calling recv_bytes() on the individual channels.
    """
    a, b, c = _FakeChannel(), _FakeChannel(), _FakeChannel()
    transport = BondedTransport([a, b, c])
    try:
        for i in range(6):
            transport.send_bytes(bytes([i]))
        # Each channel must have received exactly 2 out of 6 sends.
        assert len(a.sent_data) == 2, f"channel A got {len(a.sent_data)}/2 sends"
        assert len(b.sent_data) == 2, f"channel B got {len(b.sent_data)}/2 sends"
        assert len(c.sent_data) == 2, f"channel C got {len(c.sent_data)}/2 sends"
    finally:
        transport.close()


def test_bonded_transport_recv_merges_from_all_channels() -> None:
    """recv_bytes returns data arriving on any channel.

    Uses inject() to push data as if it arrived from the remote side,
    bypassing send_bytes / sent_data tracking. The reader threads pick
    it up and forward it to the bonded recv_queue.
    """
    a, b = _FakeChannel(), _FakeChannel()
    transport = BondedTransport([a, b])
    try:
        a.inject(b"from-a")
        b.inject(b"from-b")
        received = {transport.recv_bytes(timeout=0.5), transport.recv_bytes(timeout=0.5)}
        assert received == {b"from-a", b"from-b"}
    finally:
        transport.close()


def test_bonded_transport_continues_after_one_dc_closes_mid_session() -> None:
    """If one DataChannel closes during a session, remaining channels stay
    live and carry traffic without raising.

    Regression for D3: the bonded transport must not declare itself closed
    or drop the active session when only a subset of channels fail.

    Round-robin with 2 channels: first send goes to A (idx=0), then A is
    closed, next 4 sends all go to B. We verify B's sent_data has those 4.
    """
    a, b = _FakeChannel(), _FakeChannel()
    transport = BondedTransport([a, b])
    try:
        # First send → channel A (idx 0).
        transport.send_bytes(b"before-fail")
        assert len(a.sent_data) == 1

        # Kill channel A mid-session (simulate DC close / network drop).
        a.closed = True

        # Subsequent sends must all go to B without raising.
        for _ in range(4):
            transport.send_bytes(b"after-fail")

        # The transport is NOT globally closed — one channel is still up.
        assert not transport.closed
        assert len(b.sent_data) == 4, (
            f"expected 4 sends on channel B, got {len(b.sent_data)}: {b.sent_data}"
        )
    finally:
        transport.close()


def test_bonded_transport_mtu_and_rate_aggregation() -> None:
    """mtu is the minimum across channels; rate_hint is the sum."""
    fast = _FakeChannel()
    fast.rate_hint = 200_000.0
    fast.mtu = 1200
    slow = _FakeChannel()
    slow.rate_hint = 50_000.0
    slow.mtu = 800
    transport = BondedTransport([fast, slow])
    try:
        assert transport.mtu == 800
        assert transport.rate_hint == pytest.approx(250_000.0)
    finally:
        transport.close()
