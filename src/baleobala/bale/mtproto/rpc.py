"""
RPC dispatcher skeleton for Nasim-MTProto.

Once framing and auth-key are live (blocked on a capture; see
docs/CAPTURE.md), this module will own:

- a background reader task that pulls frames off the socket and
  routes them to the matching caller's future, or to the update
  subscriber callback;
- a public `rpc(request)` async method that submits a protobuf,
  allocates a request-id, registers a future, writes the frame, and
  awaits the response;
- a `subscribe(callback)` method for server-initiated updates
  (UpdateCallAction, UpdateCallStatusChanged, etc.).

Today it exposes the surface so downstream modules (auth, api) can
import clean names; every method raises with a pointer to the
capture workflow.
"""

from __future__ import annotations

from typing import Any, Callable


class MtpRpcClient:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[Any], None]] = []

    async def rpc(self, request) -> Any:  # noqa: ANN001
        raise NotImplementedError(
            "MTProto RPC dispatch is gated on a live capture. "
            "See docs/CAPTURE.md for the workflow that unblocks this."
        )

    def subscribe(self, callback: Callable[[Any], None]) -> None:
        """Register a callback for server-initiated updates."""
        self._subscribers.append(callback)
