"""Plugin dispatch for long-running Bale userbots."""

from __future__ import annotations

import logging
from typing import Protocol

from userbot_bale.userbot.client import BaleUserClient, MessageEvent

log = logging.getLogger(__name__)


class UserbotPlugin(Protocol):
    def handle(self, event: MessageEvent, client: BaleUserClient) -> None: ...


class EchoPlugin:
    """Explicit opt-in example plugin that echoes messages from approved peers."""

    def handle(self, event: MessageEvent, client: BaleUserClient) -> None:
        if client.store.is_peer_allowed(event.peer_id):
            client.send_text(event.peer_id, event.text)


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
