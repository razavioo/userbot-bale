"""Unit tests for BaleCoordinatorTransport.

We mock BaleApiClient + LiveKitSession + LiveKitDataChannel so the test does
not require a real Bale server or a real LiveKit room. The goal is to verify
the transport's wiring decisions: which client gets used for listen vs
dispatch, how incoming-call events are translated into IncomingCall objects,
and how quick_exchange round-trips a control message.
"""

from __future__ import annotations

import queue
import threading
from typing import Any
from unittest.mock import patch

import pytest

from baleobala.bale.protos import CallCredentials, IncomingCallEvent
from baleobala.coordinator.protocol import (
    CONTROL_TOPIC,
    ControlMessage,
    Kind,
    decode,
    encode,
    make_assign,
    make_hello,
    make_online,
)


# ---- Fakes that mirror the real interfaces just enough for the transport.


class FakeDataChannel:
    def __init__(self) -> None:
        self.outgoing: list[bytes] = []
        self.incoming: queue.Queue[bytes | None] = queue.Queue()
        self.closed = False
        self.mtu = 1200
        self.rate_hint = 0

    def send_bytes(self, payload: bytes) -> None:
        self.outgoing.append(payload)

    def recv_bytes(self, timeout: float | None = None) -> bytes | None:
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True
        self.incoming.put(None)


class FakeLiveKitSession:
    instances: list["FakeLiveKitSession"] = []

    def __init__(self, *, url: str, token: str, identity: str, **_kwargs: object) -> None:
        self.url = url
        self.token = token
        self.identity = identity
        self.started = False
        self.stopped = False
        self.remote_joined = False
        self.channels: dict[str, FakeDataChannel] = {}
        FakeLiveKitSession.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        for ch in self.channels.values():
            ch.close()

    def wait_for_remote_participant(self, timeout: float = 30.0) -> None:
        self.remote_joined = True

    def data_channel(self, *, topic: str = "vpn", reliable: bool = True) -> FakeDataChannel:
        if topic not in self.channels:
            self.channels[topic] = FakeDataChannel()
        return self.channels[topic]


class FakeBaleApiClient:
    def __init__(self, *, jwt: str, ws_tls_config: Any = None) -> None:
        self.jwt = jwt
        self.started = False
        self.stopped = False
        self.listener = None
        self.fetch_calls: list[int] = []
        self.fetch_response: CallCredentials | None = None

    def start(self, *args, **kwargs) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def listen_incoming_calls(self, callback) -> None:
        self.listener = callback

    def fetch_livekit_credentials(self, peer_id: int, *, creds_timeout: float = 30.0) -> CallCredentials:
        self.fetch_calls.append(peer_id)
        if self.fetch_response is None:
            return CallCredentials(
                url=f"wss://livekit.test/{peer_id}", token=f"tok-{peer_id}",
                room=f"room-{peer_id}", peer_id=peer_id,
            )
        return self.fetch_response

    def is_started(self) -> bool:
        return self.started


@pytest.fixture(autouse=True)
def reset_session_instances():
    FakeLiveKitSession.instances.clear()
    yield
    FakeLiveKitSession.instances.clear()


@pytest.fixture
def transport(monkeypatch):
    from baleobala.coordinator import bale_transport as bt

    monkeypatch.setattr(bt, "LiveKitSession", FakeLiveKitSession)

    clients: dict[str, FakeBaleApiClient] = {}

    def factory(*, jwt: str, ws_tls_config=None):
        client = FakeBaleApiClient(jwt=jwt, ws_tls_config=ws_tls_config)
        clients[jwt] = client
        return client

    t = bt.BaleCoordinatorTransport(
        listen_jwt="jwt-listen",
        dispatch_jwt="jwt-dispatch",
        client_factory=factory,
    )
    t._clients = clients  # type: ignore[attr-defined]
    return t


def test_listen_starts_both_clients_and_registers_callback(transport):
    received: list = []
    transport.listen(lambda call: received.append(call))
    assert transport._clients["jwt-listen"].started
    assert transport._clients["jwt-dispatch"].started
    assert transport._clients["jwt-listen"].listener is not None


def test_shared_jwt_uses_one_client(monkeypatch):
    from baleobala.coordinator import bale_transport as bt

    monkeypatch.setattr(bt, "LiveKitSession", FakeLiveKitSession)
    clients = []

    def factory(*, jwt: str, ws_tls_config=None):
        c = FakeBaleApiClient(jwt=jwt)
        clients.append(c)
        return c

    t = bt.BaleCoordinatorTransport(
        listen_jwt="jwt-x",
        dispatch_jwt="jwt-x",
        client_factory=factory,
    )
    t.listen(lambda _: None)
    assert len(clients) == 1
    assert clients[0].started


def test_incoming_call_dispatches_to_handler(transport):
    received_calls: list = []
    done = threading.Event()

    def handler(call):
        received_calls.append(call)
        done.set()

    transport.listen(handler)
    listen_client = transport._clients["jwt-listen"]
    creds = CallCredentials(
        url="wss://livekit.test/in", token="tok-in", room="room-in", peer_id=42,
    )
    listen_client.listener(IncomingCallEvent(credentials=creds, peer_id=42))
    assert done.wait(timeout=2.0)
    assert received_calls[0].peer_id == 42


def test_incoming_call_recv_decodes_control_message(transport):
    received_calls: list = []
    done = threading.Event()

    def handler(call):
        received_calls.append(call)
        done.set()

    transport.listen(handler)
    listen_client = transport._clients["jwt-listen"]
    creds = CallCredentials(
        url="wss://livekit.test/in", token="tok-in", room="room-in", peer_id=42,
    )
    listen_client.listener(IncomingCallEvent(credentials=creds, peer_id=42))
    done.wait(timeout=2.0)
    call = received_calls[0]

    # Find the inbound session and push a HELLO into its control channel
    session = next(s for s in FakeLiveKitSession.instances if s.identity == "coordinator-in-42")
    channel = session.data_channel(topic=CONTROL_TOPIC, reliable=True)
    channel.incoming.put(encode(make_hello(client_id="dev-1")))

    msg = call.recv(timeout=1.0)
    assert msg is not None and msg.kind == Kind.HELLO
    assert msg.get("client_id") == "dev-1"


def test_incoming_call_send_emits_encoded_payload(transport):
    received_calls: list = []
    done = threading.Event()

    def handler(call):
        received_calls.append(call)
        done.set()

    transport.listen(handler)
    listen_client = transport._clients["jwt-listen"]
    creds = CallCredentials(
        url="wss://livekit.test/in", token="tok-in", room="room-in", peer_id=42,
    )
    listen_client.listener(IncomingCallEvent(credentials=creds, peer_id=42))
    done.wait(timeout=2.0)
    call = received_calls[0]

    call.send(make_assign(relay_peer_id=99, session_id="s1", expires_in_secs=30))

    session = next(s for s in FakeLiveKitSession.instances if s.identity == "coordinator-in-42")
    channel = session.channels[CONTROL_TOPIC]
    assert len(channel.outgoing) == 1
    decoded = decode(channel.outgoing[0])
    assert decoded.kind == Kind.ASSIGN
    assert decoded.get("relay_peer_id") == 99


def test_incoming_call_hangup_stops_session(transport):
    received_calls: list = []
    done = threading.Event()

    def handler(call):
        received_calls.append(call)
        done.set()

    transport.listen(handler)
    listen_client = transport._clients["jwt-listen"]
    creds = CallCredentials(
        url="wss://livekit.test/in", token="tok-in", room="room-in", peer_id=42,
    )
    listen_client.listener(IncomingCallEvent(credentials=creds, peer_id=42))
    done.wait(timeout=2.0)
    call = received_calls[0]
    call.hangup()

    session = next(s for s in FakeLiveKitSession.instances if s.identity == "coordinator-in-42")
    assert session.stopped


def test_quick_exchange_round_trip(transport):
    transport.listen(lambda _: None)
    dispatch_client = transport._clients["jwt-dispatch"]
    dispatch_client.fetch_response = CallCredentials(
        url="wss://livekit.test/out", token="tok-out", room="room-out", peer_id=200,
    )

    # Pre-arrange the relay's reply on the channel that will be created
    expected_reply = ControlMessage(kind=Kind.EXPECT_ACK, body={})

    # We can't set the reply before data_channel is created, so wrap data_channel
    # to inject the reply once the channel exists.
    original = FakeLiveKitSession.data_channel

    def patched_data_channel(self, *, topic="vpn", reliable=True):
        ch = original(self, topic=topic, reliable=reliable)
        if topic == CONTROL_TOPIC and not ch.outgoing:
            ch.incoming.put(encode(expected_reply))
        return ch

    with patch.object(FakeLiveKitSession, "data_channel", patched_data_channel):
        reply = transport.quick_exchange(
            peer_id=200,
            send=ControlMessage(kind=Kind.EXPECT_CLIENT, body={"x": 1}),
            timeout=1.0,
        )

    assert dispatch_client.fetch_calls == [200]
    assert reply is not None
    assert reply.kind == Kind.EXPECT_ACK

    out_session = next(s for s in FakeLiveKitSession.instances if s.identity == "coordinator-out-200")
    assert out_session.stopped


def test_quick_exchange_returns_none_on_timeout(transport):
    transport.listen(lambda _: None)
    dispatch_client = transport._clients["jwt-dispatch"]
    dispatch_client.fetch_response = CallCredentials(
        url="wss://livekit.test/out", token="tok-out", room="room-out", peer_id=200,
    )

    reply = transport.quick_exchange(
        peer_id=200,
        send=ControlMessage(kind=Kind.EXPECT_CLIENT, body={}),
        timeout=0.1,
    )
    assert reply is None
    out_session = next(s for s in FakeLiveKitSession.instances if s.identity == "coordinator-out-200")
    assert out_session.stopped


def test_stop_stops_both_clients(transport):
    transport.listen(lambda _: None)
    transport.stop()
    assert transport._clients["jwt-listen"].stopped
    assert transport._clients["jwt-dispatch"].stopped


def test_event_with_missing_peer_id_is_ignored(transport):
    received: list = []
    transport.listen(lambda call: received.append(call))
    listen_client = transport._clients["jwt-listen"]
    creds = CallCredentials(url="wss://x", token="t", room="r", peer_id=None)
    listen_client.listener(IncomingCallEvent(credentials=creds, peer_id=None))
    # Give the worker thread a beat — there shouldn't be one because the path bails out.
    import time
    time.sleep(0.1)
    assert received == []


# --- inbound semaphore: bounds concurrent sessions, releases on every exit ---


def _push_call(client, peer_id: int) -> None:
    creds = CallCredentials(
        url=f"wss://lk/{peer_id}", token=f"tok-{peer_id}", room=f"r-{peer_id}",
        peer_id=peer_id,
    )
    client.listener(IncomingCallEvent(credentials=creds, peer_id=peer_id))


def test_inbound_semaphore_caps_concurrent_sessions(transport):
    """Bale's push system redelivers each notification 5-10×; without a
    cap the livekit-ffi runtime stalls every per-session callback past
    the HELLO recv timeout. The cap (3) drops further pushes immediately
    so the in-flight sessions can complete cleanly.

    This test holds 4 incoming calls inside the handler concurrently and
    asserts only 3 were ever admitted."""
    import time as _time

    from baleobala.coordinator.bale_transport import MAX_CONCURRENT_INBOUND_SESSIONS

    release = threading.Event()
    admitted = []
    admit_lock = threading.Lock()

    def blocking_handler(call):
        with admit_lock:
            admitted.append(call.peer_id)
        release.wait(timeout=2.0)

    transport.listen(blocking_handler)
    listen_client = transport._clients["jwt-listen"]

    # Fire 1 more push than capacity. The extra one must be dropped.
    for peer_id in range(100, 100 + MAX_CONCURRENT_INBOUND_SESSIONS + 1):
        _push_call(listen_client, peer_id)

    # Give dispatch threads a moment to enter the handler.
    deadline = _time.time() + 1.0
    while _time.time() < deadline and len(admitted) < MAX_CONCURRENT_INBOUND_SESSIONS:
        _time.sleep(0.02)

    assert len(admitted) == MAX_CONCURRENT_INBOUND_SESSIONS, (
        f"expected exactly {MAX_CONCURRENT_INBOUND_SESSIONS} admitted, "
        f"got {len(admitted)} ({admitted}). The 4th push should have been "
        "dropped by the bounded semaphore."
    )

    release.set()


def test_inbound_semaphore_releases_after_handler_returns(transport):
    """After the handler returns, the slot is freed so the next push
    can proceed. The cap is on concurrency, not lifetime."""
    import time as _time

    from baleobala.coordinator.bale_transport import MAX_CONCURRENT_INBOUND_SESSIONS

    handled = threading.Event()
    handled_count = {"n": 0}

    def handler(call):
        handled_count["n"] += 1
        handled.set()

    transport.listen(handler)
    listen_client = transport._clients["jwt-listen"]

    # Push 2× capacity, one at a time, waiting for each to be handled.
    for i in range(MAX_CONCURRENT_INBOUND_SESSIONS * 2):
        handled.clear()
        _push_call(listen_client, 200 + i)
        assert handled.wait(timeout=2.0), f"push {i} was not handled"
    assert handled_count["n"] == MAX_CONCURRENT_INBOUND_SESSIONS * 2


def test_inbound_semaphore_releases_when_handler_raises(transport):
    """If the handler crashes the slot must still be freed; otherwise
    each handler crash would permanently leak one inbound slot until
    the relay was restarted."""
    import time as _time

    from baleobala.coordinator.bale_transport import MAX_CONCURRENT_INBOUND_SESSIONS

    crashes = {"n": 0}

    def crashing_handler(call):
        crashes["n"] += 1
        raise RuntimeError("handler boom")

    transport.listen(crashing_handler)
    listen_client = transport._clients["jwt-listen"]

    # Drive more than capacity sequentially. After each push, wait for
    # the call's session to be torn down (hangup ran → finally is about
    # to release the slot). The next iteration confirms the slot is
    # actually free; if it weren't, the new session would never be made.
    target = MAX_CONCURRENT_INBOUND_SESSIONS + 2
    for i in range(target):
        before = len(FakeLiveKitSession.instances)
        _push_call(listen_client, 300 + i)
        deadline = _time.time() + 2.0
        while _time.time() < deadline and len(FakeLiveKitSession.instances) <= before:
            _time.sleep(0.01)
        # If the slot leaked, the next push would silently drop and
        # no new session would be created.
        assert len(FakeLiveKitSession.instances) > before, (
            f"push {i}: no new session created — semaphore did not "
            f"release after prior crashes"
        )
        # Wait for the dispatch thread's finally to actually release.
        new_session = FakeLiveKitSession.instances[-1]
        deadline = _time.time() + 2.0
        while _time.time() < deadline and not new_session.stopped:
            _time.sleep(0.01)
        assert new_session.stopped, f"push {i}: handler exception did not lead to hangup"
    assert crashes["n"] == target


def test_inbound_semaphore_releases_when_no_listener_registered(monkeypatch):
    """Bale-push events that arrive before `listen()` is called still
    spin up a LiveKitSession; the slot they consume must be released
    when we discover there's no handler and hang up."""
    from baleobala.coordinator import bale_transport as bt
    from baleobala.coordinator.bale_transport import MAX_CONCURRENT_INBOUND_SESSIONS

    monkeypatch.setattr(bt, "LiveKitSession", FakeLiveKitSession)
    clients: dict[str, FakeBaleApiClient] = {}

    def factory(*, jwt: str, ws_tls_config=None):
        c = FakeBaleApiClient(jwt=jwt, ws_tls_config=ws_tls_config)
        clients[jwt] = c
        return c

    t = bt.BaleCoordinatorTransport(
        listen_jwt="jwt-l", dispatch_jwt="jwt-d", client_factory=factory,
    )
    # We need a listener to be present so _on_listen_event runs at all,
    # but we'll race the no-listener branch by clearing it before each push.
    t.listen(lambda _: None)
    t._on_call = None  # simulate no-listener

    for i in range(MAX_CONCURRENT_INBOUND_SESSIONS + 2):
        _push_call(clients["jwt-l"], 400 + i)

    # All sessions should be torn down (hangup → stop) because there was
    # no listener; the cap must NOT have stuck any of them in limbo.
    assert all(s.stopped for s in FakeLiveKitSession.instances), (
        "no-listener path leaked a session past hangup"
    )
    assert len(FakeLiveKitSession.instances) == MAX_CONCURRENT_INBOUND_SESSIONS + 2, (
        "fewer sessions than push events — semaphore did not release "
        "between events with no listener"
    )
