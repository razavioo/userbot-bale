"""
RPC transport: store-and-forward VPN frames as Bale chat messages.

Why this path exists
--------------------
When a Bale call drops (or WebRTC data channels are blocked at the
network layer), the chat channel usually still works — you can always
send a message. This transport encodes each VPN frame as the body of a
direct message and decodes inbound messages back into frames.

Status
------
**Wired but unverified live.** `BaleApiClient.send_message()` and
`.listen_messages()` exist (Phase 8), built from the decompiled
`MessagingOuterClass$RequestSendMessage` / `UpdateMessage` protos.
Unit-tested for wire-format roundtrip against our own encoder; not
yet tested end-to-end against production servers, so if the server
rejects with `invalid_payload`, the first thing to check is whether
the RPC needs an outer-tag wrapper like StartCall (tag 6) /
ImportContacts (tag 8) — see protos.py for the pattern.

Framing
-------
Bale message bodies are UTF-8 text. We base64 the binary VPN frame so
it survives whatever canonicalization the chat layer does. The
receiving side strips the prefix + b64-decodes.
"""

from __future__ import annotations

import base64
import logging
import queue
from typing import Optional

from baleobala.bale.messaging_backend import MessagingBackend

log = logging.getLogger(__name__)

# Prefix so plain text messages from the peer don't get confused with
# VPN payloads. Any message not starting with this is ignored by recv.
MSG_PREFIX = "\u200Bbb-vpn:"  # zero-width space + tag, nearly invisible in chat


class RpcTransportNotReady(RuntimeError):
    """Raised when the underlying BaleApiClient lacks send_message /
    listen_messages. See module docstring for the RE pointer."""


class RpcTransport:
    MTU = 3 * 1024  # conservative below Bale's message length cap
    RATE_HINT = 4_000.0  # bytes/s; store-and-forward, high latency

    def __init__(self, api_client: MessagingBackend, peer_id: int) -> None:
        self.mtu = self.MTU
        self.rate_hint = self.RATE_HINT
        self._client = api_client
        self._peer_id = peer_id
        self._rx: "queue.Queue[bytes]" = queue.Queue()
        self._closed = False
        self._wire_receive()

    # ---- public API ------------------------------------------------------

    def send_bytes(self, data: bytes) -> None:
        if self._closed:
            return
        if len(data) > self.MTU:
            raise ValueError(f"frame {len(data)} > rpc MTU {self.MTU}")
        send = getattr(self._client, "send_message", None)
        if send is None:
            raise RpcTransportNotReady(
                "BaleApiClient.send_message is not implemented. "
                "See baleobala.vpn.transports.rpc_transport module docstring."
            )
        body = MSG_PREFIX + base64.b64encode(data).decode("ascii")
        send(self._peer_id, body.encode("utf-8"))

    def recv_bytes(self, timeout: Optional[float] = None) -> Optional[bytes]:
        if self._closed:
            return None
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True

    # ---- internals -------------------------------------------------------

    def _wire_receive(self) -> None:
        listen = getattr(self._client, "listen_messages", None)
        if listen is None:
            log.warning(
                "rpc transport: BaleApiClient.listen_messages missing; "
                "inbound VPN frames over chat will be silently dropped. "
                "Implement listen_messages(peer_id, cb) to enable."
            )
            return
        try:
            listen(self._peer_id, self._on_message)
        except Exception:  # noqa: BLE001
            log.exception("rpc transport: listen_messages setup failed")

    def _on_message(self, body: bytes) -> None:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return
        if not text.startswith(MSG_PREFIX):
            return
        try:
            frame = base64.b64decode(text[len(MSG_PREFIX) :], validate=True)
        except (ValueError, base64.binascii.Error):
            return
        self._rx.put(frame)
