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

    def search_contacts(self, query: str):
        return []


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
    preview = service.send_text(12, "allowed")
    assert preview["needs_confirm"] is True
    assert preview["ok"] is False
    assert not api.sent
    confirmed = service.send_text(
        12, "allowed", confirm_token=preview["confirm_token"],
    )
    assert confirmed == {"ok": True, "peer_id": 12, "confirmed": True}
    assert api.sent == [(12, b"allowed")]
    with pytest.raises(PermissionError, match="confirm_token"):
        service.send_text(12, "allowed", confirm_token=preview["confirm_token"])


def test_mcp_service_rejects_unbounded_limits(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(12)
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    service = BaleMcpService(client, store)

    with pytest.raises(ValueError, match="between 1 and 100"):
        service.list_dialogs(101)
    with pytest.raises(ValueError, match="between 1 and 100"):
        service.list_messages(12, 0)

    assert not api.started


def test_mcp_service_returns_normalized_dict_shapes(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(12)
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    service = BaleMcpService(client, store)

    assert service.list_messages(12) == {"messages": [], "count": 0}
    assert service.search_messages("hello") == {"messages": [], "count": 0}
    assert service.search_contacts("ali") == {"contacts": [], "count": 0}
    assert service.list_dialogs() == {"dialogs": [], "count": 0}
    # Dict tools must stay dict-shaped so FastMCP does not wrap them
    # under a different key than the list tools.
    for result in (
        service.list_messages(12),
        service.search_messages("x"),
        service.search_contacts("x"),
        service.list_dialogs(),
        service.account_status(),
    ):
        assert isinstance(result, dict)
        assert set(result) != {"result"}


def test_user_client_caches_resolved_phones(tmp_path) -> None:
    api = FakeApiClient()
    api.resolve_peer = lambda phone: 42
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]
    client.start()

    assert client.resolve_phone("+989121234567") == 42
    api.resolve_peer = lambda phone: pytest.fail("resolved phone should be cached")
    assert client.resolve_phone("989121234567") == 42

    # A brand-new client/process must hit the durable store, not the network.
    api2 = FakeApiClient()
    api2.resolve_peer = lambda phone: pytest.fail("store cache should avoid network")
    store2 = UserbotStore(tmp_path / "userbot.sqlite3")
    client2 = BaleUserClient(jwt="test", store=store2, api_client=api2)  # type: ignore[arg-type]
    client2.start()
    assert client2.resolve_phone("+989121234567") == 42


def test_store_persists_resolved_phone_across_instances(tmp_path) -> None:
    path = tmp_path / "userbot.sqlite3"
    store = UserbotStore(path)
    assert store.get_resolved_phone("989121234567") is None
    store.put_resolved_phone("989121234567", 42)
    store.close()

    reopened = UserbotStore(path)
    assert reopened.get_resolved_phone("989121234567") == 42
    reopened.close()


def test_user_client_records_sender_id_and_peer_type(tmp_path) -> None:
    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(99)
    payload = base64.urlsafe_b64encode(
        json.dumps({"payload": {"user_id": 55}}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    client = BaleUserClient(
        jwt=f"header.{payload}.signature", store=store, api_client=api,  # type: ignore[arg-type]
    )
    client.send_text(99, "hello")
    messages = store.list_messages(99)
    assert len(messages) == 1
    assert messages[0]["sender_id"] == 55
    assert messages[0]["direction"] == "outbound"


def test_cmd_userbot_status_and_cli(tmp_path, capsys) -> None:
    from userbot_bale.cli import cmd_userbot
    import argparse

    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(100)

    class Args:
        userbot_cmd = "status"

    # monkeypatch store path in cmd_userbot
    import userbot_bale.userbot
    old_store_cls = userbot_bale.userbot.UserbotStore
    userbot_bale.userbot.UserbotStore = lambda: store
    try:
        assert cmd_userbot(Args()) == 0
        out = capsys.readouterr().out
        assert "Auth state" in out
        assert "100" in out
    finally:
        userbot_bale.userbot.UserbotStore = old_store_cls


def test_command_dispatcher_routes_commands_and_regex(tmp_path) -> None:
    from userbot_bale.userbot import CommandDispatcher

    api = FakeApiClient()
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.allow_peer(77)
    client = BaleUserClient(jwt="test", store=store, api_client=api)  # type: ignore[arg-type]

    dispatcher = CommandDispatcher(prefix="!")
    called = {}

    @dispatcher.command("ping")
    def ping_cmd(event, c, args):
        called["ping"] = args
        c.send_text(event.peer_id, "pong")

    @dispatcher.regex(r"^calc\s+(\d+)\+(\d+)")
    def calc_cmd(event, c, match):
        res = int(match.group(1)) + int(match.group(2))
        called["calc"] = res
        c.send_text(event.peer_id, f"result={res}")

    @dispatcher.default
    def fallback(event, c):
        called["default"] = event.text

    runtime = UserbotRuntime(client, [dispatcher])
    runtime.start()

    # 1. Trigger command
    api.callback(InboundMessage(peer_user_id=77, sender_uid=77, rid=1, text="!ping now"))
    assert called.get("ping") == ["now"]
    assert (77, b"pong") in api.sent

    # 2. Trigger regex
    api.callback(InboundMessage(peer_user_id=77, sender_uid=77, rid=2, text="calc 10+25"))
    assert called.get("calc") == 35
    assert (77, b"result=35") in api.sent

    # 3. Trigger default
    api.callback(InboundMessage(peer_user_id=77, sender_uid=77, rid=3, text="other text"))
    assert called.get("default") == "other text"


def test_store_search_messages(tmp_path) -> None:
    store = UserbotStore(tmp_path / "userbot.sqlite3")
    store.record_message(
        message_id="m1", peer_id=10, sender_id=10,
        direction="inbound", text="apple banana cherry", received_at=1.0,
    )
    store.record_message(
        message_id="m2", peer_id=20, sender_id=20,
        direction="inbound", text="banana date fig", received_at=2.0,
    )

    all_banana = store.search_messages("banana")
    assert len(all_banana) == 2
    assert all_banana[0]["message_id"] == "m2"
    assert all_banana[1]["message_id"] == "m1"

    scoped = store.search_messages("banana", peer_id=10)
    assert len(scoped) == 1
    assert scoped[0]["message_id"] == "m1"

    none_found = store.search_messages("grape")
    assert len(none_found) == 0
