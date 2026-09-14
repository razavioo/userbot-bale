"""Event classes and fluent message objects for Bale userbots."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MessageEvent:
    """Rich message event with bound actions (Telethon/Pyrogram style)."""

    message_id: str
    peer_id: int
    sender_id: int
    text: str
    received_at: float
    peer_type: int = 1
    rid: int = 0
    direction: str = "inbound"
    raw: Any = None
    client: Any = None
    pattern_match: Optional[re.Match] = None
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)

    @property
    def chat_id(self) -> int:
        """Alias for peer_id."""
        return self.peer_id

    @property
    def raw_text(self) -> str:
        """Alias for text."""
        return self.text

    @property
    def is_private(self) -> bool:
        """True if the message is in a 1-on-1 private chat."""
        return self.peer_type == 1

    @property
    def is_group(self) -> bool:
        """True if the message is in a group."""
        return self.peer_type == 2

    @property
    def is_channel(self) -> bool:
        """True if the message is in a channel."""
        return self.peer_type == 3

    @property
    def is_incoming(self) -> bool:
        """True if message was received from an external user."""
        return self.direction != "outbound"

    @property
    def is_outgoing(self) -> bool:
        """True if message was sent by the current account."""
        return self.direction == "outbound"

    def reply(self, text: str, *, is_silent: bool = False) -> Any:
        """Reply to this message.

        If client is asynchronous, returns a coroutine (use `await event.reply(...)`).
        If client is synchronous, executes immediately and returns result.
        """
        if self.client is None:
            raise RuntimeError("Cannot reply: event is not bound to a client instance")
        send_fn = getattr(self.client, "send_text", None) or getattr(self.client, "send_message", None)
        if send_fn is None:
            raise RuntimeError("Client does not implement send_text")

        try:
            return send_fn(
                self.peer_id,
                text,
                peer_type=self.peer_type,
                reply_to=self.rid or None,
                is_silent=is_silent,
            )
        except TypeError:
            # Fallback for clients with simpler signature
            return send_fn(self.peer_id, text, peer_type=self.peer_type)

    def respond(self, text: str, *, is_silent: bool = False) -> Any:
        """Send a new message to the same chat."""
        return self.reply(text, is_silent=is_silent)

    def mark_read(self) -> Any:
        """Mark this chat as read up to this message's timestamp."""
        if self.client is None:
            return None
        mark_fn = getattr(self.client, "mark_read", None)
        if mark_fn is None:
            return None
        timestamp = int(self.received_at * 1000) if self.received_at else 0
        return mark_fn(self.peer_id, timestamp, peer_type=self.peer_type)

    # Explicit async aliases
    async def reply_async(self, text: str, *, is_silent: bool = False) -> Any:
        res = self.reply(text, is_silent=is_silent)
        if inspect.isawaitable(res):
            return await res
        return res

    async def respond_async(self, text: str, *, is_silent: bool = False) -> Any:
        return await self.reply_async(text, is_silent=is_silent)

    async def mark_read_async(self) -> Any:
        res = self.mark_read()
        if inspect.isawaitable(res):
            return await res
        return res


# Alias Message to MessageEvent for standard Telethon/Pyrogram naming
Message = MessageEvent


class NewMessage:
    """Event pattern descriptor (Telethon style: @client.on(events.NewMessage(...)))."""

    def __init__(self, filter: Any = None, **kwargs: Any) -> None:
        from userbot_bale.filters import Filter, all as all_filter, command, peer, regex

        self.filter = filter or all_filter
        if "pattern" in kwargs:
            self.filter = self.filter & regex(kwargs["pattern"])
        if "command" in kwargs:
            self.filter = self.filter & command(kwargs["command"])
        if "peer_id" in kwargs:
            self.filter = self.filter & peer(kwargs["peer_id"])

    async def check(self, client: Any, event: MessageEvent) -> bool:
        if hasattr(self.filter, "check"):
            return await self.filter.check(client, event)
        if callable(self.filter):
            res = self.filter(client, event)
            if inspect.isawaitable(res):
                return bool(await res)
            return bool(res)
        return True

    def __call__(self, client: Any, event: MessageEvent) -> bool:
        if hasattr(self.filter, "__call__"):
            res = self.filter(client, event)
            if inspect.isawaitable(res):
                raise RuntimeError("Async filter evaluated synchronously")
            return bool(res)
        return True
