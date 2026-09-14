"""Plugin dispatch for long-running Bale userbots."""

from __future__ import annotations

import logging
import re
from typing import Callable, Protocol

from userbot_bale.userbot.client import BaleUserClient, MessageEvent

log = logging.getLogger(__name__)


class UserbotPlugin(Protocol):
    def handle(self, event: MessageEvent, client: BaleUserClient) -> None: ...


class EchoPlugin:
    """Explicit opt-in example plugin that echoes messages from approved peers."""

    def handle(self, event: MessageEvent, client: BaleUserClient) -> None:
        if client.store.is_peer_allowed(event.peer_id):
            client.send_text(event.peer_id, event.text)


class CommandDispatcher:
    r"""Dispatches messages to command handlers, regex patterns, or a fallback.

    Usage:
        dispatcher = CommandDispatcher(prefix="/")

        @dispatcher.command("ping")
        def ping(event: MessageEvent, client: BaleUserClient, args: list[str]) -> None:
            client.send_text(event.peer_id, "pong")

        @dispatcher.regex(r"^echo\s+(.*)")
        def echo(event: MessageEvent, client: BaleUserClient, match: re.Match) -> None:
            client.send_text(event.peer_id, match.group(1))

        runtime = UserbotRuntime(client, plugins=[dispatcher])
    """

    def __init__(self, prefix: str = "/") -> None:
        self.prefix = prefix
        self._commands: dict[str, Callable[[MessageEvent, BaleUserClient, list[str]], None]] = {}
        self._regex_handlers: list[tuple[re.Pattern, Callable[[MessageEvent, BaleUserClient, re.Match], None]]] = []
        self._default_handler: Callable[[MessageEvent, BaleUserClient], None] | None = None

    def command(self, name: str) -> Callable:
        name = name.lower().lstrip(self.prefix)
        def decorator(fn: Callable[[MessageEvent, BaleUserClient, list[str]], None]) -> Callable:
            self._commands[name] = fn
            return fn
        return decorator

    def regex(self, pattern: str | re.Pattern) -> Callable:
        compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
        def decorator(fn: Callable[[MessageEvent, BaleUserClient, re.Match], None]) -> Callable:
            self._regex_handlers.append((compiled, fn))
            return fn
        return decorator

    def default(self, fn: Callable[[MessageEvent, BaleUserClient], None]) -> Callable:
        self._default_handler = fn
        return fn

    def handle(self, event: MessageEvent, client: BaleUserClient) -> None:
        if not client.store.is_peer_allowed(event.peer_id):
            return
        text = event.text.strip()
        if text.startswith(self.prefix):
            parts = text[len(self.prefix):].split()
            if parts:
                cmd_name = parts[0].lower()
                handler = self._commands.get(cmd_name)
                if handler is not None:
                    handler(event, client, parts[1:])
                    return
        for pattern, r_handler in self._regex_handlers:
            match = pattern.search(text)
            if match:
                r_handler(event, client, match)
                return
        if self._default_handler is not None:
            self._default_handler(event, client)


class UserbotRuntime:
    def __init__(self, client: BaleUserClient, plugins: list[UserbotPlugin] | None = None) -> None:
        self.client = client
        self.plugins = plugins or []
        self.client.on_message(self._dispatch)

    def start(self) -> None:
        self.client.start()

    def stop(self) -> None:
        self.client.stop()

    def _dispatch(self, event: MessageEvent) -> None:
        for plugin in self.plugins:
            try:
                plugin.handle(event, self.client)
            except Exception:  # noqa: BLE001
                log.exception("userbot plugin failed: %s", type(plugin).__name__)
