"""
Receiver: capture → GGWave decode → frame decode → reassemble → emit.

The audio source is pluggable (AudioSource protocol). Default is
sounddevice; LiveKit injects its own source, tests use MemorySource.
A single worker thread walks source.iter_blocks() and feeds the codec.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Iterator

from userbot_bale.audio_backend import AudioSource
from userbot_bale.codec import Codec, Protocol
from userbot_bale.framing import Frame, Reassembler

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    msg_id: int
    data: bytes

    def text(self, errors: str = "replace") -> str:
        return self.data.decode("utf-8", errors=errors)


class Receiver:
    """
    Start/stop the capture pipeline. Two consumption patterns:

        rx = Receiver()
        with rx:
            for msg in rx.iter_messages():
                print(msg.text())

    Or callback form:

        rx = Receiver(on_message=lambda m: print(m.text()))
        with rx:
            rx.wait_forever()

    Construct with either `source=...` (any AudioSource) or `device=...`
    (CLI convenience; resolved to SoundDeviceSource).
    """

    def __init__(
        self,
        device: str | int | None = None,
        *,
        source: AudioSource | None = None,
        protocol: Protocol = Protocol.AUDIBLE_FAST,
        on_message: Callable[[Message], None] | None = None,
        on_raw_packet: Callable[[bytes], None] | None = None,
    ) -> None:
        if source is not None and device is not None:
            raise ValueError("pass either source= or device=, not both")
        if source is None:
            from userbot_bale.backends.sounddevice_backend import SoundDeviceSource
            source = SoundDeviceSource(device=device)
        self._source = source
        self._codec = Codec(protocol=protocol)
        self._reassembler = Reassembler()
        self._msg_q: "queue.Queue[Message]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_message = on_message
        self._on_raw_packet = on_raw_packet

    def __enter__(self) -> "Receiver":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("Receiver already started")
        self._codec.__enter__()
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run_worker, name="userbot-bale-rx", daemon=True
        )
        self._worker.start()
        log.info("receiver started")

    def stop(self) -> None:
        self._stop.set()
        try:
            self._source.close()
        except Exception:  # noqa: BLE001
            log.exception("source.close failed")
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._codec.__exit__(None, None, None)
        log.info("receiver stopped")

    def _run_worker(self) -> None:
        for chunk in self._source.iter_blocks():
            if self._stop.is_set():
                return
            try:
                payload = self._codec.decode_chunk(chunk)
            except Exception:  # noqa: BLE001
                log.exception("decode_chunk failed")
                continue
            if payload is None:
                continue
            if self._on_raw_packet is not None:
                try:
                    self._on_raw_packet(payload)
                except Exception:  # noqa: BLE001
                    log.exception("on_raw_packet callback failed")
            frame = Frame.decode(payload)
            if frame is None:
                log.debug("dropped non-userbot-bale packet (%d bytes)", len(payload))
                continue
            assembled = self._reassembler.push(frame)
            if assembled is None:
                continue
            msg = Message(msg_id=frame.msg_id, data=assembled)
            self._msg_q.put(msg)
            if self._on_message is not None:
                try:
                    self._on_message(msg)
                except Exception:  # noqa: BLE001
                    log.exception("on_message callback failed")

    def get_message(self, timeout: float | None = None) -> Message | None:
        """Pull a single completed message, or None on timeout. Useful for
        transports that need a bounded-wait recv call (e.g. VPN audio)."""
        try:
            return self._msg_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def iter_messages(self, timeout: float | None = None) -> Iterator[Message]:
        """Yield messages as they complete. Returns when stop() is called and the queue drains."""
        while True:
            try:
                msg = self._msg_q.get(timeout=timeout if timeout is not None else 0.25)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            yield msg
            if self._stop.is_set() and self._msg_q.empty():
                return

    def wait_forever(self) -> None:
        try:
            self._stop.wait()
        except KeyboardInterrupt:
            pass
