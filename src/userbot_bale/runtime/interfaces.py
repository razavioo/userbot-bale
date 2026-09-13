"""Core interfaces for the tunnel runtime."""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class ByteChannel(Protocol):
    """Bidirectional byte transport."""

    def send(self, data: bytes) -> None:
        """Send one atomic byte payload to the peer."""

    def recv(self, timeout: float | None = None) -> bytes | None:
        """Receive the next atomic byte payload, or None on timeout/close."""

    def close(self) -> None:
        """Close the channel."""


@runtime_checkable
class SecurityProvider(Protocol):
    """Pluggable per-session sealing/opening."""

    def start_handshake(self, role: str) -> bytes:
        """Return the first handshake payload for the given role."""

    def seal(self, payload: bytes) -> bytes:
        """Protect outbound payload bytes."""

    def open(self, payload: bytes) -> bytes:
        """Recover inbound payload bytes."""

    def session_info(self) -> dict[str, str]:
        """Return debug metadata about the negotiated session."""


@runtime_checkable
class TunnelTransport(Protocol):
    """A bidirectional byte stream used by higher-level protocols."""

    def send(self, data: bytes) -> None:
        """Send raw bytes to the peer."""

    def recv(self, timeout: float | None = None) -> bytes | None:
        """Receive raw bytes from the peer."""

    def close(self) -> None:
        """Close the transport."""
