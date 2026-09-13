"""
In-memory AudioSink / AudioSource for testing.

MemorySink collects every played waveform; MemorySource yields pre-canned
blocks. The pair lets us round-trip Transmitter → Receiver without a real
audio device and is used by tests/test_backends.py.
"""

from __future__ import annotations

from typing import Iterator, List

import numpy as np


class MemorySink:
    def __init__(self) -> None:
        self.buffers: List[np.ndarray] = []
        self._closed = False

    def play(self, waveform: np.ndarray) -> None:
        if self._closed:
            raise RuntimeError("sink closed")
        self.buffers.append(waveform.astype(np.float32, copy=True))

    def close(self) -> None:
        self._closed = True

    def concatenated(self) -> np.ndarray:
        if not self.buffers:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.buffers)


class MemorySource:
    """Yield pre-loaded audio as fixed-size blocks."""

    def __init__(self, waveform: np.ndarray, block_size: int = 1024) -> None:
        self._waveform = waveform.astype(np.float32, copy=False)
        self._block_size = block_size
        self._closed = False

    def iter_blocks(self) -> Iterator[np.ndarray]:
        n = len(self._waveform)
        for start in range(0, n, self._block_size):
            if self._closed:
                return
            yield self._waveform[start : start + self._block_size]

    def close(self) -> None:
        self._closed = True
