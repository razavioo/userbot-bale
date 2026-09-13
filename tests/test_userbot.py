from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from userbot_bale.bale.protos import DialogInfo, HistoryMessage, InboundMessage
from userbot_bale.mcp.server import BaleMcpService
from userbot_bale.userbot.client import BaleUserClient
from userbot_bale.userbot.runtime import EchoPlugin, UserbotRuntime
from userbot_bale.userbot.store import UserbotStore


class FakeApiClient:
    def __init__(self) -> None:
        self.callback = None
        self.sent: list[tuple[int, bytes]] = []
        self.started = False
        self.call_order: list[str] = []
        self.dialogs: list[DialogInfo] = []
        self.history: list[HistoryMessage] = []

    def start(self) -> None:
        self.call_order.append("start")
        self.started = True

    def stop(self) -> None:
        self.started = False

    def listen_all_messages(self, callback) -> None:  # noqa: ANN001
        self.call_order.append("listen_all_messages")
        self.callback = callback

    def send_message(self, peer_id: int, body: bytes) -> None:
        self.sent.append((peer_id, body))

    def load_dialogs(self, *, limit: int):
        return self.dialogs[:limit]

    def load_history(self, peer_id: int, *, peer_type: int = 1, limit: int = 20):
        return self.history[:limit]


def test_user_client_closes_api_if_start_persistence_fails() -> None:
    class FailingStore:
        def audit(self, event_type: str, **kwargs) -> None:  # noqa: ANN003
            raise RuntimeError("store unavailable")

    api = FakeApiClient()
    client = BaleUserClient(jwt="test", store=FailingStore(), api_client=api)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="store unavailable"):
        client.start()

    assert api.call_order[:2] == ["listen_all_messages", "start"]
    assert not api.started


def test_user_client_ignores_own_get_diff_echo() -> None:
    class MemoryStore:
        def __init__(self) -> None:
            self.recorded: list[str] = []
        def audit(self, event_type: str, **kwargs) -> None:  # noqa: ANN003
            pass
        def record_message(self, *, message_id: str, **kwargs) -> bool:  # noqa: ANN003
            self.recorded.append(message_id)
            return True

    payload = base64.urlsafe_b64encode(
        json.dumps({"payload": {"user_id": 77}}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    api = FakeApiClient()
    store = MemoryStore()
    client = BaleUserClient(
        jwt=f"header.{payload}.signature", store=store, api_client=api,  # type: ignore[arg-type]
    )
    events = []
    client.on_message(events.append)

    client._on_inbound(InboundMessage(peer_user_id=77, sender_uid=77, rid=1, text="own"))
    client._on_inbound(InboundMessage(peer_user_id=12, sender_uid=12, rid=2, text="external"))

    assert store.recorded == ["in:12:2"]
    assert [event.text for event in events] == ["external"]


def test_store_lists_locally_observed_dialogs() -> None:
    store = UserbotStore(Path(":memory:"))
    store.record_message(
        message_id="in:7:1", peer_id=7, sender_id=7,
        direction="inbound", text="older", received_at=1.0,
    )
    store.record_message(
        message_id="out:8:1", peer_id=8, sender_id=0,
        direction="outbound", text="newer", received_at=2.0,
    )

    dialogs = store.list_dialogs()

    assert [dialog["peer_id"] for dialog in dialogs] == [8, 7]
    assert dialogs[0]["source"] == "local_observed"
    assert dialogs[0]["message_count"] == 1


def test_user_client_reads_remote_history_after_start() -> None:
    api = FakeApiClient()
    api.dialogs = [DialogInfo(peer_id=11, peer_type=1, unread_count=0, last_message_date=9)]
    api.history = [HistoryMessage(rid=3, sender_uid=11, date=10, text="remote")]
    store = UserbotStore(Path(":memory:"))
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    client.start()

    messages = client.list_messages(11)

    assert messages[0]["message_id"] == "remote:11:3"
    assert messages[0]["text"] == "remote"
    assert messages[0]["source"] == "remote"


def test_store_allowlist_and_message_dedup(tmp_path) -> None:
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(42)
    assert store.allowed_peers() == [42]
    assert store.is_peer_allowed(42)
    assert store.record_message(
        message_id="in:42:1", peer_id=42, sender_id=42,
        direction="inbound", text="hello", received_at=1.0,
    )
    assert not store.record_message(
        message_id="in:42:1", peer_id=42, sender_id=42,
        direction="inbound", text="hello", received_at=1.0,
    )
    message = store.list_messages(42)[0]
    assert message["message_id"] == "in:42:1"
    assert message["direction"] == "inbound"
    assert message["text"] == "hello"
    store.record_message(
        message_id="out:9:1", peer_id=9, sender_id=0,
        direction="outbound", text="newer", received_at=2.0,
    )
    dialogs = store.list_dialogs()
    assert dialogs[0]["peer_id"] == 9
    assert dialogs[0]["source"] == "local_observed"
    assert dialogs[1]["peer_id"] == 42


def test_user_client_persists_inbound_and_rejects_unapproved_send(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    assert api.suppress_auto_accept is True
    events = []
    client.on_message(events.append)
    client.start()
    assert api.callback is not None
    assert api.call_order[:2] == ["listen_all_messages", "start"]
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
