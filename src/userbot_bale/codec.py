"""
Thin, typed wrapper around the ggwave native library.

Why a wrapper?
  * The ggwave Python bindings have a C-style lifecycle (init / free) that
    leaks if the caller forgets to release the instance. We expose it as a
    context manager.
  * The encode/decode signatures take/return bytes of specific dtypes;
    we centralize the numpy conversions so callers only see np.float32.
  * Protocol IDs and sample rates are magic constants upstream; we name
    them and pick the only combinations that are known to survive Opus.

The five built-in protocols:
     0 AUDIBLE_NORMAL     ~ 8 B/s  most robust
     1 AUDIBLE_FAST       ~16 B/s  recommended default for VoIP
     2 AUDIBLE_FASTEST    ~32 B/s  marginal through aggressive NS
     3 ULTRASOUND_NORMAL             — killed by Opus voice mode
     4 ULTRASOUND_FAST              — killed by Opus voice mode
     5 ULTRASOUND_FASTEST           — killed by Opus voice mode

Only the audible protocols are exposed here.
"""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from enum import IntEnum
from types import TracebackType
from typing import Optional

import numpy as np

try:
    import ggwave  # type: ignore
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "ggwave is not installed. Install with: pip install ggwave"
    ) from e

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000  # ggwave default; matches PipeWire default graph rate


class Protocol(IntEnum):
    AUDIBLE_NORMAL = 0
    AUDIBLE_FAST = 1
    AUDIBLE_FASTEST = 2


class Codec(AbstractContextManager["Codec"]):
    """
    Stateful codec. Encoding is stateless (each call self-contained).
    Decoding is streaming: feed audio chunks via `decode_chunk` and
    it returns a payload when a full packet has been reconstructed.
    """

    def __init__(self, protocol: Protocol = Protocol.AUDIBLE_FAST, volume: int = 50) -> None:
        if not (0 <= volume <= 100):
            raise ValueError("volume must be in [0, 100]")
        self.protocol = Protocol(protocol)
        self.volume = volume
        self._instance: Optional[int] = None

    def __enter__(self) -> "Codec":
        self._instance = ggwave.init()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._instance is not None:
            ggwave.free(self._instance)
            self._instance = None

    def encode(self, payload: bytes) -> np.ndarray:
        """Encode a single GGWave packet. Returns float32 waveform at 48 kHz."""
        if not payload:
            raise ValueError("payload must be non-empty")
        if len(payload) > 140:
            raise ValueError("ggwave packet payload must be <= 140 bytes")
        waveform_bytes = ggwave.encode(
            payload,
            protocolId=int(self.protocol),
            volume=self.volume,
        )
        audio = np.frombuffer(waveform_bytes, dtype=np.float32)
        return audio

    def decode_chunk(self, chunk: np.ndarray) -> bytes | None:
        """
        Feed a float32 mono audio chunk at 48 kHz.
        Returns the decoded payload bytes when a packet completes, else None.
        """
        if self._instance is None:
            raise RuntimeError("Codec must be used as a context manager")
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32, copy=False)
        if chunk.ndim > 1:
            chunk = chunk[:, 0]
        result = ggwave.decode(self._instance, chunk.tobytes())
        if result is None:
            return None
        return bytes(result)
