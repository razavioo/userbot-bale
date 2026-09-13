"""ByteChannel backed by an audio sink/source pair."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from userbot_bale.audio_backend import AudioSink, AudioSource
from userbot_bale.runtime.interfaces import ByteChannel


@dataclass
class AudioByteChannel:
    """Bridge raw bytes across any audio transport.

    Outbound bytes are sent with `Transmitter`; inbound bytes are
    collected with `Receiver` and queued for the byte-stream tunnel.
    """

    sink: AudioSink
    source: AudioSource
    protocol: str = "fast"
    volume: int = 50

    def __post_init__(self) -> None:
        self._rx_q: "queue.Queue[bytes | None]" = queue.Queue()
        self._rx_thread: threading.Thread | None = None
        self._rx = None
        self._tx = None
        self._closed = False

    def start(self) -> None:
        if self._tx is not None:
            return
        from userbot_bale.transmitter import Transmitter
        from userbot_bale.receiver import Receiver
        from userbot_bale.codec import Protocol as GgProtocol

        proto = {
            "normal": GgProtocol.AUDIBLE_NORMAL,
            "fast": GgProtocol.AUDIBLE_FAST,
            "fastest": GgProtocol.AUDIBLE_FASTEST,
        }[self.protocol]
        self._tx = Transmitter(sink=self.sink, protocol=proto, volume=self.volume)
        self._rx = Receiver(
            source=self.source,
            protocol=proto,
            on_message=lambda msg: self._rx_q.put(msg.data),
        )
        self._tx.__enter__()
        self._rx.start()

    def send(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("channel closed")
        if self._tx is None:
            self.start()
        assert self._tx is not None
        self._tx.send(bytes(data))

    def recv(self, timeout: float | None = None) -> bytes | None:
        if self._closed:
            return None
        try:
            return self._rx_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._rx is not None:
            self._rx.stop()
            self._rx = None
        if self._tx is not None:
            self._tx.__exit__(None, None, None)
            self._tx = None
        self._rx_q.put(None)

