"""Tests for coordinator routing policy (B2 — region affinity + weighted load)."""

from __future__ import annotations

from baleobala.coordinator.policy import pick_relay
from baleobala.coordinator.registry import RelayRegistry, RelaySlot


def _reg(*slots: RelaySlot) -> RelayRegistry:
    r = RelayRegistry()
    for s in slots:
        r.register(s)
    return r


def _slot(relay_id: str, capacity: int = 1, in_use: list[int] | None = None, region: str = "") -> RelaySlot:
    s = RelaySlot(relay_id=relay_id, capacity=capacity, in_use=in_use or [], peer_id=int(relay_id[-1], 36), region=region)
    return s


def test_no_relays_returns_none():
    assert pick_relay(RelayRegistry()) is None


def test_all_full_returns_none():
    reg = _reg(_slot("r1", capacity=1, in_use=[42]))
    assert pick_relay(reg) is None


def test_single_free_slot_returned():
    reg = _reg(_slot("r1", capacity=2, in_use=[42]))
    result = pick_relay(reg)
    assert result is not None and result.relay_id == "r1"


def test_weighted_load_prefers_lower_fraction():
    """A relay at 50% load (2/4) should beat one at 100%... but also beat one
    at 1/1 which is full. With capacity=4 in_use=2 vs capacity=2 in_use=0:
    fractions are 0.5 vs 0.0 so the empty one wins."""
    reg = _reg(
        _slot("r-heavy", capacity=4, in_use=[1, 2]),   # 2/4 = 0.5
        _slot("r-empty", capacity=2, in_use=[]),        # 0/2 = 0.0
    )
    result = pick_relay(reg)
    assert result is not None and result.relay_id == "r-empty"


def test_weighted_load_breaks_fraction_tie_by_relay_id():
    """Equal load fraction → alphabetical relay_id wins (determinism)."""
    reg = _reg(
        _slot("r-z", capacity=2, in_use=[1]),   # 0.5
        _slot("r-a", capacity=2, in_use=[2]),   # 0.5
    )
    result = pick_relay(reg)
    assert result is not None and result.relay_id == "r-a"


def test_region_affinity_prefers_same_region():
    """A more-loaded same-region relay beats a less-loaded different-region one."""
    reg = _reg(
        _slot("r-ir-1", capacity=4, in_use=[1, 2, 3], region="ir"),  # 0.75, same region
        _slot("r-eu-1", capacity=4, in_use=[], region="eu"),          # 0.0, different
    )
    result = pick_relay(reg, client_region="ir")
    assert result is not None and result.relay_id == "r-ir-1"


def test_region_affinity_falls_back_when_no_same_region_free():
    """If the same-region relay is full, pick the best any-region slot."""
    reg = _reg(
        _slot("r-ir-1", capacity=1, in_use=[42], region="ir"),  # full
        _slot("r-eu-1", capacity=2, in_use=[], region="eu"),    # free
    )
    result = pick_relay(reg, client_region="ir")
    assert result is not None and result.relay_id == "r-eu-1"


def test_no_region_hint_uses_weighted_load_globally():
    """Without client_region, falls back to global weighted-load sort."""
    reg = _reg(
        _slot("r-ir-1", capacity=4, in_use=[1], region="ir"),   # 0.25
        _slot("r-eu-1", capacity=4, in_use=[1, 2], region="eu"),# 0.5
    )
    result = pick_relay(reg, client_region=None)
    assert result is not None and result.relay_id == "r-ir-1"


def test_multiple_same_region_picks_lightest():
    """When several same-region relays are free, pick the one with lowest load."""
    reg = _reg(
        _slot("r-ir-1", capacity=4, in_use=[1, 2], region="ir"),  # 0.5
        _slot("r-ir-2", capacity=4, in_use=[3], region="ir"),     # 0.25
        _slot("r-ir-3", capacity=4, in_use=[], region="ir"),      # 0.0
    )
    result = pick_relay(reg, client_region="ir")
    assert result is not None and result.relay_id == "r-ir-3"


def test_region_from_online_message_is_preserved_in_registry(monkeypatch):
    """End-to-end: ONLINE with region= should surface in registry and routing."""
    from baleobala.coordinator.protocol import make_online
    from baleobala.coordinator.service import CoordinatorService
    import queue
    from typing import Callable
    from baleobala.coordinator.protocol import ControlMessage
    from baleobala.coordinator.transport import IncomingCall

    class FakeCall:
        def __init__(self, inbox):
            self.peer_id = 999
            self._q = queue.Queue()
            for m in inbox: self._q.put(m)
            self.sent = []; self.hung_up = False
        def recv(self, *, timeout): return self._q.get(timeout=timeout) if not self._q.empty() else None
        def send(self, m): self.sent.append(m)
        def hangup(self): self.hung_up = True

    class FakeTr:
        def __init__(self): self._on_call = None; self.outbound = []; self.stopped = False
        def listen(self, f): self._on_call = f
        def quick_exchange(self, *, peer_id, send, timeout): self.outbound.append((peer_id, send)); return None
        def stop(self): self.stopped = True
        def deliver(self, call): self._on_call(call)

    registry = RelayRegistry()
    transport = FakeTr()
    svc = CoordinatorService(transport=transport, registry=registry)
    svc.start()

    call = FakeCall([make_online(relay_id="r-ir-1", peer_id=200, capacity=2, region="ir")])
    transport.deliver(call)

    slot = registry.get_relay("r-ir-1")
    assert slot is not None and slot.region == "ir"

    # A client from IR gets routed there
    result = pick_relay(registry, client_region="ir")
    assert result is not None and result.relay_id == "r-ir-1"
