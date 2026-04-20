"""
LiveKit keepalive: publish a tiny payload on a dedicated DataChannel
topic every `interval` seconds so the SFU doesn't reap an idle call.

Bale's SFU has an empirical idle timeout around 30 minutes — traffic
on the audio track is usually enough to keep a call alive, but a
DataChannel-only tunnel with no IP traffic for a while will go silent.
The keepalive payload is one byte on a topic the tunnel ignores.

Usage:
    ka = LiveKitKeepalive(session, interval=20.0)
    ka.start()
    ...
    ka.stop()
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

KEEPALIVE_TOPIC = "bb-ka"
KEEPALIVE_PAYLOAD = b"\x00"


class LiveKitKeepalive:
    def __init__(self, session, *, interval: float = 20.0) -> None:  # type: ignore[no-untyped-def]
        self._session = session
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="vpn-keepalive", daemon=True
        )
        self._thread.start()
        log.info("keepalive started (interval=%.1fs)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._session._submit_data(
                    KEEPALIVE_PAYLOAD,
                    topic=KEEPALIVE_TOPIC,
                    reliable=False,
                )
            except Exception:  # noqa: BLE001
                log.exception("keepalive publish failed")
