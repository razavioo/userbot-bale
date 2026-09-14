"""High-level Bale messaging client with durable deduplication and policy."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Union

from userbot_bale.bale.api import BaleApiClient
from userbot_bale.bale.protos import DialogInfo, InboundMessage
from userbot_bale.events import MessageEvent, NewMessage
from userbot_bale.filters import Filter
from userbot_bale.userbot.store import MemoryUserbotStore, UserbotStore
from userbot_bale.vpn.jwt_util import user_id as user_id_from_jwt


class BaleUserClient:
    """Owns one authenticated Bale messaging session for a userbot."""

    MAX_OUTBOUND_PER_MINUTE = 20

    def __init__(
        self,
        *,
        jwt: str,
        store: Any | None = None,
        api_client: BaleApiClient | None = None,
        enforce_allowlist: bool = True,
        max_outbound_per_minute: int | None = 20,
    ) -> None:
        self.store = store or UserbotStore()
        self._api = api_client or BaleApiClient(jwt=jwt)
        # Call handling belongs to the carrier/VPN boundary. A userbot must
        # never accept an unexpected call while it is listening for messages.
        self._api.suppress_auto_accept = True
        self._handlers: list[tuple[Any, Callable[[MessageEvent], None]]] = []
        self._started = False
        self._self_user_id = user_id_from_jwt(jwt)
        self._dialog_peer_types: dict[int, int] = {}
        self.enforce_allowlist = enforce_allowlist
        self.max_outbound_per_minute = max_outbound_per_minute

    @property
    def user_id(self) -> int | None:
        return self._self_user_id

    def start(self) -> None:
        if self._started:
            return
        # Register before the WS connects: Bale's initial GetDiff response
        # can arrive immediately after the handshake and contains the
        # bootstrap update batch.  Registering afterwards loses that batch.
        self._api.listen_all_messages(self._on_inbound)
        try:
            self._api.start()
            self.store.audit("client_started")
        except Exception:
            # ``start`` has already opened the socket when persistence (or a
            # plugin store) fails.  Do not leave a background WS behind.
            self._api.stop()
            raise
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._api.stop()
        self.store.audit("client_stopped")
        self._started = False

    def on_message(self, handler_or_filter: Any = None) -> Any:
        """Register a message handler. Supports both traditional callback and decorator pattern."""
        if callable(handler_or_filter) and not isinstance(handler_or_filter, Filter):
            self._handlers.append((None, handler_or_filter))
            return handler_or_filter

        def decorator(fn: Callable[[MessageEvent], None]) -> Callable[[MessageEvent], None]:
            self._handlers.append((handler_or_filter, fn))
            return fn

        return decorator

    on = on_message

    def send_text(
        self,
        peer_id: int,
        text: str,
        *,
        peer_type: int | None = None,
        reply_to: int | None = None,
        is_silent: bool = False,
    ) -> None:
        if self.enforce_allowlist and not self.store.is_peer_allowed(peer_id):
            self.store.audit("outbound_rejected", peer_id=peer_id, detail="peer_not_allowlisted")
            raise PermissionError(f"peer {peer_id} is not in the outbound allowlist")
        text = text.strip()
        if not text:
            raise ValueError("message text cannot be empty")
        if len(text) > 4_000:
            raise ValueError("message text exceeds 4000 characters")
        max_limit = self.max_outbound_per_minute or self.MAX_OUTBOUND_PER_MINUTE
        if self.max_outbound_per_minute is not None and not self.store.reserve_outbound(
            peer_id, maximum=max_limit, window_seconds=60.0,
        ):
            self.store.audit("outbound_rejected", peer_id=peer_id, detail="rate_limited")
            raise RuntimeError("outbound rate limit reached for this peer")
        resolved_peer_type = peer_type or self._dialog_peer_types.get(peer_id, 1)
        try:
            self._api.send_message(
                peer_id,
                text.encode("utf-8"),
                peer_type=resolved_peer_type,
                quoted_rid=reply_to,
                is_silent=is_silent,
            )
        except TypeError:
            self._api.send_message(peer_id, text.encode("utf-8"))
        message_id = "out:" + hashlib.sha256(
            f"{peer_id}:{time.time_ns()}:{text}".encode("utf-8")
        ).hexdigest()
        self.store.record_message(
            message_id=message_id, peer_id=peer_id, sender_id=self._self_user_id or 0,
            direction="outbound", text=text,
        )
        self.store.audit("outbound_sent", peer_id=peer_id, detail=f"characters={len(text)}")

    def list_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        remote: list[dict[str, object]] = []
        if self._started:
            try:
                dialogs = self._api.load_dialogs(limit=limit)
                self._remember_dialog_types(dialogs)
                remote = [
                    {
                        "peer_id": dialog.peer_id,
                        "peer_type": dialog.peer_type,
                        "unread_count": dialog.unread_count,
                        "last_message_date": dialog.last_message_date,
                        "source": "remote",
                    }
                    for dialog in dialogs
                ]
            except Exception:  # noqa: BLE001
                remote = []
        # Preserve a real server result whenever available. The local index
        # keeps the userbot useful during a transient API failure or while it
        # is offline, without claiming to be full account history.
        return remote or self.store.list_dialogs(limit)

    def list_messages(self, peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        if self._started:
            try:
                peer_type = self._dialog_peer_types.get(peer_id)
                if peer_type is None:
                    self._remember_dialog_types(self._api.load_dialogs(limit=100))
                    peer_type = self._dialog_peer_types.get(peer_id, 1)
                remote = self._api.load_history(
                    peer_id, peer_type=peer_type, limit=limit,
                )
            except Exception:  # noqa: BLE001
                remote = []
            if remote:
                return [
                    {
                        "message_id": f"remote:{peer_id}:{message.rid}",
                        "peer_id": peer_id,
                        "sender_id": message.sender_uid,
                        "direction": (
                            "outbound"
                            if self._self_user_id is not None
                            and message.sender_uid == self._self_user_id
                            else "inbound"
                        ),
                        "text": message.text or "",
                        "received_at": float(message.date),
                        "source": "remote",
                    }
                    for message in remote
                ]
        return self.store.list_messages(peer_id, limit)

    def search_messages(
        self, query: str, *, peer_id: int | None = None, limit: int = 20,
    ) -> list[dict[str, object]]:
        return self.store.search_messages(query, peer_id=peer_id, limit=limit)

    def search_contacts(self, query: str) -> list[dict[str, object]]:
        if not self._started:
            return []
        try:
            contacts = self._api.search_contacts(query)
            return [
                {
                    "user_id": c.user_id,
                    "phone_number": c.phone_number,
                    "name": c.name,
                }
                for c in contacts
            ]
        except Exception:  # noqa: BLE001
            return []

    def resolve_phone(self, phone: str | int) -> int:
        if not self._started:
            raise RuntimeError("client not started")
        return self._api.resolve_peer(phone)

    def mark_read(self, peer_id: int, date: int, *, peer_type: int | None = None) -> None:
        if self.enforce_allowlist and not self.store.is_peer_allowed(peer_id):
            raise PermissionError(f"peer {peer_id} is not in the outbound allowlist")
        if self._started:
            resolved_peer_type = peer_type or self._dialog_peer_types.get(peer_id, 1)
            try:
                self._api.mark_read(peer_id, date, peer_type=resolved_peer_type)
            except Exception:  # noqa: BLE001
                pass

    def _remember_dialog_types(self, dialogs: list[DialogInfo]) -> None:
        self._dialog_peer_types.update(
            {dialog.peer_id: dialog.peer_type for dialog in dialogs}
        )

    def _on_inbound(self, message: InboundMessage) -> None:
        # GetDiff can replay our own recent text messages alongside inbound
        # updates.  Treating those as inbound would let EchoPlugin reply to
        # ourselves and create a loop.  A missing sender id is retained: it
        # is common for some non-private update shapes.
        if (
            self._self_user_id is not None
            and message.sender_uid == self._self_user_id
        ):
            return
        peer_id = message.sender_uid or message.peer_user_id
        fingerprint = hashlib.sha256(
            f"{peer_id}:{message.text}".encode("utf-8")
        ).hexdigest()
        message_id = f"in:{peer_id}:{message.rid}" if message.rid else f"in:{fingerprint}"
        received_at = time.time()
        if message.peer_type and peer_id:
            self._dialog_peer_types[peer_id] = message.peer_type
        if not self.store.record_message(
            message_id=message_id, peer_id=peer_id, sender_id=message.sender_uid,
            direction="inbound", text=message.text, received_at=received_at,
        ):
            return
        event = MessageEvent(
            message_id=message_id,
            peer_id=peer_id,
            sender_id=message.sender_uid,
            text=message.text,
            received_at=received_at,
            peer_type=message.peer_type or 1,
            rid=message.rid,
            direction="inbound",
            raw=message,
            client=self,
        )
        self.store.audit("inbound_received", peer_id=peer_id, detail=f"characters={len(message.text)}")
        for filter_obj, handler in tuple(self._handlers):
            try:
                matched = True
                if filter_obj is not None:
                    if hasattr(filter_obj, "__call__"):
                        matched = bool(filter_obj(self, event))
                if matched:
                    handler(event)
            except Exception:
                pass
