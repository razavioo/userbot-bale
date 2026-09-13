"""Core carrier interfaces."""

from __future__ import annotations

from typing import Protocol

from userbot_bale.audio_backend import AudioSink, AudioSource


class CarrierSession(Protocol):
    """A media session that can publish and receive audio."""

    def sink(self) -> AudioSink:
        """Return the local audio sink."""

    def source(self) -> AudioSource:
        """Return the local audio source."""

    def start(self) -> None:
        """Connect to the media carrier."""

    def stop(self) -> None:
        """Disconnect from the media carrier."""

