"""
DataChannel transport: VPN frames ride on a LiveKit WebRTC DataChannel
inside the same Bale call that would otherwise carry audio. This is the
preferred transport — reliable mode gives ordered delivery at ~100 KB/s+.

The actual send/recv logic lives on `LiveKitDataChannel` in
userbot_bale.bale.livekit_backend; this module is a 1:1 adapter so the VPN
router can import transports uniformly.
"""

from __future__ import annotations

from userbot_bale.bale.livekit_backend import LiveKitDataChannel, LiveKitSession


class DataChannelTransport:
    def __init__(
        self,
        session: LiveKitSession,
        *,
        topic: str = "vpn",
        reliable: bool = True,
    ) -> None:
        self._channel = session.data_channel(topic=topic, reliable=reliable)
        self.mtu = self._channel.mtu
        self.rate_hint = self._channel.rate_hint

    def send_bytes(self, data: bytes) -> None:
        self._channel.send_bytes(data)

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        return self._channel.recv_bytes(timeout)

    def close(self) -> None:
        self._channel.close()
