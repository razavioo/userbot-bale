"""Routing policy: pick a relay for an incoming client.

Selection algorithm (B2 — region-affinity + weighted load):

1. Filter to free slots (in_use < capacity).
2. If `client_region` is provided, prefer same-region slots. Fall back to
   any-region if no same-region slot is free.
3. Within each tier sort by load_fraction (in_use / capacity) ascending,
   breaking ties by relay_id for determinism.

Using load_fraction instead of raw in_use count prevents a relay with
capacity=4 from being unfairly preferred over capacity=1 relays just
because its absolute in_use count is smaller.
"""

from __future__ import annotations

from baleobala.coordinator.registry import RelayRegistry, RelaySlot


def _sort_key(slot: RelaySlot) -> tuple[float, str]:
    return (slot.load_fraction(), slot.relay_id)


def pick_relay(
    registry: RelayRegistry,
    *,
    client_peer_id: int | None = None,
    client_region: str | None = None,
) -> RelaySlot | None:
    """Return the best relay slot for a new client, or None if none free.

    `client_peer_id` is reserved for future sticky/ACL routing; unused now.
    `client_region` enables region-affinity: same-region slots are tried
    first; falls back to any free slot if no same-region slot is available.
    """
    del client_peer_id  # reserved for future affinity/ACL policies
    candidates = [slot for slot in registry.list_relays() if slot.is_free()]
    if not candidates:
        return None

    if client_region:
        same_region = [s for s in candidates if s.region == client_region]
        if same_region:
            same_region.sort(key=_sort_key)
            return same_region[0]

    candidates.sort(key=_sort_key)
    return candidates[0]
