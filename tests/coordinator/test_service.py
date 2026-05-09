from __future__ import annotations

import queue
from typing import Callable

import pytest

from baleobala.coordinator.protocol import (
    ControlMessage,
    DenyReason,
    Kind,
    encode,
    decode,
    make_assign,
    make_expect_client,
    make_heartbeat,
    make_hello,
    make_offline,
    make_online,
    make_released,
)
from baleobala.coordinator.auth import generate_secret, sign
from baleobala.coordinator.registry import RelayRegistry, RelaySlot
from baleobala.coordinator.service import CoordinatorService, ServiceConfig
from baleobala.coordinator.transport import IncomingCall


class FakeIncomingCall:
    def __init__(self, peer_id: int, inbox: list[ControlMessage]) -> None:
        self.peer_id = peer_id
        self._inbox = queue.Queue()
        for msg in inbox:
            self._inbox.put(msg)
        self.sent: list[ControlMessage] = []
        self.hung_up = False

    def recv(self, *, timeout: float) -> ControlMessage | None:
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def send(self, msg: ControlMessage) -> None:
        self.sent.append(msg)

    def hangup(self) -> None:
        self.hung_up = True


class FakeTransport:
    def __init__(self, ack_for_relay: dict[int, ControlMessage] | None = None) -> None:
        self._on_call: Callable[[IncomingCall], None] | None = None
        self.outbound: list[tuple[int, ControlMessage]] = []
        self.ack_for_relay = ack_for_relay or {}
        self.stopped = False

    def listen(self, on_call):
        self._on_call = on_call

    def quick_exchange(self, *, peer_id, send, timeout):
        self.outbound.append((peer_id, send))
        return self.ack_for_relay.get(peer_id)

    def stop(self):
        self.stopped = True

    # test helper
    def deliver(self, call: FakeIncomingCall) -> None:
        assert self._on_call is not None, "service.start() not called"
        self._on_call(call)


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def registry():
    return RelayRegistry()


@pytest.fixture
def service(transport, registry):
    svc = CoordinatorService(
        transport=transport,
        registry=registry,
        config=ServiceConfig(hello_timeout=1.0, expect_timeout=1.0, session_expires_secs=30),
        new_session_id=lambda: "session-fixed",
    )
    svc.start()
    return svc


def test_service_listen_starts_transport(service, transport):
    assert transport._on_call is not None


def test_online_registers_relay(service, transport, registry):
    call = FakeIncomingCall(peer_id=200, inbox=[
        make_online(relay_id="r1", peer_id=200, capacity=2)
    ])
    transport.deliver(call)
    assert call.hung_up
    relay = registry.get_relay("r1")
    assert relay is not None
    assert relay.peer_id == 200 and relay.capacity == 2


def test_hello_assigns_relay_and_instructs(service, transport, registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport.ack_for_relay = {200: ControlMessage(kind=Kind.EXPECT_ACK, body={})}

    call = FakeIncomingCall(peer_id=42, inbox=[make_hello(client_id="dev-1")])
    transport.deliver(call)

    # Client received ASSIGN
    assert len(call.sent) == 1
    assert call.sent[0].kind == Kind.ASSIGN
    assert call.sent[0].get("relay_peer_id") == 200
    assert call.sent[0].get("session_id") == "session-fixed"
    assert call.hung_up

    # Coordinator instructed the relay
    assert len(transport.outbound) == 1
    target_peer_id, expect_msg = transport.outbound[0]
    assert target_peer_id == 200
    assert expect_msg.kind == Kind.EXPECT_CLIENT
    assert expect_msg.get("client_peer_id") == 42

    # Session is reserved
    relay = registry.get_relay("r1")
    assert relay is not None and relay.in_use == [42]


def test_hello_denies_when_no_capacity(service, transport, registry):
    # No relays registered → DENY
    call = FakeIncomingCall(peer_id=42, inbox=[make_hello(client_id="dev-1")])
    transport.deliver(call)
    assert len(call.sent) == 1
    assert call.sent[0].kind == Kind.DENY
    assert call.sent[0].get("reason") == DenyReason.NO_CAPACITY
    assert transport.outbound == []


def test_hello_rolls_back_session_if_relay_does_not_ack(service, transport, registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport.ack_for_relay = {}  # no ack from relay

    call = FakeIncomingCall(peer_id=42, inbox=[make_hello(client_id="dev-1")])
    transport.deliver(call)

    # Client got ASSIGN but session was rolled back
    relay = registry.get_relay("r1")
    assert relay is not None and relay.is_free()
    assert registry.get_session("session-fixed") is None


def test_released_frees_session(service, transport, registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )

    call = FakeIncomingCall(peer_id=200, inbox=[
        make_released(relay_id="r1", session_id="s1")
    ])
    transport.deliver(call)
    assert registry.get_session("s1") is None
    relay = registry.get_relay("r1")
    assert relay is not None and relay.is_free()


def test_heartbeat_updates_in_use(service, transport, registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=2))
    call = FakeIncomingCall(peer_id=200, inbox=[
        make_heartbeat(relay_id="r1", in_use=[42, 43])
    ])
    transport.deliver(call)
    relay = registry.get_relay("r1")
    assert relay is not None
    assert relay.in_use == [42, 43]


def test_offline_removes_relay(service, transport, registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    call = FakeIncomingCall(peer_id=200, inbox=[
        make_offline(relay_id="r1", reason="shutdown")
    ])
    transport.deliver(call)
    assert registry.get_relay("r1") is None


def test_unexpected_kind_is_denied(service, transport):
    call = FakeIncomingCall(peer_id=42, inbox=[
        ControlMessage(kind="WEIRD", body={})
    ])
    transport.deliver(call)
    assert any(s.kind == Kind.DENY for s in call.sent)


def test_no_message_hangs_up_quietly(service, transport):
    call = FakeIncomingCall(peer_id=42, inbox=[])  # empty -> recv returns None on timeout
    transport.deliver(call)
    assert call.hung_up
    assert call.sent == []


def test_load_balances_across_relays(service, transport, registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=2))
    registry.register(RelaySlot(relay_id="r2", peer_id=201, capacity=2))
    transport.ack_for_relay = {
        200: ControlMessage(kind=Kind.EXPECT_ACK, body={}),
        201: ControlMessage(kind=Kind.EXPECT_ACK, body={}),
    }

    # Each call uses a unique session id (use a counter) so reservation succeeds
    counter = {"n": 0}

    def session_id():
        counter["n"] += 1
        return f"s-{counter['n']}"

    service._new_session_id = session_id

    for client in (42, 43, 44):
        call = FakeIncomingCall(peer_id=client, inbox=[make_hello(client_id=str(client))])
        transport.deliver(call)

    r1 = registry.get_relay("r1")
    r2 = registry.get_relay("r2")
    # 3 clients across 2 relays → first relay gets 2, second gets 1 (deterministic by tie-break)
    counts = sorted([len(r1.in_use), len(r2.in_use)])
    assert counts == [1, 2]


def test_heartbeat_from_unknown_relay_uses_peer_id_field_not_call_peer_id(
    service, transport, registry
):
    """When a HEARTBEAT arrives for an unknown relay, re-registration must use
    the peer_id from the message body — `call.peer_id` is the callee's
    (coordinator's own) peer_id under Bale push semantics, so it would
    register the coordinator itself as a relay."""
    # The fake sets call.peer_id to 999 (simulating coordinator's own peer_id);
    # the heartbeat body carries the relay's actual peer_id 4242.
    call = FakeIncomingCall(peer_id=999, inbox=[
        make_heartbeat(relay_id="r1", in_use=[42], peer_id=4242, capacity=3)
    ])
    transport.deliver(call)
    relay = registry.get_relay("r1")
    assert relay is not None
    assert relay.peer_id == 4242
    assert relay.capacity == 3
    assert relay.in_use == [42]


def test_hello_uses_client_peer_id_field_not_call_peer_id(service, transport, registry):
    """HELLO must take client_peer_id from the message body so the coordinator
    can identify the caller. `call.peer_id` is the callee's peer_id under
    Bale push semantics and would route EXPECT_CLIENT to the coordinator
    itself."""
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport.ack_for_relay = {200: ControlMessage(kind=Kind.EXPECT_ACK, body={})}

    # call.peer_id = 999 (would be coordinator's own); HELLO body has true caller=42.
    call = FakeIncomingCall(peer_id=999, inbox=[
        make_hello(client_id="dev-1", client_peer_id=42)
    ])
    transport.deliver(call)

    target_peer_id, expect_msg = transport.outbound[0]
    assert target_peer_id == 200
    assert expect_msg.get("client_peer_id") == 42

    relay = registry.get_relay("r1")
    assert relay is not None and relay.in_use == [42]


def test_hello_falls_back_to_call_peer_id_when_field_missing(service, transport, registry):
    """Legacy clients that don't include client_peer_id in HELLO still work
    via the fallback to call.peer_id (preserving backward compatibility)."""
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport.ack_for_relay = {200: ControlMessage(kind=Kind.EXPECT_ACK, body={})}

    call = FakeIncomingCall(peer_id=42, inbox=[make_hello(client_id="legacy")])
    transport.deliver(call)
    target_peer_id, expect_msg = transport.outbound[0]
    assert expect_msg.get("client_peer_id") == 42


def test_round_trip_through_encode_decode_in_handler(service, transport, registry):
    """Sanity: messages built with factories survive encode/decode and the
    service still processes them correctly."""
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport.ack_for_relay = {200: ControlMessage(kind=Kind.EXPECT_ACK, body={})}

    raw_hello = encode(make_hello(client_id="dev-1"))
    msg = decode(raw_hello)

    call = FakeIncomingCall(peer_id=42, inbox=[msg])
    transport.deliver(call)
    assert call.sent[0].kind == Kind.ASSIGN


def test_expect_client_dispatched_before_assign(service, transport, registry):
    """Regression for c318c14 (dial-race fix).

    The relay's EXPECT_CLIENT must be in flight BEFORE the client receives
    ASSIGN. With the previous order, the device dialed the relay within
    100-300 ms of receiving ASSIGN but the relay's probe set
    `_account_active=True` and skip-probe'd the dispatch when it finally
    arrived, so the device timed out as an unknown caller.

    This test enforces the order at the point each side observes its
    message: when `transport.quick_exchange` is invoked (relay-side
    dispatch), the client must not yet have been sent ASSIGN."""
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))

    sent_at_dispatch: list[list] = []

    class OrderingTransport(FakeTransport):
        def __init__(self, ack: ControlMessage, observed_call: "FakeIncomingCall") -> None:
            super().__init__(ack_for_relay={200: ack})
            self._observed = observed_call

        def quick_exchange(self, *, peer_id, send, timeout):
            # Snapshot what the client has been sent at the moment we
            # invoke the relay-side dispatch.
            sent_at_dispatch.append(list(self._observed.sent))
            return super().quick_exchange(peer_id=peer_id, send=send, timeout=timeout)

    call = FakeIncomingCall(peer_id=42, inbox=[make_hello(client_id="dev-1")])
    ordering_transport = OrderingTransport(
        ack=ControlMessage(kind=Kind.EXPECT_ACK, body={}),
        observed_call=call,
    )
    ordering_service = CoordinatorService(
        transport=ordering_transport,
        registry=registry,
        config=ServiceConfig(hello_timeout=1.0, expect_timeout=1.0),
        new_session_id=lambda: "session-fixed",
    )
    ordering_service.start()
    ordering_transport.deliver(call)

    assert sent_at_dispatch, "quick_exchange was never invoked — EXPECT_CLIENT was not dispatched"
    assert sent_at_dispatch[0] == [], (
        "ASSIGN was sent to the client BEFORE EXPECT_CLIENT reached the relay; "
        "this re-introduces the dial-race fixed in c318c14"
    )
    # And the final state is correct: client got ASSIGN, relay got EXPECT_CLIENT.
    assert call.sent[0].kind == Kind.ASSIGN
    assert ordering_transport.outbound[0][1].kind == Kind.EXPECT_CLIENT


def test_relay_no_ack_results_in_deny_not_assign(service, transport, registry):
    """Regression for c318c14 + the rollback half of _instruct_relay.

    When the relay does not return EXPECT_ACK, the client must receive
    DENY — never ASSIGN — otherwise the device would dial a relay that
    has not registered it, hit the unknown-caller branch, and time out."""
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport.ack_for_relay = {}  # relay never acks

    call = FakeIncomingCall(peer_id=42, inbox=[make_hello(client_id="dev-1")])
    transport.deliver(call)

    assert call.sent, "client received nothing"
    assert call.sent[0].kind == Kind.DENY, (
        f"expected DENY when relay does not ack, got {call.sent[0].kind}"
    )
    # And the session was rolled back (already covered by the existing
    # rollback test, but keep the assertion local so this regression
    # check stands alone).
    assert registry.get_session("session-fixed") is None


# ---- A6: relay authentication -----------------------------------------------


def _authed_registry(*relay_ids_with_secrets: tuple[str, str]) -> RelayRegistry:
    """Build a registry pre-loaded with relay secrets."""
    secrets = {rid: sec for rid, sec in relay_ids_with_secrets}
    return RelayRegistry(relay_secrets=secrets)


def test_online_with_valid_sig_is_accepted():
    secret = generate_secret()
    registry = _authed_registry(("r1", secret))
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    msg = make_online(relay_id="r1", peer_id=200, capacity=1, secret=secret)
    call = FakeIncomingCall(peer_id=200, inbox=[msg])
    transport.deliver(call)
    assert registry.get_relay("r1") is not None


def test_online_with_wrong_sig_is_rejected():
    real_secret = generate_secret()
    wrong_secret = generate_secret()
    registry = _authed_registry(("r1", real_secret))
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    msg = make_online(relay_id="r1", peer_id=200, capacity=1, secret=wrong_secret)
    call = FakeIncomingCall(peer_id=200, inbox=[msg])
    transport.deliver(call)
    # Message dropped — relay not registered.
    assert registry.get_relay("r1") is None


def test_heartbeat_with_valid_sig_is_accepted():
    secret = generate_secret()
    registry = _authed_registry(("r1", secret))
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=2))
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    msg = make_heartbeat(relay_id="r1", in_use=[42], secret=secret)
    call = FakeIncomingCall(peer_id=200, inbox=[msg])
    transport.deliver(call)
    relay = registry.get_relay("r1")
    assert relay is not None and relay.in_use == [42]


def test_heartbeat_with_missing_sig_is_rejected_when_enrolled():
    secret = generate_secret()
    registry = _authed_registry(("r1", secret))
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=2))
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    # No secret → no sig field
    msg = make_heartbeat(relay_id="r1", in_use=[42])
    call = FakeIncomingCall(peer_id=200, inbox=[msg])
    transport.deliver(call)
    relay = registry.get_relay("r1")
    # in_use should NOT have been updated
    assert relay is not None and relay.in_use == []


def test_offline_with_valid_sig_removes_relay():
    secret = generate_secret()
    registry = _authed_registry(("r1", secret))
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    msg = make_offline(relay_id="r1", reason="shutdown", secret=secret)
    call = FakeIncomingCall(peer_id=200, inbox=[msg])
    transport.deliver(call)
    assert registry.get_relay("r1") is None


def test_offline_with_wrong_sig_does_not_remove_relay():
    real_secret = generate_secret()
    wrong_secret = generate_secret()
    registry = _authed_registry(("r1", real_secret))
    registry.register(RelaySlot(relay_id="r1", peer_id=200, capacity=1))
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    msg = make_offline(relay_id="r1", reason="shutdown", secret=wrong_secret)
    call = FakeIncomingCall(peer_id=200, inbox=[msg])
    transport.deliver(call)
    assert registry.get_relay("r1") is not None, "relay was removed despite bad sig"


def test_unenrolled_relay_is_accepted_with_warning(caplog):
    """Relays without a registered secret are still accepted for backwards
    compat; a WARNING is emitted so the gap is visible in the journal."""
    import logging
    registry = RelayRegistry()  # no secrets loaded
    transport = FakeTransport()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    msg = make_online(relay_id="legacy-r", peer_id=300, capacity=1)
    call = FakeIncomingCall(peer_id=300, inbox=[msg])
    with caplog.at_level(logging.WARNING):
        transport.deliver(call)
    assert registry.get_relay("legacy-r") is not None
    assert any("no enrolled secret" in r.message for r in caplog.records)


def test_auth_sign_verify_round_trip():
    """sign() + verify() round-trip at the same minute."""
    secret = generate_secret()
    sig = sign(secret, "r1", Kind.ONLINE)
    from baleobala.coordinator.auth import verify
    assert verify(secret, "r1", Kind.ONLINE, sig)


def test_auth_verify_rejects_different_relay_id():
    secret = generate_secret()
    sig = sign(secret, "r1", Kind.ONLINE)
    from baleobala.coordinator.auth import verify
    assert not verify(secret, "r2", Kind.ONLINE, sig)


def test_auth_verify_rejects_wrong_kind():
    secret = generate_secret()
    sig = sign(secret, "r1", Kind.ONLINE)
    from baleobala.coordinator.auth import verify
    assert not verify(secret, "r1", Kind.HEARTBEAT, sig)
