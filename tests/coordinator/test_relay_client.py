"""Tests for relay-side coordinator helpers: CoordinatorReporter + ExpectedClientSet."""

from __future__ import annotations

import queue
import threading
import time

import pytest

from baleobala.coordinator.protocol import (
    ControlMessage,
    Kind,
    decode,
    encode,
    make_expect_client,
)
from baleobala.coordinator.relay_client import (
    CoordinatorReporter,
    ExpectedClientSet,
    handle_coordinator_instruction,
)


# ---- Fakes ---------------------------------------------------------------

class FakeDataChannel:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._inbox: queue.Queue[bytes | None] = queue.Queue()

    def send_bytes(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        try:
            return self._inbox.get(timeout=timeout or 1.0)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._inbox.put(None)

    def push(self, msg: ControlMessage) -> None:
        self._inbox.put(encode(msg))


class FakeLiveKitSession:
    def __init__(self, *, url, token, identity):
        self.stopped = False
        self._channels: dict[str, FakeDataChannel] = {}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def wait_for_remote_participant(self, timeout=10.0) -> None:
        pass

    def data_channel(self, *, topic="vpn", reliable=True) -> FakeDataChannel:
        if topic not in self._channels:
            self._channels[topic] = FakeDataChannel()
        return self._channels[topic]


class FakeBaleApiClient:
    def __init__(self) -> None:
        self.fetch_calls: list[int] = []
        self._session_factory = FakeLiveKitSession

    def fetch_livekit_credentials(self, peer_id: int, *, creds_timeout=15.0):
        self.fetch_calls.append(peer_id)
        from baleobala.bale.protos import CallCredentials
        return CallCredentials(url="wss://x", token="t", room="r", peer_id=peer_id)


# ---- ExpectedClientSet tests ---------------------------------------------

def test_consume_returns_session_id_when_expected():
    s = ExpectedClientSet()
    s.register(client_peer_id=42, session_id="s1", expires_in_secs=30)
    result = s.consume(42)
    assert result == "s1"


def test_consume_removes_entry():
    s = ExpectedClientSet()
    s.register(client_peer_id=42, session_id="s1", expires_in_secs=30)
    s.consume(42)
    assert s.consume(42) is None


def test_consume_unknown_returns_none():
    s = ExpectedClientSet()
    assert s.consume(99) is None


def test_consume_expired_returns_none():
    s = ExpectedClientSet()
    s.register(client_peer_id=42, session_id="s1", expires_in_secs=0)
    time.sleep(0.05)
    assert s.consume(42) is None


def test_register_overrides_previous():
    s = ExpectedClientSet()
    s.register(client_peer_id=42, session_id="old", expires_in_secs=30)
    s.register(client_peer_id=42, session_id="new", expires_in_secs=30)
    assert s.consume(42) == "new"


# ---- CoordinatorReporter tests -------------------------------------------

@pytest.fixture
def reporter(monkeypatch):
    from baleobala.coordinator import relay_client as rc
    monkeypatch.setattr(rc, "LiveKitSession", FakeLiveKitSession)
    client = FakeBaleApiClient()
    r = CoordinatorReporter(
        coordinator_peer_id=9000,
        relay_id="box1-0",
        relay_peer_id=100,
        bale_client=client,
        identity_prefix="test-reporter",
    )
    r.start()
    yield r, client
    r.stop()


def test_report_online_dials_coordinator(reporter):
    r, client = reporter
    r.report_online(capacity=2)
    # Give worker thread time to dispatch
    deadline = time.monotonic() + 2.0
    while not client.fetch_calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client.fetch_calls == [9000]


def test_report_sends_correct_kind(reporter, monkeypatch):
    from baleobala.coordinator import relay_client as rc
    sent_msgs: list[bytes] = []

    class CaptureLKSession(FakeLiveKitSession):
        def data_channel(self, *, topic="vpn", reliable=True):
            ch = super().data_channel(topic=topic, reliable=reliable)
            original_send = ch.send_bytes
            def capturing_send(payload):
                sent_msgs.append(payload)
                original_send(payload)
            ch.send_bytes = capturing_send
            return ch

    monkeypatch.setattr(rc, "LiveKitSession", CaptureLKSession)
    client = FakeBaleApiClient()
    r = CoordinatorReporter(
        coordinator_peer_id=9000,
        relay_id="box1-0",
        relay_peer_id=100,
        bale_client=client,
    )
    r.start()
    r.report_heartbeat(in_use=[1, 2, 3])
    deadline = time.monotonic() + 2.0
    while not sent_msgs and time.monotonic() < deadline:
        time.sleep(0.05)
    r.stop()

    assert sent_msgs, "no message was sent"
    msg = decode(sent_msgs[0])
    assert msg.kind == Kind.HEARTBEAT
    assert msg.get("in_use") == [1, 2, 3]


def test_report_released_sends_released(reporter, monkeypatch):
    from baleobala.coordinator import relay_client as rc
    sent_msgs: list[bytes] = []

    class CaptureLKSession(FakeLiveKitSession):
        def data_channel(self, *, topic="vpn", reliable=True):
            ch = super().data_channel(topic=topic, reliable=reliable)
            ch.send_bytes = lambda p: sent_msgs.append(p)
            return ch

    monkeypatch.setattr(rc, "LiveKitSession", CaptureLKSession)
    client = FakeBaleApiClient()
    r = CoordinatorReporter(
        coordinator_peer_id=9000,
        relay_id="box1-0",
        relay_peer_id=100,
        bale_client=client,
    )
    r.start()
    r.report_released("session-xyz")
    deadline = time.monotonic() + 2.0
    while not sent_msgs and time.monotonic() < deadline:
        time.sleep(0.05)
    r.stop()

    assert sent_msgs
    msg = decode(sent_msgs[0])
    assert msg.kind == Kind.RELEASED
    assert msg.get("session_id") == "session-xyz"


def test_heartbeat_fires_periodically(reporter, monkeypatch):
    from baleobala.coordinator import relay_client as rc
    monkeypatch.setattr(rc, "LiveKitSession", FakeLiveKitSession)
    client = FakeBaleApiClient()
    r = CoordinatorReporter(
        coordinator_peer_id=9000,
        relay_id="box1-0",
        relay_peer_id=100,
        bale_client=client,
    )
    r.start()
    fire_count = {"n": 0}

    def get_in_use():
        fire_count["n"] += 1
        return []

    r.start_heartbeat(interval=0.05, get_in_use=get_in_use)
    time.sleep(0.3)
    r.stop()
    assert fire_count["n"] >= 2


# ---- handle_coordinator_instruction tests --------------------------------

def test_handle_coordinator_instruction_registers_expected(monkeypatch):
    from baleobala.coordinator import relay_client as rc
    monkeypatch.setattr(rc, "LiveKitSession", FakeLiveKitSession)

    expected = ExpectedClientSet()
    from baleobala.bale.protos import CallCredentials, IncomingCallEvent

    creds = CallCredentials(url="wss://x", token="t", room="r", peer_id=9000)
    event = IncomingCallEvent(credentials=creds, peer_id=9000)

    # Inject EXPECT_CLIENT into the session's control channel before it's read
    inject_done = threading.Event()

    original_factory = rc.LiveKitSession

    class InjectingSession(FakeLiveKitSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def wait_for_remote_participant(self, timeout=10.0):
            # Push the EXPECT_CLIENT message right away
            ch = self.data_channel(topic="control", reliable=True)
            ch.push(make_expect_client(
                client_peer_id=42, session_id="s1", expires_in_secs=30,
            ))
            inject_done.set()

    monkeypatch.setattr(rc, "LiveKitSession", InjectingSession)

    handle_coordinator_instruction(
        event=event,
        expected=expected,
        reporter=None,
        identity="relay-test",
    )

    assert expected.consume(42) == "s1"


def test_handle_coordinator_instruction_sends_ack(monkeypatch):
    from baleobala.coordinator import relay_client as rc

    ack_payloads: list[bytes] = []

    class AckCapturingSession(FakeLiveKitSession):
        def wait_for_remote_participant(self, timeout=10.0):
            ch = self.data_channel(topic="control", reliable=True)
            ch.push(make_expect_client(
                client_peer_id=42, session_id="s1", expires_in_secs=30,
            ))
            original_send = ch.send_bytes
            ch.send_bytes = lambda p: ack_payloads.append(p) or original_send(p)

    monkeypatch.setattr(rc, "LiveKitSession", AckCapturingSession)

    from baleobala.bale.protos import CallCredentials, IncomingCallEvent
    creds = CallCredentials(url="wss://x", token="t", room="r", peer_id=9000)
    event = IncomingCallEvent(credentials=creds, peer_id=9000)

    handle_coordinator_instruction(event=event, expected=ExpectedClientSet(), reporter=None)

    assert ack_payloads
    ack = decode(ack_payloads[0])
    assert ack.kind == Kind.EXPECT_ACK
