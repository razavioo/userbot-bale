"""RPC dispatcher for plain MTProto-style framed streams."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from itertools import count
from typing import Any, Callable

from baleobala.bale.mtproto.framing import read_frame, write_frame
from baleobala.bale.mtproto.session import PlainSessionCodec, SessionCodec

log = logging.getLogger(__name__)


def _request_bytes(request: object) -> bytes:
    if isinstance(request, bytes):
        return request
    encode = getattr(request, "encode", None)
    if callable(encode):
        return encode()
    raise TypeError("request must be bytes or expose encode() -> bytes")


class MtpRpcClient:
    def __init__(self, conn, *, codec: SessionCodec | None = None) -> None:  # type: ignore[no-untyped-def]
        self._conn = conn
        self._codec = codec or PlainSessionCodec()
        self._subscribers: list[Callable[[Any], None]] = []
        self._pending: dict[int, "queue.Queue[object]"] = {}
        self._pending_lock = threading.Lock()
        self._seq = count(1)
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._terminal_error: BaseException | None = None

    def start(self) -> None:
        if self._reader is not None:
            return
        self._reader = threading.Thread(
            target=self._run_reader,
            name="baleobala-mtproto-rpc",
            daemon=True,
        )
        self._reader.start()
        self._started.set()

    def close(self) -> None:
        self._stop.set()
        close = getattr(self._conn, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        if self._reader is not None:
            self._reader.join(timeout=1.0)

    def call(self, request, *, timeout: float = 10.0) -> bytes:  # noqa: ANN001
        self.start()
        if self._terminal_error is not None:
            raise RuntimeError(f"MTProto RPC reader stopped: {self._terminal_error}") from self._terminal_error
        seq = next(self._seq)
        q: "queue.Queue[object]" = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[seq] = q
        try:
            frame = self._codec.encode_request(seq, _request_bytes(request))
            write_frame(self._conn, frame)
            result = q.get(timeout=timeout)
        except queue.Empty as e:
            raise TimeoutError(f"MTProto RPC timed out waiting for seq={seq}") from e
        finally:
            with self._pending_lock:
                self._pending.pop(seq, None)
        if isinstance(result, BaseException):
            raise RuntimeError(f"MTProto RPC failed for seq={seq}: {result}") from result
        return result  # type: ignore[return-value]

    async def rpc(self, request, *, timeout: float = 10.0) -> bytes:  # noqa: ANN001
        return await asyncio.to_thread(self.call, request, timeout=timeout)

    def subscribe(self, callback: Callable[[Any], None]) -> None:
        """Register a callback for server-initiated updates."""
        self._subscribers.append(callback)

    def _run_reader(self) -> None:
        try:
            while not self._stop.is_set():
                frame = read_frame(self._conn)
                inbound = self._codec.classify(frame)
                if inbound.kind == "response":
                    self._deliver_response(inbound.seq, inbound.body)
                else:
                    self._deliver_update(inbound.body)
        except Exception as e:  # noqa: BLE001
            self._terminal_error = e
            self._fail_pending(e)
            if not self._stop.is_set():
                log.debug("mtproto rpc reader stopped", exc_info=True)

    def _deliver_response(self, seq: int, body: bytes) -> None:
        with self._pending_lock:
            q = self._pending.get(seq)
        if q is None:
            self._deliver_update(body)
            return
        q.put(body)

    def _deliver_update(self, body: bytes) -> None:
        for callback in list(self._subscribers):
            try:
                callback(body)
            except Exception:  # noqa: BLE001
                log.exception("mtproto update callback failed")

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for q in pending:
            try:
                q.put_nowait(error)
            except queue.Full:
                pass
