from __future__ import annotations

import asyncio
import re
import pytest

from userbot_bale import BaleClient, BaleUserClient, Message, MessageEvent, events, filters
from userbot_bale.bale.protos import InboundMessage, OutPeer, RequestSendMessage
from userbot_bale.userbot.store import MemoryUserbotStore, UserbotStore


# ---------------------------------------------------------------------------
# Fake API Client for Testing
# ---------------------------------------------------------------------------
class FakeApiClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.started = False
        self.messages_callback = None
        self.mark_read_calls: list[tuple[int, int]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def listen_all_messages(self, callback) -> None:
        self.messages_callback = callback

    def send_message(
        self,
        peer_id: int,
        body: bytes,
        *,
        peer_type: int = 1,
        quoted_rid: int | None = None,
        is_silent: bool = False,
    ) -> None:
        self.sent.append({
            "peer_id": peer_id,
            "body": body,
            "text": body.decode("utf-8"),
            "peer_type": peer_type,
            "quoted_rid": quoted_rid,
            "is_silent": is_silent,
        })

    def mark_read(self, peer_id: int, date: int, *, peer_type: int = 1) -> None:
        self.mark_read_calls.append((peer_id, date))

    def load_dialogs(self, *, limit: int = 20):
        return []

    def load_history(self, peer_id: int, *, peer_type: int = 1, limit: int = 20):
        return []


# ---------------------------------------------------------------------------
# Filter Tests
# ---------------------------------------------------------------------------
def test_filters_text():
    msg1 = MessageEvent(message_id="1", peer_id=1, sender_id=2, text="hello", received_at=1.0)
    msg2 = MessageEvent(message_id="2", peer_id=1, sender_id=2, text="   ", received_at=1.0)

    assert filters.text(None, msg1) is True
    assert filters.text(None, msg2) is False


def test_filters_command():
    f_cmd = filters.command(["start", "help"], prefixes=["/", "!"])
    msg_valid = MessageEvent(message_id="1", peer_id=1, sender_id=2, text="/start fast 123", received_at=1.0)
    msg_alt_prefix = MessageEvent(message_id="2", peer_id=1, sender_id=2, text="!help", received_at=1.0)
    msg_invalid = MessageEvent(message_id="3", peer_id=1, sender_id=2, text="/unknown", received_at=1.0)

    assert f_cmd(None, msg_valid) is True
    assert msg_valid.command == "start"
    assert msg_valid.args == ["fast", "123"]

    assert f_cmd(None, msg_alt_prefix) is True
    assert msg_alt_prefix.command == "help"
    assert msg_alt_prefix.args == []

    assert f_cmd(None, msg_invalid) is False


def test_filters_regex():
    f_reg = filters.regex(r"^order #?(\d+)$")
    msg_match = MessageEvent(message_id="1", peer_id=1, sender_id=2, text="order #9876", received_at=1.0)
    msg_no_match = MessageEvent(message_id="2", peer_id=1, sender_id=2, text="something else", received_at=1.0)

    assert f_reg(None, msg_match) is True
    assert msg_match.pattern_match is not None
    assert msg_match.pattern_match.group(1) == "9876"

    assert f_reg(None, msg_no_match) is False


def test_filters_composition():
    f_cmd = filters.command("ping")
    f_peer = filters.peer(100)
    f_private = filters.private

    f_combined = (f_cmd & f_peer) | ~f_private

    msg1 = MessageEvent(message_id="1", peer_id=100, sender_id=2, text="/ping", received_at=1.0, peer_type=1)
    msg2 = MessageEvent(message_id="2", peer_id=200, sender_id=2, text="/ping", received_at=1.0, peer_type=1)
    msg_group = MessageEvent(message_id="3", peer_id=300, sender_id=2, text="random", received_at=1.0, peer_type=2)

    assert f_combined(None, msg1) is True
    assert f_combined(None, msg2) is False
    assert f_combined(None, msg_group) is True  # ~private is True


def test_filters_custom():
    f_even = filters.create(lambda msg: msg.peer_id % 2 == 0)
    msg_even = MessageEvent(message_id="1", peer_id=100, sender_id=2, text="hi", received_at=1.0)
    msg_odd = MessageEvent(message_id="2", peer_id=101, sender_id=2, text="hi", received_at=1.0)

    assert f_even(None, msg_even) is True
    assert f_even(None, msg_odd) is False


# ---------------------------------------------------------------------------
# Proto RequestSendMessage Tests
# ---------------------------------------------------------------------------
def test_request_send_message_quoted_and_silent():
    req = RequestSendMessage(
        peer=OutPeer(user_id=123, type=1),
        text="quoted message",
        rid=42,
        quoted_rid=999,
        is_silent=True,
    )
    encoded = req.encode()
    assert b"quoted message" in encoded
    # Check that quoted field tag (tag 5, wire type 2 = 0x2a) is in encoded
    assert b"\x2a" in encoded
    # Check that is_silent tag (tag 7, wire type 0 = 0x38) is in encoded
    assert b"\x38\x01" in encoded


# ---------------------------------------------------------------------------
# MemoryStore Tests
# ---------------------------------------------------------------------------
def test_memory_userbot_store():
    store = MemoryUserbotStore()
    store.allow_peer(500)
    assert store.is_peer_allowed(500) is True
    assert store.is_peer_allowed(501) is False

    inserted = store.record_message(
        message_id="m1",
        peer_id=500,
        sender_id=500,
        direction="inbound",
        text="hello store",
        received_at=10.0,
    )
    assert inserted is True
    # duplicate insert
    assert store.record_message(
        message_id="m1",
        peer_id=500,
        sender_id=500,
        direction="inbound",
        text="dup",
        received_at=11.0,
    ) is False

    dialogs = store.list_dialogs()
    assert len(dialogs) == 1
    assert dialogs[0]["peer_id"] == 500

    messages = store.list_messages(500)
    assert len(messages) == 1
    assert messages[0]["text"] == "hello store"

    search = store.search_messages("store")
    assert len(search) == 1


# ---------------------------------------------------------------------------
# Synchronous BaleUserClient Bound Actions & Decorator Tests
# ---------------------------------------------------------------------------
def test_bale_user_client_fluent_and_decorators():
    api = FakeApiClient()
    client = BaleUserClient(jwt="eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo3N30.sig", api_client=api, enforce_allowlist=False)

    received_events: list[MessageEvent] = []

    @client.on_message(filters.command("ping"))
    def handle_ping(event: MessageEvent):
        received_events.append(event)
        event.reply("pong!")

    client.start()
    assert api.started is True

    # Simulate incoming message
    client._on_inbound(InboundMessage(peer_user_id=123, sender_uid=123, rid=101, text="/ping"))

    assert len(received_events) == 1
    assert received_events[0].command == "ping"
    assert len(api.sent) == 1
    assert api.sent[0]["peer_id"] == 123
    assert api.sent[0]["text"] == "pong!"
    assert api.sent[0]["quoted_rid"] == 101

    client.stop()
    assert api.started is False


# ---------------------------------------------------------------------------
# AsyncBaleClient Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_async_bale_client_basic_flow():
    api = FakeApiClient()
    store = MemoryUserbotStore()
    client = BaleClient(
        jwt="eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo3N30.sig",
        api_client=api,
        store=store,
        allow_all=True,
    )

    handled_pings = []

    @client.on(events.NewMessage(pattern=r"^/ping$"))
    async def on_ping(event: Message):
        handled_pings.append(event)
        await event.reply("pong from async!")

    async with client:
        assert api.started is True
        # Inbound message
        api.messages_callback(InboundMessage(peer_user_id=888, sender_uid=888, rid=202, text="/ping"))

        # Give event loop a moment to process the task
        await asyncio.sleep(0.05)

        assert len(handled_pings) == 1
        assert handled_pings[0].peer_id == 888
        assert handled_pings[0].is_private is True
        assert len(api.sent) == 1
        assert api.sent[0]["peer_id"] == 888
        assert api.sent[0]["text"] == "pong from async!"
        assert api.sent[0]["quoted_rid"] == 202

    assert api.started is False


@pytest.mark.asyncio
async def test_async_bale_client_filters_and_mark_read():
    api = FakeApiClient()
    client = BaleClient(
        jwt="eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo3N30.sig",
        api_client=api,
        allow_all=True,
    )

    handled = []

    @client.on(filters.command("order") & filters.peer(999))
    async def on_order(event: Message):
        handled.append(event)
        await event.mark_read()

    await client.start()
    try:
        # Match
        api.messages_callback(InboundMessage(peer_user_id=999, sender_uid=999, rid=301, text="/order 456"))
        # Non-match (different peer)
        api.messages_callback(InboundMessage(peer_user_id=111, sender_uid=111, rid=302, text="/order 456"))
        await asyncio.sleep(0.05)

        assert len(handled) == 1
        assert handled[0].args == ["456"]
        assert len(api.mark_read_calls) == 1
        assert api.mark_read_calls[0][0] == 999
    finally:
        await client.stop()
