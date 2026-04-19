"""Byte-stream tunnel session with seq/ack framing."""

from __future__ import annotations

import itertools
import secrets
from dataclasses import dataclass
from typing import Iterator

from baleobala.runtime.frame import (
    MAX_PAYLOAD,
    TunnelFrame,
    TunnelFrameType,
    TunnelRole,
    decode_tunnel_frame,
    encode_tunnel_frame,
)
from baleobala.runtime.interfaces import ByteChannel, SecurityProvider
from baleobala.runtime.security import NullSecurityProvider


@dataclass(frozen=True)
class TunnelConfig:
    max_payload: int = MAX_PAYLOAD
    heartbeat_seconds: float = 5.0
    retransmit_seconds: float = 2.0


@dataclass(frozen=True)
class TunnelStats:
    tx_frames: int = 0
    rx_frames: int = 0
    tx_bytes: int = 0
    rx_bytes: int = 0
    acked_seq: int = 0


class TunnelSession:
    """Minimal full-duplex byte tunnel over a generic channel."""

    def __init__(
        self,
        channel: ByteChannel,
        *,
        role: TunnelRole = TunnelRole.CLIENT,
        security: SecurityProvider | None = None,
        config: TunnelConfig | None = None,
    ) -> None:
        self._channel = channel
        self._role = role
        self._security = security or NullSecurityProvider(session_id=secrets.token_hex(8))
        self._config = config or TunnelConfig()
        self._seq = itertools.count(1)
        self._remote_ack = 0
        self._open = False
        self._closed = False
        self._stats = TunnelStats()

    def open(self) -> None:
        if self._open:
            return
        self._channel.send(
            encode_tunnel_frame(
                TunnelFrame(
                    frame_type=TunnelFrameType.OPEN,
                    seq=0,
                    payload=self._security.start_handshake(self._role.name.lower()),
                )
            )
        )
        self._open = True

    def send(self, data: bytes) -> int:
        if self._closed:
            raise RuntimeError("tunnel closed")
        if not self._open:
            self.open()
        seq = next(self._seq)
        payload = self._security.seal(bytes(data))
        if len(payload) > self._config.max_payload:
            raise ValueError(f"payload too large for one frame: {len(payload)}")
        frame = TunnelFrame(
            frame_type=TunnelFrameType.DATA,
            seq=seq,
            ack=self._remote_ack,
            payload=payload,
        )
        self._channel.send(encode_tunnel_frame(frame))
        self._stats = TunnelStats(
            tx_frames=self._stats.tx_frames + 1,
            rx_frames=self._stats.rx_frames,
            tx_bytes=self._stats.tx_bytes + len(payload),
            rx_bytes=self._stats.rx_bytes,
            acked_seq=self._stats.acked_seq,
        )
        return seq

    def recv(self, timeout: float | None = None) -> bytes | None:
        if self._closed:
            return None
        while True:
            raw = self._channel.recv(timeout=timeout)
            if raw is None:
                return None
            frame = decode_tunnel_frame(raw)
            if frame is None:
                continue
            self._stats = TunnelStats(
                tx_frames=self._stats.tx_frames,
                rx_frames=self._stats.rx_frames + 1,
                tx_bytes=self._stats.tx_bytes,
                rx_bytes=self._stats.rx_bytes + len(frame.payload),
                acked_seq=max(self._stats.acked_seq, frame.ack),
            )
            if frame.frame_type == TunnelFrameType.DATA:
                self._remote_ack = frame.seq
                self._channel.send(
                    encode_tunnel_frame(
                        TunnelFrame(
                            frame_type=TunnelFrameType.ACK,
                            seq=0,
                            ack=frame.seq,
                        )
                    )
                )
                return self._security.open(frame.payload)
            if frame.frame_type == TunnelFrameType.CLOSE:
                self._closed = True
                return None
            if frame.frame_type == TunnelFrameType.RESET:
                self._closed = True
                raise RuntimeError("tunnel reset by peer")
            # OPEN / ACK / PING frames are control traffic; keep reading.

    def ping(self) -> None:
        if self._closed:
            return
        self._channel.send(
            encode_tunnel_frame(
                TunnelFrame(frame_type=TunnelFrameType.PING, seq=0, ack=self._remote_ack)
            )
        )

    def close(self) -> None:
        if self._closed:
            return
        self._channel.send(
            encode_tunnel_frame(
                TunnelFrame(frame_type=TunnelFrameType.CLOSE, seq=0, ack=self._remote_ack)
            )
        )
        self._closed = True
        self._channel.close()

    def stats(self) -> TunnelStats:
        return self._stats

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def is_open(self) -> bool:
        return self._open and not self._closed
