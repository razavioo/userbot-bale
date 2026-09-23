"""Modern asyncio-native Bale userbot client, inspired by Telethon and Pyrogram."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import logging
import signal
import sys
import threading
import time
from typing import Any, Callable, Coroutine, List, Optional, Sequence, Union

from userbot_bale.bale.api import BaleApiClient
from userbot_bale.bale.protos import DialogInfo, HistoryMessage, InboundMessage
from userbot_bale.events import MessageEvent, NewMessage
from userbot_bale.filters import Filter
from userbot_bale.userbot.store import MemoryUserbotStore, UserbotStore
from userbot_bale.vpn.jwt_util import user_id as user_id_from_jwt

log = logging.getLogger(__name__)

HandlerFunc = Union[
    Callable[[MessageEvent], Coroutine[Any, Any, None]],
    Callable[[MessageEvent], None],
]


class AsyncBaleClient:
    """Asyncio-first client for Bale messenger automation.

    Usage:
        client = AsyncBaleClient(jwt="...")

        @client.on(events.NewMessage(pattern=r"^/ping"))
        async def handle_ping(event: MessageEvent):
            await event.reply("pong!")

        client.run()
    """

    def __init__(
        self,
        jwt: str | None = None,
        *,
        store: Any | None = None,
        api_client: BaleApiClient | None = None,
        allow_all: bool = True,
        rate_limit: int | None = None,
        ws_url: str | None = None,
    ) -> None:
        if jwt is None:
            # Try to resolve saved credentials automatically
            try:
                from userbot_bale.control.auth import load_auth_record
                rec = load_auth_record()
                if rec and rec.jwt:
                    jwt = rec.jwt
            except Exception:
                pass
        if not jwt:
            raise ValueError("A valid JWT must be provided or saved in local auth store")

        self._jwt = jwt
        self._store = store or MemoryUserbotStore()
        self._api = api_client or BaleApiClient(jwt=jwt, ws_url=ws_url)
        self._api.suppress_auto_accept = True
        self.allow_all = allow_all
        self.rate_limit = rate_limit

        self._self_user_id = user_id_from_jwt(jwt)
        self._dialog_peer_types: dict[int, int] = {}
        self._handlers: list[tuple[Any, HandlerFunc]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._disconnect_event: asyncio.Event | None = None
        self._worker_pool: ThreadPoolExecutor | None = None
        self._handler_tasks: set[asyncio.Task[None]] = set()

    @property
    def user_id(self) -> int | None:
        return self._self_user_id

    @property
    def store(self) -> Any:
        return self._store

    async def __aenter__(self) -> "AsyncBaleClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    def on(self, filter_or_event: Any = None) -> Callable[[HandlerFunc], HandlerFunc]:
        """Decorator to register a message or event handler."""
        def decorator(fn: HandlerFunc) -> HandlerFunc:
            self.add_event_handler(fn, filter_or_event)
            return fn
        return decorator

    def add_event_handler(self, handler: HandlerFunc, filter_or_event: Any = None) -> None:
        """Register an event handler with an optional filter or event descriptor."""
        if filter_or_event is None:
            filter_obj = NewMessage()
        elif isinstance(filter_or_event, (Filter, NewMessage)):
            filter_obj = filter_or_event
        elif isinstance(filter_or_event, type) and issubclass(filter_or_event, NewMessage):
            filter_obj = filter_or_event()
        else:
            filter_obj = filter_or_event
        self._handlers.append((filter_obj, handler))

    def remove_event_handler(self, handler: HandlerFunc) -> bool:
        """Remove a previously registered handler."""
        orig_len = len(self._handlers)
        self._handlers = [(f, h) for (f, h) in self._handlers if h != handler]
        return len(self._handlers) < orig_len

    async def start(self) -> None:
        """Start the client connection asynchronously."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._disconnect_event = asyncio.Event()
        self._worker_pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="userbot-bale",
        )

        # Connect the inbound dispatch
        self._api.listen_all_messages(self._on_inbound_threadsafe)
        await asyncio.to_thread(self._api.start)
        self._started = True
        if hasattr(self._store, "audit"):
            self._store.audit("client_started")

    async def stop(self) -> None:
        """Disconnect and release resources."""
        if not self._started:
            return
        await asyncio.to_thread(self._api.stop)
        await self.wait_idle()
        self._started = False
        if self._worker_pool is not None:
            self._worker_pool.shutdown(wait=True, cancel_futures=True)
            self._worker_pool = None
        if self._disconnect_event is not None:
            self._disconnect_event.set()
        if hasattr(self._store, "audit"):
            self._store.audit("client_stopped")

    async def run_until_disconnected(self) -> None:
        """Keep the client running until disconnected or stopped."""
        if not self._started:
            await self.start()
        if self._disconnect_event is not None:
            await self._disconnect_event.wait()

    async def wait_idle(self) -> None:
        """Wait until all inbound events currently being handled are complete."""
        while self._handler_tasks:
            await asyncio.gather(*tuple(self._handler_tasks), return_exceptions=True)

    def run(self) -> None:
        """Run the client synchronously until interrupted (Pyrogram/Telethon style)."""
        async def _main() -> None:
            await self.start()
            loop = asyncio.get_running_loop()
            stop_event = asyncio.Event()

            def _handle_signal() -> None:
                stop_event.set()

            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _handle_signal)
                except (NotImplementedError, RuntimeError):
                    pass

            try:
                await stop_event.wait()
            finally:
                await self.stop()

        try:
            asyncio.run(_main())
        except (KeyboardInterrupt, SystemExit):
            pass

    async def send_message(
        self,
        peer_id: int,
        text: str,
        *,
        peer_type: int | None = None,
        reply_to: int | None = None,
        is_silent: bool = False,
    ) -> None:
        """Send a text message to a peer, optionally quoting another message."""
        if not self.allow_all and hasattr(self._store, "is_peer_allowed"):
            if not self._store.is_peer_allowed(peer_id):
                if hasattr(self._store, "audit"):
                    self._store.audit("outbound_rejected", peer_id=peer_id, detail="peer_not_allowlisted")
                raise PermissionError(f"peer {peer_id} is not in the outbound allowlist")

        text = text.strip()
        if not text:
            raise ValueError("message text cannot be empty")
        if len(text) > 4000:
            raise ValueError("message text exceeds 4000 characters")

        if self.rate_limit is not None and hasattr(self._store, "reserve_outbound"):
            if not self._store.reserve_outbound(peer_id, maximum=self.rate_limit, window_seconds=60.0):
                if hasattr(self._store, "audit"):
                    self._store.audit("outbound_rejected", peer_id=peer_id, detail="rate_limited")
                raise RuntimeError("outbound rate limit reached for this peer")

        resolved_peer_type = peer_type or self._dialog_peer_types.get(peer_id, 1)

        await asyncio.to_thread(
            self._api.send_message,
            peer_id,
            text.encode("utf-8"),
            peer_type=resolved_peer_type,
            quoted_rid=reply_to,
            is_silent=is_silent,
        )

        message_id = f"out:{peer_id}:{time.time_ns()}"
        if hasattr(self._store, "record_message"):
            self._store.record_message(
                message_id=message_id,
                peer_id=peer_id,
                sender_id=self._self_user_id or 0,
                direction="outbound",
                text=text,
            )
        if hasattr(self._store, "audit"):
            self._store.audit("outbound_sent", peer_id=peer_id, detail=f"characters={len(text)}")

    # Alias for send_message
    send_text = send_message

    async def mark_read(self, peer_id: int, date: int, *, peer_type: int | None = None) -> None:
        """Mark messages up to timestamp as read."""
        if not self.allow_all and hasattr(self._store, "is_peer_allowed"):
            if not self._store.is_peer_allowed(peer_id):
                raise PermissionError(f"peer {peer_id} is not in the outbound allowlist")
        if self._started:
            resolved_peer_type = peer_type or self._dialog_peer_types.get(peer_id, 1)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._worker_pool,
                lambda: self._api.mark_read(
                    peer_id, date, peer_type=resolved_peer_type,
                ),
            )

    async def get_dialogs(self, limit: int = 20) -> list[dict[str, object]]:
        """Retrieve recent dialogs / conversations."""
        remote: list[dict[str, object]] = []
        if self._started:
            try:
                dialogs = await asyncio.to_thread(self._api.load_dialogs, limit=limit)
                self._dialog_peer_types.update({d.peer_id: d.peer_type for d in dialogs})
                remote = [
                    {
                        "peer_id": d.peer_id,
                        "peer_type": d.peer_type,
                        "unread_count": d.unread_count,
                        "last_message_date": d.last_message_date,
                        "source": "remote",
                    }
                    for d in dialogs
                ]
            except Exception:
                remote = []
        if remote:
            return remote
        if hasattr(self._store, "list_dialogs"):
            return self._store.list_dialogs(limit)
        return []

    async def get_messages(self, peer_id: int, limit: int = 20) -> list[dict[str, object]]:
        """Retrieve recent message history with a peer."""
        if self._started:
            try:
                peer_type = self._dialog_peer_types.get(peer_id)
                if peer_type is None:
                    dialogs = await asyncio.to_thread(self._api.load_dialogs, limit=100)
                    self._dialog_peer_types.update({d.peer_id: d.peer_type for d in dialogs})
                    peer_type = self._dialog_peer_types.get(peer_id, 1)
                remote = await asyncio.to_thread(self._api.load_history, peer_id, peer_type=peer_type, limit=limit)
                if remote:
                    return [
                        {
                            "message_id": f"remote:{peer_id}:{m.rid}",
                            "peer_id": peer_id,
                            "sender_id": m.sender_uid,
                            "direction": "outbound" if self._self_user_id and m.sender_uid == self._self_user_id else "inbound",
                            "text": m.text or "",
                            "received_at": float(m.date),
                            "source": "remote",
                        }
                        for m in remote
                    ]
            except Exception:
                pass
        if hasattr(self._store, "list_messages"):
            return self._store.list_messages(peer_id, limit)
        return []

    async def search_messages(
        self, query: str, *, peer_id: int | None = None, limit: int = 20,
    ) -> list[dict[str, object]]:
        """Search local messages."""
        if hasattr(self._store, "search_messages"):
            return self._store.search_messages(query, peer_id=peer_id, limit=limit)
        return []

    async def search_contacts(self, query: str) -> list[dict[str, object]]:
        """Search Bale contacts matching query."""
        if not self._started:
            return []
        try:
            contacts = await asyncio.to_thread(self._api.search_contacts, query)
            return [
                {"user_id": c.user_id, "phone_number": c.phone_number, "name": c.name}
                for c in contacts
            ]
        except Exception:
            return []

    async def resolve_peer(self, phone: str | int) -> int:
        """Resolve a phone number to a numeric user_id."""
        if not self._started:
            raise RuntimeError("client not started")
        return await asyncio.to_thread(self._api.resolve_peer, phone)

    def _on_inbound_threadsafe(self, message: InboundMessage) -> None:
        """Called from the WebSocket background thread when a message arrives."""
        if self._loop is None or not self._loop.is_running():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self._loop:
            self._schedule_inbound(message)
            return
        self._loop.call_soon_threadsafe(self._schedule_inbound, message)

    def _schedule_inbound(self, message: InboundMessage) -> None:
        task = asyncio.create_task(self._process_inbound(message))
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _process_inbound(self, message: InboundMessage) -> None:
        if self._self_user_id is not None and message.sender_uid == self._self_user_id:
            return
        peer_id = message.sender_uid or message.peer_user_id
        received_at = time.time()
        message_id = f"in:{peer_id}:{message.rid}" if message.rid else f"in:{peer_id}:{received_at}"

        if message.peer_type and peer_id:
            self._dialog_peer_types[peer_id] = message.peer_type

        if hasattr(self._store, "record_message"):
            if not self._store.record_message(
                message_id=message_id,
                peer_id=peer_id,
                sender_id=message.sender_uid,
                direction="inbound",
                text=message.text,
                received_at=received_at,
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

        if hasattr(self._store, "audit"):
            self._store.audit("inbound_received", peer_id=peer_id, detail=f"characters={len(message.text)}")

        for filter_obj, handler in list(self._handlers):
            try:
                matched = False
                if hasattr(filter_obj, "check"):
                    matched = await filter_obj.check(self, event)
                elif callable(filter_obj):
                    res = filter_obj(self, event)
                    matched = bool(await res) if inspect.isawaitable(res) else bool(res)
                else:
                    matched = True

                if matched:
                    if inspect.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(self._worker_pool, handler, event)
            except Exception:
                log.exception("Error executing handler %r for event %r", handler, event)


BaleClient = AsyncBaleClient
