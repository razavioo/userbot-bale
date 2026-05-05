from __future__ import annotations

import pytest

from baleobala.coordinator.registry import RelayRegistry, RelaySlot


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def registry(clock):
    return RelayRegistry(clock=clock)


def test_register_adds_relay(registry, clock):
    slot = registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    assert slot.relay_id == "r1"
    assert slot.last_heartbeat == clock.now
    assert registry.list_relays()[0].relay_id == "r1"


def test_register_preserves_existing_in_use(registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=2))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=10,
    )
    # Re-register (e.g. relay restarted and re-announced) — must not lose binding.
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=2))
    relay = registry.get_relay("r1")
    assert relay is not None
    assert relay.in_use == [42]


def test_reserve_session_consumes_slot(registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )
    relay = registry.get_relay("r1")
    assert relay is not None
    assert not relay.is_free()


def test_reserve_session_rejects_unknown_relay(registry):
    with pytest.raises(LookupError):
        registry.reserve_session(
            session_id="s1", client_peer_id=42, relay_id="missing", expires_in_secs=30,
        )


def test_reserve_session_rejects_full_capacity(registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )
    with pytest.raises(RuntimeError):
        registry.reserve_session(
            session_id="s2", client_peer_id=43, relay_id="r1", expires_in_secs=30,
        )


def test_release_session_frees_slot(registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )
    released = registry.release_session("s1")
    assert released is not None
    assert released.session_id == "s1"
    relay = registry.get_relay("r1")
    assert relay is not None and relay.is_free()


def test_release_unknown_session_returns_none(registry):
    assert registry.release_session("nope") is None


def test_heartbeat_updates_last_heartbeat_and_in_use(registry, clock):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=2))
    clock.advance(60)
    updated = registry.heartbeat("r1", in_use=[1, 2])
    assert updated is not None
    assert updated.last_heartbeat == 1060.0
    assert updated.in_use == [1, 2]


def test_heartbeat_unknown_relay_returns_none(registry):
    assert registry.heartbeat("missing") is None


def test_prune_stale_removes_old_relays(registry, clock):
    registry.register(RelaySlot(relay_id="r1", peer_id=100))
    registry.register(RelaySlot(relay_id="r2", peer_id=101))
    clock.advance(100)
    registry.heartbeat("r2")  # only r2 stays fresh
    clock.advance(60)
    removed = registry.prune_stale(timeout_secs=90.0)
    assert removed == ["r1"]
    assert {s.relay_id for s in registry.list_relays()} == {"r2"}


def test_prune_stale_drops_their_sessions(registry, clock):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=120,
    )
    clock.advance(200)
    registry.prune_stale(timeout_secs=90.0)
    assert registry.get_session("s1") is None


def test_expire_pending_drops_unconfirmed_sessions(registry, clock):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=10,
    )
    clock.advance(20)
    expired = registry.expire_pending()
    assert [s.session_id for s in expired] == ["s1"]
    relay = registry.get_relay("r1")
    assert relay is not None and relay.is_free()


def test_snapshot_round_trip(tmp_path, clock):
    snap = tmp_path / "coord.json"
    reg = RelayRegistry(snapshot_path=snap, clock=clock)
    reg.register(RelaySlot(relay_id="r1", peer_id=100, capacity=2))
    reg.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )
    # Reload into a fresh instance.
    reg2 = RelayRegistry(snapshot_path=snap, clock=clock)
    relay = reg2.get_relay("r1")
    assert relay is not None
    assert relay.peer_id == 100
    assert relay.in_use == [42]
    session = reg2.get_session("s1")
    assert session is not None
    assert session.client_peer_id == 42


def test_total_capacity_and_in_use(registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=2))
    registry.register(RelaySlot(relay_id="r2", peer_id=101, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )
    assert registry.total_capacity() == 3
    assert registry.total_in_use() == 1


def test_mark_offline_drops_relay_and_sessions(registry):
    registry.register(RelaySlot(relay_id="r1", peer_id=100, capacity=1))
    registry.reserve_session(
        session_id="s1", client_peer_id=42, relay_id="r1", expires_in_secs=30,
    )
    registry.mark_offline("r1")
    assert registry.get_relay("r1") is None
    assert registry.get_session("s1") is None
