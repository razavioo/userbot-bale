"""Messaging backend abstractions shared by Bale VPN transports."""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


MessageCallback = Callable[[bytes], None]


@runtime_checkable
class MessagingBackend(Protocol):
    """Minimal Bale messaging surface required by VPN fallback transports."""

    def start(self, timeout: float = 15.0) -> None: ...

    def stop(self) -> None: ...

    def send_message(self, peer_id: int, body: bytes, *, peer_type: int = 1) -> None: ...

    def listen_messages(self, peer_id: int, callback: MessageCallback) -> None: ...
