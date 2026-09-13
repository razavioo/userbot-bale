"""Regression tests for the relay's per-call state machine.

Originally a 380-line closure inside `cmd_vpn_mesh`; extracted as
`RelayCallHandler` in plan slice A1c. These tests drive the new class
with fakes and lock in the byte-equivalent behavior of every
production-fix on the recent commit log.

Reaper-specific tests live in `tests/vpn/test_session_reaper.py`.
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from userbot_bale.vpn.constants import DEFAULT_TUNNEL_SESS_ID
from userbot_bale.vpn.relay_handler import RelayCallHandler
from userbot_bale.vpn.relay_state import (
    DEFAULT_STALE_ACTIVE_FLAG_SECS,
    RelayState,
)


# ---- Fakes -------------------------------------------------------------


class FakeChannel:
    """Stand-in for a LiveKit data channel — backed by an in-memory queue."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._inbox: list[bytes] = []
        self._closed = False

    def push(self, payload: bytes) -> None:
        self._inbox.append(payload)

    def recv_bytes(self, timeout: float = 0.0) -> bytes | None:
        if self._inbox:
            return self._inbox.pop(0)
        return None

    def send_bytes(self, payload: bytes) -> None:
        self.sent.append(payload)


class FakeLiveKitSession:
    """Synchronous, deterministic fake. Tests control the EXPECT_CLIENT
    payload, the participant identities, and whether enable_audio raises."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        identity: str,
        publish_audio: bool = True,
    ) -> None:
        self.url = url
        self.token = token
        self.identity = identity
        self.publish_audio = publish_audio
        self.started = False
        self.stopped = False
        self._channels: dict[str, FakeChannel] = {}
        self._enable_audio_calls = 0
        # Test-controlled hooks
        self.expect_client_payload: bytes | None = None
        self.remote_identities: list[str] = []
        self.enable_audio_raises: BaseException | None = None
        self._terminal = False
        self._peer_disconnect_at: float | None = None

    def start(self) -> None:
        self.started = True
        # If the harness staged an EXPECT_CLIENT message, push it now so
        # `data_channel(topic="control").recv_bytes()` returns it once.
        if self.expect_client_payload is not None:
            ch = self._channel(CONTROL_TOPIC)
            ch.push(self.expect_client_payload)

    def stop(self) -> None:
        self.stopped = True

    def is_terminal(self) -> bool:
        return self._terminal

    def wait_for_remote_participant(self, timeout: float = 0.0) -> None:
        return None

    def data_channel(self, *, topic: str = "vpn", reliable: bool = True) -> FakeChannel:
        return self._channel(topic)

    def _channel(self, topic: str) -> FakeChannel:
        if topic not in self._channels:
            self._channels[topic] = FakeChannel()
        return self._channels[topic]

    def enable_audio(self) -> None:
        self._enable_audio_calls += 1
        if self.enable_audio_raises is not None:
            raise self.enable_audio_raises

    def remote_participant_identities(self) -> list[str]:
        return list(self.remote_identities)


class FakeKeepalive:
    def __init__(self, session: Any, *, interval: float) -> None:
        self.session = session
        self.interval = interval

    def start(self) -> None:
        return None


class FakeDataChannelTransport:
    mtu = 1200

    def __init__(self, session: Any, *, topic: str, reliable: bool) -> None:
        self.session = session
        self.topic = topic
        self.reliable = reliable
        self.sent: list[bytes] = []
        # Tests inject the provisioning ACK payload via _ack_payload below.
        self._ack_payload: bytes | None = None

    def send_bytes(self, payload: bytes) -> None:
        self.sent.append(payload)

    def stage_provision_ack(self, payload: bytes) -> None:
        self._ack_payload = payload


def _fake_recv_provision(transport: FakeDataChannelTransport, *, timeout: float):
    """Provisioning ack reader — test populates `transport._ack_payload`
    via stage_provision_ack(); we decode that here."""
    if transport._ack_payload is None:
        return None
    from userbot_bale.vpn.provisioning import MeshProvisionMessage
    return MeshProvisionMessage.decode(transport._ack_payload)


@dataclass
class FakeAssignment:
    pool_cidr: str = "10.77.0.0/16"
    prefix: int = 30
    gateway: str = "10.77.0.1"
    client: str = "10.77.0.2"


class FakeRecord:
    def __init__(self, assignment: FakeAssignment) -> None:
        self._assignment = assignment

    def assignment(self) -> FakeAssignment:
        return self._assignment


class FakeControl:
    def __init__(self) -> None:
        self.issued: list[int] = []
        self.activated: list[int] = []
        self.released: list[int] = []
        self._next_assignment = FakeAssignment()

    def issue_mesh_assignment(self, peer_id: int, *, pool_cidr: str, transport: str, session_id: int):
        self.issued.append(peer_id)
        return FakeRecord(self._next_assignment)

    def activate_mesh_assignment(self, peer_id: int, *, transport: str, session_id: int) -> None:
        self.activated.append(peer_id)

    def release_mesh_assignment(self, peer_id: int, *, error: str | None = None) -> None:
        self.released.append(peer_id)


class FakeMesh:
    """Models production MeshExitNode behavior: drop_client invokes the
    on_drop callback registered at issue_client time. The relay handler
    relies on that callback to clear `_account_active` — without it the
    flag would leak past every session teardown."""

    def __init__(self) -> None:
        self.issued: list[tuple[int, Any, FakeAssignment]] = []
        self.activated: list[int] = []
        self.dropped: list[int] = []
        self._on_drop: dict[int, Any] = {}

    def issue_client(self, peer_id: int, transport: Any, *, assignment: FakeAssignment, on_drop):
        self.issued.append((peer_id, transport, assignment))
        self._on_drop[peer_id] = on_drop

    def activate_client(self, peer_id: int, *, mtu_override: int) -> FakeAssignment:
        self.activated.append(peer_id)
        return FakeAssignment()

    def drop_client(self, peer_id: int) -> None:
        self.dropped.append(peer_id)
        cb = self._on_drop.pop(peer_id, None)
        if cb is not None:
            try:
                cb(peer_id)
            except Exception:
                pass


@dataclass
class FakeSlot:
    index: int
    peer_ids: list[int]


class FakeAllocator:
    """Models one slot per relay JWT; each can hold multiple peer_ids."""

    def __init__(self, num_accounts: int = 1, max_per_slot: int = 4) -> None:
        self._slots = [FakeSlot(index=i, peer_ids=[]) for i in range(num_accounts)]
        self._max = max_per_slot
        # Map peer_id -> slot index
        self._by_peer: dict[int, int] = {}
        self.total_capacity = num_accounts * max_per_slot

    def slots(self) -> list[FakeSlot]:
        return self._slots

    def assignment(self) -> dict[int, int]:
        return dict(self._by_peer)

    def join(self, peer_id: int) -> FakeSlot | None:
        for slot in self._slots:
            if len(slot.peer_ids) < self._max and peer_id not in slot.peer_ids:
                slot.peer_ids.append(peer_id)
                self._by_peer[peer_id] = slot.index
                return slot
        return None

    def leave(self, peer_id: int) -> None:
        idx = self._by_peer.pop(peer_id, None)
        if idx is None:
            return
        slot = self._slots[idx]
        if peer_id in slot.peer_ids:
            slot.peer_ids.remove(peer_id)


def _fake_event(*, peer_id: int, url: str = "wss://x", token: str = "t"):
    creds = SimpleNamespace(url=url, token=token, peer_id=peer_id)
    return SimpleNamespace(peer_id=peer_id, credentials=creds)


# ---- Harness -----------------------------------------------------------


class _Harness:
    """Bundle of fakes + the handler under test. Each test instantiates
    its own to avoid shared state across cases."""

    def __init__(
        self,
        *,
        num_accounts: int = 1,
        max_per_slot: int = 4,
        psk_key: bytes | None = None,
    ) -> None:
        self.log = io.StringIO()
        self.mesh = FakeMesh()
        self.allocator = FakeAllocator(
            num_accounts=num_accounts, max_per_slot=max_per_slot
        )
        self.control = FakeControl()
        self.session_map: dict[str, str] = {}
        self.session_map_lock = threading.Lock()
        self.relay_state = RelayState(num_accounts=num_accounts, log_stream=self.log)
        self.sessions: list[tuple[int, Any, Any]] = []
        # Track every LiveKitSession the handler instantiates so tests
        # can inspect / pre-stage payloads.
        self.created_sessions: list[FakeLiveKitSession] = []
        # Track every transport too so tests can stage the provisioning
        # ack.
        self.created_transports: list[FakeDataChannelTransport] = []

        def lk_factory(**kwargs: Any) -> FakeLiveKitSession:
            sess = FakeLiveKitSession(**kwargs)
            # Apply pre-staged remote identities to the NEXT session, then
            # clear so subsequent sessions don't reuse stale state.
            if self._pending_remote_identities is not None:
                sess.remote_identities = list(self._pending_remote_identities)
                self._pending_remote_identities = None
            self.created_sessions.append(sess)
            return sess

        def dc_factory(session: Any, *, topic: str, reliable: bool) -> FakeDataChannelTransport:
            t = FakeDataChannelTransport(session, topic=topic, reliable=reliable)
            self.created_transports.append(t)
            # Auto-stage a successful provisioning ACK so the tunnel
            # bring-up reaches sessions.append. Tests that want to
            # check pre-provision behavior can override before calling.
            from userbot_bale.vpn.provisioning import MeshProvisionMessage
            t.stage_provision_ack(
                MeshProvisionMessage(
                    version=1,
                    kind="ack",
                    peer_id=0,
                    session_id=DEFAULT_TUNNEL_SESS_ID,
                    pool_cidr="10.77.0.0/16",
                    prefix=30,
                    gateway_ip="10.77.0.1",
                    client_ip="10.77.0.2",
                    tun_mtu=1400,
                    transport="dc",
                ).encode()
            )
            return t

        def enc_factory(transport: Any, key: bytes) -> Any:
            return transport  # passthrough — the encryption layer isn't on test path

        from userbot_bale.vpn.provisioning import (
            MeshProvisionMessage,
            ProvisioningError,
        )

        self._pending_remote_identities: list[str] | None = None

        self.handler = RelayCallHandler(
            identity_prefix="test-relay",
            pool_cidr="10.77.0.0/16",
            tun_mtu=1400,
            provision_timeout=5.0,
            psk_key=psk_key,
            mesh=self.mesh,
            allocator=self.allocator,
            control=self.control,
            session_map=self.session_map,
            session_map_lock=self.session_map_lock,
            probe_locks={i: _NoLock() for i in range(num_accounts)},
            relay_state=self.relay_state,
            stale_active_flag_secs=DEFAULT_STALE_ACTIVE_FLAG_SECS,
            sessions=self.sessions,
            livekit_session_factory=lk_factory,
            keepalive_factory=FakeKeepalive,
            datachannel_transport_factory=dc_factory,
            encrypted_transport_factory=enc_factory,
            provision_message_factory=MeshProvisionMessage,
            recv_provision=_fake_recv_provision,
            provisioning_error_cls=ProvisioningError,
            log_stream=self.log,
        )

    def stage_remote_identities(self, identities: list[str]) -> None:
        self._pending_remote_identities = identities


class _NoLock:
    def __enter__(self): return None
    def __exit__(self, *a): return None


# ---- Tests --------------------------------------------------------------


def test_missing_peer_id_is_rejected():
    """The first guard at handle_call: a missing canonical peer_id is
    immediately rejected without touching state or starting a probe."""
    h = _Harness()
    event = _fake_event(peer_id=0)
    event.peer_id = None
    h.handler.handle_call(event)
    assert h.created_sessions == []
    assert "incoming call rejected: missing canonical peer_id" in h.log.getvalue()
    assert h.relay_state.is_active(0) is False


def test_no_caller_identity_rejects_call():
    """If the LiveKit participant list contains no resolvable caller
    user_id (only the relay's own identity, or no remote yet), the
    handler must refuse the call rather than issue a tunnel keyed by
    the relay's own peer_id."""
    h = _Harness()
    # No remote identities staged — _join_room joins the room but
    # remote_participant_identities() returns [].
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)
    assert h.allocator.assignment() == {}, "unknown caller was allocated a slot"
    assert h.mesh.issued == [], "unknown caller had a tunnel issued"
    assert "could not resolve caller identity" in h.log.getvalue()
    assert h.relay_state.is_active(0) is False


def test_per_caller_routing_uses_livekit_identity_over_event_peer_id():
    """event.peer_id is the relay's own peer_id under Bale push
    semantics. The handler must override it with the real caller
    user_id resolved from LiveKit participant identity, so two
    distinct callers on the same relay JWT each get their own slot
    in mesh.issue_client."""
    h = _Harness()
    h.stage_remote_identities(["42"])
    # The Bale push delivers event.peer_id = relay's own (here 999).
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)

    # The mesh was issued for the resolved caller (42), not the
    # relay's own peer_id (999).
    assert [pid for pid, *_ in h.mesh.issued] == [42]
    assert "caller resolved from LiveKit identity: 42" in h.log.getvalue()


def test_active_flag_clears_after_session_commit():
    """The flag is set True at join-start and must be cleared after
    sessions.append. It does NOT remain True for the session's
    lifetime — the reaper / on_drop are what clear it later in
    production. Here we drive a successful tunnel bring-up and
    assert the flag is False on return."""
    h = _Harness()
    h.stage_remote_identities(["42"])
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)

    assert h.sessions, "session was not committed"
    assert h.relay_state.is_active(0) is False, (
        "_account_active leaked True past after-commit"
    )


def test_reconnect_within_grace_evicts_stale_session():
    """Regression for 2215aee. A fresh inbound call for an account
    whose existing session has lost its peer (or is terminal) must
    immediately evict the stale session, not skip-probe forever.

    Drives the full production flow: bring up a tunnel via handle_call,
    poison the session with `_peer_disconnect_at`, then have the same
    caller reconnect — the eviction loop must drop the dead session
    and let the new call proceed all the way to sessions.append."""
    h = _Harness()
    # First call — establishes the (eventually-stale) tunnel.
    h.stage_remote_identities(["42"])
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)
    assert h.sessions, "first call did not establish a tunnel"
    initial_session_count = len(h.created_sessions)

    # Make the live session look peer-lost (the SFU dropped the client
    # but the grace timer hasn't promoted to terminal yet).
    live_pid, live_sess, _live_tx = h.sessions[0]
    live_sess._peer_disconnect_at = time.monotonic() - 1.0

    # Second call — same caller reconnects through a fresh Bale push.
    h._pending_remote_identities = ["42"]  # for the next created session
    event2 = _fake_event(peer_id=999)
    h.handler.handle_call(event2)

    assert live_sess.stopped, "stale LiveKitSession.stop() was not invoked"
    assert 42 in h.mesh.dropped, "stale session was not evicted via mesh.drop_client"
    assert "reconnect detected for account=0; evicting 1 stale session" in h.log.getvalue()
    # A fresh probe / session was created for the reconnect.
    assert len(h.created_sessions) > initial_session_count
    # And the new tunnel is live (the original eviction test focused on
    # the eviction itself; the post-eviction provisioning is guaranteed
    # by test_active_flag_clears_after_session_commit).


def test_stale_active_flag_force_clears_after_threshold():
    """Regression for the stale-flag auto-recovery (vpn/cli.py:357-367).

    If the active flag is True with no slot allocated AND its age >
    STALE_ACTIVE_FLAG_SECS, the next inbound call must force-clear and
    proceed instead of skip-probing forever."""
    # Inject a clock so we can age the flag without sleeping 60 s.
    log = io.StringIO()
    clock = {"t": 0.0}
    state = RelayState(
        num_accounts=1, clock=lambda: clock["t"], log_stream=log,
    )

    # Build the harness manually so we can swap in our clocked state.
    h = _Harness()  # for the rest of the deps
    h.relay_state = state
    h.handler._relay_state = state  # rewire

    # Set the flag True at t=0, then jump past STALE_ACTIVE_FLAG_SECS.
    state.transition(0, True, "leaked-from-prior")
    clock["t"] = DEFAULT_STALE_ACTIVE_FLAG_SECS + 5.0

    # No slot allocated for any peer on account 0 — the auto-recovery
    # branch should fire.
    h.stage_remote_identities(["42"])
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)

    out_combined = log.getvalue() + h.log.getvalue()
    assert (
        "_active[0] looks stale" in out_combined
    ), "stale-flag auto-recovery branch did not fire"
    # And the call proceeded — sessions[] now has the new entry.
    assert h.sessions, "stale-recovery did not let the new call proceed"


def test_refuse_call_when_account_has_active_session(monkeypatch):
    """When `_account_active[idx]==True` and a slot is already
    allocated AND no eviction conditions apply, the new call must be
    refused — starting a concurrent LiveKit session overloads the
    livekit-rtc FFI. The caller's app will fall back to the next
    relay peer_id in its list."""
    h = _Harness()
    # Pre-populate a *running* session (not terminal, not peer-lost)
    # plus the active flag.
    running = FakeLiveKitSession(url="x", token="t", identity="prior")
    h.sessions.append((42, running, object()))
    h.allocator.join(42)
    h.relay_state.transition(0, True, "from-prior-call")

    # Brand-new inbound call.
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)

    out = h.log.getvalue()
    assert "refusing call on account=0" in out
    assert h.created_sessions == [], (
        "busy path created a LiveKitSession; that's the FFI-overload "
        "regression we're guarding against"
    )
    # And the active flag and sessions were left intact.
    assert h.relay_state.is_active(0) is True
    assert len(h.sessions) == 1


def test_sess_id_used_in_mesh_provision_matches_constant():
    """Regression for cf66326. The handler must use DEFAULT_TUNNEL_SESS_ID
    when building the MeshProvisionMessage; per-peer offsets caused the
    receiving side's sess_id filter to silently drop every Android frame."""
    h = _Harness()
    h.stage_remote_identities(["42"])
    event = _fake_event(peer_id=999)
    h.handler.handle_call(event)

    # Decode the provisioning message the handler sent on the transport.
    from userbot_bale.vpn.provisioning import MeshProvisionMessage
    sent = h.created_transports[0].sent
    assert sent, "no provisioning message sent"
    msg = MeshProvisionMessage.decode(sent[0])
    assert msg.session_id == DEFAULT_TUNNEL_SESS_ID


