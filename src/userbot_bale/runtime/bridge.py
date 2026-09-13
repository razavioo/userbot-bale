"""High-level runtime bridge that composes carrier + audio channel + tunnel."""

from __future__ import annotations

from dataclasses import dataclass

from userbot_bale.carrier.interfaces import CarrierSession
from userbot_bale.runtime.audio_channel import AudioByteChannel
from userbot_bale.runtime.frame import TunnelRole
from userbot_bale.runtime.session import TunnelSession


@dataclass
class AudioTunnelBridge:
    """Full-duplex byte tunnel over any audio carrier."""

    carrier: CarrierSession
    role: TunnelRole = TunnelRole.CLIENT
    protocol: str = "fast"
    volume: int = 50

    def __post_init__(self) -> None:
        self._channel = AudioByteChannel(
            sink=self.carrier.sink(),
            source=self.carrier.source(),
            protocol=self.protocol,
            volume=self.volume,
        )
        self._tunnel = TunnelSession(self._channel, role=self.role)

    def start(self) -> None:
        self.carrier.start()
        self._channel.start()
        self._tunnel.open()

    def __enter__(self) -> "AudioTunnelBridge":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send(self, data: bytes) -> int:
        return self._tunnel.send(data)

    def recv(self, timeout: float | None = None) -> bytes | None:
        return self._tunnel.recv(timeout=timeout)

    def close(self) -> None:
        self._tunnel.close()
        self._channel.close()
        self.carrier.stop()

    def stats(self):
        return self._tunnel.stats()

    @property
    def closed(self) -> bool:
        return self._tunnel.closed
