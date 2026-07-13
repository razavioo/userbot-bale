"""High-level Bale messaging client with durable deduplication and policy."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from baleobala.bale.api import BaleApiClient
from baleobala.bale.protos import InboundMessage
from baleobala.userbot.store import UserbotStore


@dataclass(frozen=True)
class MessageEvent:
    message_id: str
    peer_id: int
    sender_id: int
    text: str
    received_at: float


class BaleUserClient:
    """Owns one authenticated Bale messaging session for a userbot."""

    MAX_OUTBOUND_PER_MINUTE = 20

    def __init__(
        self,
        *,
        jwt: str,
        store: UserbotStore | None = None,
        api_client: BaleApiClient | None = None,
    ) -> None:
        self.store = store or UserbotStore()
        self._api = api_client or BaleApiClient(jwt=jwt)
        # Call handling belongs to the carrier/VPN boundary. A userbot must
        # never accept an unexpected call while it is listening for messages.
        self._api.suppress_auto_accept = True
        self._handlers: list[Callable[[MessageEvent], None]] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._api.start()
        self._api.listen_all_messages(self._on_inbound)
        self.store.audit("client_started")
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._api.stop()
        self.store.audit("client_stopped")
        self._started = False

    def on_message(self, handler: Callable[[MessageEvent], None]) -> None:
        self._handlers.append(handler)

    def send_text(self, peer_id: int, text: str) -> None:
        if not self.store.is_peer_allowed(peer_id):
            self.store.audit("outbound_rejected", peer_id=peer_id, detail="peer_not_allowlisted")
            raise PermissionError(f"peer {peer_id} is not in the outbound allowlist")
        text = text.strip()
        if not text:
            raise ValueError("message text cannot be empty")
        if len(text) > 4_000:
            raise ValueError("message text exceeds 4000 characters")
        if not self.store.reserve_outbound(
            peer_id, maximum=self.MAX_OUTBOUND_PER_MINUTE, window_seconds=60.0,
        ):
            self.store.audit("outbound_rejected", peer_id=peer_id, detail="rate_limited")
            raise RuntimeError("outbound rate limit reached for this peer")
        self._api.send_message(peer_id, text.encode("utf-8"))
        message_id = "out:" + hashlib.sha256(
            f"{peer_id}:{time.time_ns()}:{text}".encode("utf-8")
        ).hexdigest()
        self.store.record_message(
            message_id=message_id, peer_id=peer_id, sender_id=0,
            direction="outbound", text=text,
        )
        self.store.audit("outbound_sent", peer_id=peer_id, detail=f"characters={len(text)}")

    def list_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        return [dialog.__dict__ for dialog in self._api.load_dialogs(limit=limit)]

    def list_messages(self, peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        return self.store.list_messages(peer_id, limit)

    def _on_inbound(self, message: InboundMessage) -> None:
        peer_id = message.sender_uid or message.peer_user_id
        fingerprint = hashlib.sha256(
            f"{peer_id}:{message.text}".encode("utf-8")
        ).hexdigest()
        message_id = f"in:{peer_id}:{message.rid}" if message.rid else f"in:{fingerprint}"
        received_at = time.time()
        if not self.store.record_message(
            message_id=message_id, peer_id=peer_id, sender_id=message.sender_uid,
            direction="inbound", text=message.text, received_at=received_at,
        ):
            return
        event = MessageEvent(
            message_id=message_id, peer_id=peer_id, sender_id=message.sender_uid,
            text=message.text, received_at=received_at,
        )
        self.store.audit("inbound_received", peer_id=peer_id, detail=f"characters={len(message.text)}")
        for handler in tuple(self._handlers):
            handler(event)
