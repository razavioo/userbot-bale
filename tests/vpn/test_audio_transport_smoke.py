"""
Smoke test for AudioTransport: constructs it against a stub session
exposing MemorySink/MemorySource, verifies lifecycle + MTU + rate_hint,
and sends one frame into the sink (the full round-trip lives in
test_loopback.py at the codec level).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("ggwave")

from baleobala.backends.memory import MemorySink, MemorySource
from baleobala.vpn.transports.audio_transport import AudioTransport


class _StubSession:
    def __init__(self) -> None:
        self._sink = MemorySink()
        # Empty source: iter_blocks() immediately finishes. Receiver stays
        # waiting until stopped, which is what the smoke test wants.
        self._source = MemorySource(np.zeros(0, dtype=np.float32))

    def sink(self):
        return self._sink

    def source(self):
        return self._source

    @property
    def captured(self) -> np.ndarray:
        return self._sink.concatenated()


def test_audio_transport_lifecycle_and_send():
    sess = _StubSession()
    t = AudioTransport(sess)  # type: ignore[arg-type]
    try:
        assert t.mtu == 128
        assert t.rate_hint > 0
        t.send_bytes(b"\x45\x00\x00\x14vpn-frame!")
        assert len(sess.captured) > 0  # produced audio samples
    finally:
        t.close()


def test_audio_transport_rejects_oversize():
    sess = _StubSession()
    t = AudioTransport(sess)  # type: ignore[arg-type]
    try:
        with pytest.raises(ValueError):
            t.send_bytes(b"x" * (t.mtu + 1))
    finally:
        t.close()
