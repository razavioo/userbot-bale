from __future__ import annotations

import pytest

from baleobala.bale.protos import InboundMessage
from baleobala.mcp.server import BaleMcpService
from baleobala.userbot.client import BaleUserClient
from baleobala.userbot.runtime import EchoPlugin, UserbotRuntime
from baleobala.userbot.store import UserbotStore


class FakeApiClient:
    def __init__(self) -> None:
        self.callback = None
        self.sent: list[tuple[int, bytes]] = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def listen_all_messages(self, callback) -> None:  # noqa: ANN001
        self.callback = callback

    def send_message(self, peer_id: int, body: bytes) -> None:
        self.sent.append((peer_id, body))

    def load_dialogs(self, *, limit: int):
        return []


def test_store_allowlist_and_message_dedup(tmp_path) -> None:
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(42)
    assert store.allowed_peers() == [42]
    assert store.is_peer_allowed(42)
    assert store.record_message(
        message_id="in:42:1", peer_id=42, sender_id=42,
        direction="inbound", text="hello",
    )
    assert not store.record_message(
        message_id="in:42:1", peer_id=42, sender_id=42,
        direction="inbound", text="hello",
    )
    message = store.list_messages(42)[0]
    assert message["message_id"] == "in:42:1"
    assert message["direction"] == "inbound"
    assert message["text"] == "hello"


def test_user_client_persists_inbound_and_rejects_unapproved_send(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    assert api.suppress_auto_accept is True
    events = []
    client.on_message(events.append)
    client.start()
    assert api.callback is not None
    api.callback(InboundMessage(peer_user_id=11, sender_uid=11, rid=9, text="hello"))
    api.callback(InboundMessage(peer_user_id=11, sender_uid=11, rid=9, text="hello"))
    assert [event.text for event in events] == ["hello"]
    assert store.list_messages(11)[0]["message_id"] == "in:11:9"
    with pytest.raises(PermissionError):
        client.send_text(11, "no")
    store.allow_peer(11)
    client.send_text(11, "yes")
    assert api.sent == [(11, b"yes")]


def test_echo_plugin_only_replies_to_approved_peers(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(11)
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    runtime = UserbotRuntime(client, [EchoPlugin()])
    runtime.start()
    assert api.callback is not None
    api.callback(InboundMessage(peer_user_id=11, sender_uid=11, rid=1, text="approved"))
    api.callback(InboundMessage(peer_user_id=22, sender_uid=22, rid=1, text="blocked"))
    assert api.sent == [(11, b"approved")]


def test_user_client_limits_outbound_messages_per_peer(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(11)
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    for index in range(client.MAX_OUTBOUND_PER_MINUTE):
        client.send_text(11, f"message {index}")

    with pytest.raises(RuntimeError, match="rate limit"):
        client.send_text(11, "one too many")
    assert len(api.sent) == client.MAX_OUTBOUND_PER_MINUTE


def test_mcp_service_enforces_same_allowlist(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    service = BaleMcpService(client, store)
    with pytest.raises(PermissionError):
        service.send_text(12, "blocked")
    with pytest.raises(PermissionError):
        service.list_messages(12)
    assert not api.started
    store.allow_peer(12)
    assert service.send_text(12, "allowed") == {"ok": True, "peer_id": 12}
    assert api.sent == [(12, b"allowed")]
