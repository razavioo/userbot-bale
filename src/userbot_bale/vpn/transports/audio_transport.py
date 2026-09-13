"""
Audio transport: VPN frames encoded as GGWave packets over the Bale
audio track. This is the slowest path (~16 B/s at AUDIBLE_FAST through
Opus) and exists so the VPN still has a last-resort carrier when the
peer's network blocks WebRTC data channels — the voice channel itself
is what Bale exists to deliver.

Design choices:
    * MTU = 128 bytes. The GGWave framing layer caps one on-air packet
      at 132 bytes; we stay a hair below so any future header bumps
      don't break us. IP packets are split by the tunnel core.
    * send_bytes() blocks until playback finishes (Transmitter.send is
      already synchronous). With the tunnel's window=1 on this path,
      this yields natural stop-and-wait pacing.
    * recv_bytes() pulls from Receiver.iter_messages(timeout); each
      message is one GGWave-delivered VPN frame.
"""

from __future__ import annotations

import logging
from typing import Optional

from userbot_bale.bale.livekit_backend import LiveKitSession
from userbot_bale.codec import Protocol
from userbot_bale.receiver import Receiver
from userbot_bale.transmitter import Transmitter

log = logging.getLogger(__name__)


class AudioTransport:
    MTU = 128
    RATE_HINT = 16.0  # bytes/sec at AUDIBLE_FAST through Opus

    def __init__(
        self,
        session: LiveKitSession,
        *,
        protocol: Protocol = Protocol.AUDIBLE_FAST,
        volume: int = 50,
    ) -> None:
        self.mtu = self.MTU
        self.rate_hint = self.RATE_HINT
        self._session = session
        self._tx = Transmitter(
            sink=session.sink(), protocol=protocol, volume=volume
        )
        self._tx.__enter__()
        self._rx = Receiver(source=session.source(), protocol=protocol)
        self._rx.__enter__()
        self._closed = False

    def send_bytes(self, data: bytes) -> None:
        if self._closed:
            return
        if len(data) > self.MTU:
            raise ValueError(f"frame {len(data)} > audio MTU {self.MTU}")
        # Transmitter.send takes bytes directly; it runs the framing
        # layer internally. Blocks until playback finishes.
        self._tx.send(data)

    def recv_bytes(self, timeout: float | None = None) -> Optional[bytes]:
        if self._closed:
            return None
        # iter_messages yields one at a time; pull exactly one (or None on timeout).
        msg = self._rx.get_message(timeout=timeout)
        return msg.data if msg is not None else None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._rx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.exception("receiver close failed")
        try:
            self._tx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.exception("transmitter close failed")
