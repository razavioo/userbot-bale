"""
Transmitter: fragment → encode → play.

The transmitter is a single-threaded pipeline because GGWave packets
are short (typically <1 s) and the chat-streaming use case does not
benefit from overlapping encode and playback — the call apps can't
decode two simultaneous streams anyway.

Playback uses sounddevice (PortAudio) targeting the PipeWire Pulse
device chosen by name. A short silence is prepended/appended so that
voice-activity detection doesn't clip the preamble.
"""

from __future__ import annotations

import itertools
import logging
from typing import Iterable, Iterator

import numpy as np
import sounddevice as sd

from baleobala.codec import SAMPLE_RATE, Codec, Protocol
from baleobala.framing import fragment

log = logging.getLogger(__name__)

LEAD_SILENCE_MS = 80
TAIL_SILENCE_MS = 40
INTER_FRAME_GAP_MS = 60


def _silence(ms: int) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * ms / 1000), dtype=np.float32)


class Transmitter:
    """
    Wrap a Codec + a specific output device. `send` blocks until the
    entire logical message has been played. `send_stream` walks an
    iterable and yields after each successfully-played message so the
    caller can observe progress.
    """

    def __init__(
        self,
        device: str | int | None = None,
        protocol: Protocol = Protocol.AUDIBLE_FAST,
        volume: int = 50,
        start_msg_id: int = 0,
    ) -> None:
        self.device = device
        self._codec = Codec(protocol=protocol, volume=volume)
        self._msg_counter = itertools.count(start_msg_id & 0xFFFF)

    def __enter__(self) -> "Transmitter":
        self._codec.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._codec.__exit__(None, None, None)

    def send(self, message: str | bytes) -> int:
        """Send one logical message. Returns its msg_id."""
        data = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        msg_id = next(self._msg_counter) & 0xFFFF
        frames = list(fragment(data, msg_id))
        segments: list[np.ndarray] = [_silence(LEAD_SILENCE_MS)]
        gap = _silence(INTER_FRAME_GAP_MS)
        for i, frame in enumerate(frames):
            waveform = self._codec.encode(frame.encode())
            segments.append(waveform)
            if i != len(frames) - 1:
                segments.append(gap)
        segments.append(_silence(TAIL_SILENCE_MS))
        audio = np.concatenate(segments)
        log.debug(
            "msg_id=%d frames=%d bytes=%d duration=%.2fs",
            msg_id, len(frames), len(data), len(audio) / SAMPLE_RATE,
        )
        sd.play(audio, samplerate=SAMPLE_RATE, device=self.device, blocking=True)
        return msg_id

    def send_stream(self, messages: Iterable[str | bytes]) -> Iterator[int]:
        for m in messages:
            yield self.send(m)
