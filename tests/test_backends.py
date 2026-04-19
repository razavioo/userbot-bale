"""Round-trip Transmitter → Receiver using the in-memory backends."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("ggwave")

from baleobala.backends.memory import MemorySink, MemorySource
from baleobala.codec import Protocol
from baleobala.receiver import Receiver
from baleobala.transmitter import Transmitter


def test_memory_sink_accepts_waveforms() -> None:
    sink = MemorySink()
    with Transmitter(sink=sink, protocol=Protocol.AUDIBLE_FAST) as tx:
        tx.send("short")
        tx.send("a slightly longer one")
    assert len(sink.buffers) == 2
    assert all(buf.ndim == 1 for buf in sink.buffers)
    assert all(buf.dtype.name == "float32" for buf in sink.buffers)
    assert sink.concatenated().size > 0


def test_memory_roundtrip_single_message() -> None:
    sink = MemorySink()
    with Transmitter(
        sink=sink, protocol=Protocol.AUDIBLE_FAST, start_msg_id=0,
    ) as tx:
        tx.send("hello bale")

    source = MemorySource(sink.concatenated(), block_size=1024)
    got: list[str] = []
    rx = Receiver(source=source, protocol=Protocol.AUDIBLE_FAST,
                  on_message=lambda m: got.append(m.text()))
    rx.start()
    # MemorySource exits iter_blocks when exhausted; give the worker a moment.
    deadline = time.time() + 4
    while time.time() < deadline and not got:
        time.sleep(0.05)
    rx.stop()
    assert got == ["hello bale"]


def test_transmitter_rejects_conflicting_constructors() -> None:
    try:
        Transmitter(device="foo", sink=MemorySink())
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_receiver_rejects_conflicting_constructors() -> None:
    try:
        Receiver(device="foo", source=MemorySource(
            __import__("numpy").zeros(0, dtype="float32")))
    except ValueError:
        return
    raise AssertionError("expected ValueError")
