"""
sounddevice-backed AudioSink and AudioSource.

This is the historical path: playback via `sd.play`, capture via
`sd.InputStream` with a callback that pushes into a thread-safe queue.
The classes here wrap that and satisfy the AudioSink/AudioSource protocol
so Transmitter/Receiver do not import sounddevice directly.
"""

from __future__ import annotations

import logging
import queue
from typing import Iterator

import numpy as np
import sounddevice as sd

from userbot_bale.codec import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK_SIZE = 1024


class SoundDeviceSink:
    def __init__(self, device: str | int | None = None) -> None:
        self.device = device

    def play(self, waveform: np.ndarray) -> None:
        sd.play(waveform, samplerate=SAMPLE_RATE, device=self.device, blocking=True)

    def close(self) -> None:
        sd.stop()


class SoundDeviceSource:
    def __init__(
        self,
        device: str | int | None = None,
        block_size: int = BLOCK_SIZE,
    ) -> None:
        self.device = device
        self._block_size = block_size
        self._q: "queue.Queue[np.ndarray | None]" = queue.Queue(maxsize=64)
        self._stream: sd.InputStream | None = None
        self._closed = False

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            log.debug("input stream status: %s", status)
        try:
            self._q.put_nowait(indata.copy())
        except queue.Full:
            log.warning("audio queue full; dropping block")

    def _ensure_stream(self) -> None:
        if self._stream is None:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self.device,
                blocksize=self._block_size,
                callback=self._callback,
            )
            self._stream.start()

    def iter_blocks(self) -> Iterator[np.ndarray]:
        self._ensure_stream()
        while not self._closed:
            chunk = self._q.get()
            if chunk is None:
                return
            yield chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._q.put(None)
