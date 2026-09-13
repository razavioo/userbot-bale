"""
Audio backend protocol.

Transmitter plays float32 waveforms into *some* sink; Receiver pulls float32
chunks from *some* source. Historically both were hard-coded to sounddevice.
Phase 3 (Bale headless client) needs a LiveKit backend, and tests benefit
from an in-memory backend. This module defines the minimal protocol the
two high-level classes consume.

Conventions
-----------
- Samples are float32 mono at SAMPLE_RATE (48 kHz); the codec produces and
  consumes this format, so backends that speak other formats resample
  internally.
- Sinks block until playback completes (so `Transmitter.send` stays
  synchronous for the CLI). LiveKit's sink is "complete" when the last
  frame has been handed to the track — not when the peer has rendered it.
- Sources yield blocks of BLOCK_SIZE samples (same size the Receiver's
  sounddevice callback used historically). iter_blocks() is a generator
  so a stop() call unblocks the consumer cleanly.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AudioSink(Protocol):
    """Sink of float32 mono samples at 48 kHz."""

    def play(self, waveform: np.ndarray) -> None:
        """Play a float32 mono buffer. Blocks until the buffer is submitted."""

    def close(self) -> None:
        """Release the sink. Idempotent."""


@runtime_checkable
class AudioSource(Protocol):
    """Source of float32 mono samples at 48 kHz."""

    def iter_blocks(self) -> Iterator[np.ndarray]:
        """Yield float32 mono blocks until close() is called."""

    def close(self) -> None:
        """Stop yielding and release the source. Idempotent."""
