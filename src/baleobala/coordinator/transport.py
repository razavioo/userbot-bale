"""Transport abstraction for the coordinator.

The coordinator orchestrates short Bale calls + control-channel exchanges.
This module defines the wire-level interface as a Protocol so the service
logic can be tested with a mock and wired to a real BaleApiClient +
LiveKitSession in production code.

The real implementation lives elsewhere (slice 2) so this module stays free
of Bale/LiveKit imports and is safe to depend on from tests.
"""

from __future__ import annotations

from typing import Callable, Protocol

from baleobala.coordinator.protocol import ControlMessage


class IncomingCall(Protocol):
    """A control-plane call that just landed on the coordinator's JWT."""

    @property
    def peer_id(self) -> int:
        """User_id of the calling Bale account."""

    def recv(self, *, timeout: float) -> ControlMessage | None:
        """Read one control message from the peer, or return None on timeout."""

    def send(self, msg: ControlMessage) -> None:
        """Write a control message back to the peer."""

    def hangup(self) -> None:
        """Tear down the call. Idempotent."""


class CoordinatorTransport(Protocol):
    """Wire interface the service uses to talk over Bale."""

    def listen(self, on_call: Callable[[IncomingCall], None]) -> None:
        """Begin accepting incoming Bale calls. Each call invokes on_call in
        a worker thread; the handler owns the call lifecycle (must hangup)."""

    def quick_exchange(
        self,
        *,
        peer_id: int,
        send: ControlMessage,
        timeout: float,
    ) -> ControlMessage | None:
        """Place an outbound call to peer_id, send `send`, await one reply,
        hang up. Returns the reply or None on timeout / failure."""

    def stop(self) -> None:
        """Stop listening and tear down resources. Idempotent."""
