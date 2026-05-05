"""Routing policy: pick a relay for an incoming client.

Phase 1: load-balanced first-free with simple fairness — choose the relay slot
with the smallest in_use count, breaking ties by relay_id. No oversubscription
yet (capacity is hard-cap). Geo / region / ACL policies come later.
"""

from __future__ import annotations

from baleobala.coordinator.registry import RelayRegistry, RelaySlot


def pick_relay(registry: RelayRegistry, *, client_peer_id: int | None = None) -> RelaySlot | None:
    """Return the best relay slot for a new client, or None if none free.

    `client_peer_id` is currently unused but kept in the signature so we can
    introduce sticky/affinity routing without churning callers.
    """
    del client_peer_id  # reserved for future affinity/ACL policies
    candidates = [slot for slot in registry.list_relays() if slot.is_free()]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (len(s.in_use), s.relay_id))
    return candidates[0]
