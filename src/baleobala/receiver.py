"""
Receiver: capture → GGWave decode → frame decode → reassemble → emit.

Architecture:
  * sounddevice InputStream with a callback pushes int16/float32 blocks
    into a thread-safe queue. Callback stays short; no allocations in it
    beyond the queue put.
  * A worker thread pulls blocks, feeds them into the Codec. When a
    packet completes, it runs Frame.decode and Reassembler.push, and
    invokes the user callback for each completed logical message.

Callbacks are invoked on the worker thread — keep them fast or offload
to the caller's own queue. `iter_messages()` offers a synchronous
pull-style API that avoids the threading question entirely.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Iterator

import numpy as np
import sounddevice as sd

from baleobala.codec import SAMPLE_RATE, Codec, Protocol
from baleobala.framing import Frame, Reassembler

log = logging.getLogger(__name__)

BLOCK_SIZE = 1024


@dataclass(frozen=True)
class Message:
    msg_id: int
    data: bytes

    def text(self, errors: str = "replace") -> str:
        return self.data.decode("utf-8", errors=errors)


class Receiver:
    """
    Start/stop the capture pipeline. Two ways to consume results:

        rx = Receiver(); rx.start()
        for msg in rx.iter_messages():
            print(msg.text())

    or with a callback:

        rx = Receiver(on_message=lambda m: print(m.text()))
        with rx:
            rx.wait_forever()
    """

    def __init__(
        self,
        device: str | int | None = None,
        protocol: Protocol = Protocol.AUDIBLE_FAST,
        on_message: Callable[[Message], None] | None = None,
        on_raw_packet: Callable[[bytes], None] | None = None,
    ) -> None:
        self.device = device
        self._codec = Codec(protocol=protocol)
        self._reassembler = Reassembler()
        self._audio_q: "queue.Queue[np.ndarray | None]" = queue.Queue(maxsize=64)
        self._msg_q: "queue.Queue[Message]" = queue.Queue()
        self._stream: sd.InputStream | None = None
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
            target=self._run_worker, name="baleobala-rx", daemon=True
        )
        self._worker.start()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self.device,
            blocksize=BLOCK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()
        log.info("receiver started on device=%r", self.device)

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._audio_q.put(None)  # sentinel
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._codec.__exit__(None, None, None)
        log.info("receiver stopped")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            log.debug("input stream status: %s", status)
        try:
            self._audio_q.put_nowait(indata.copy())
        except queue.Full:
            log.warning("audio queue full; dropping block")

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            chunk = self._audio_q.get()
            if chunk is None:
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
                log.debug("dropped non-baleobala packet (%d bytes)", len(payload))
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

    def iter_messages(self, timeout: float | None = None) -> Iterator[Message]:
        """Yield messages as they complete. Blocks until stop() is called."""
        while True:
            try:
                msg = self._msg_q.get(timeout=timeout)
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
