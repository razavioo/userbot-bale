from __future__ import annotations

from baleobala.vpn.mesh.capacity import FairChannelAllocator, ServerJwtAllocator


def test_single_peer_is_capped_below_total_pool():
    alloc = FairChannelAllocator(total_channels=5, max_channels_per_peer=3)
    alloc.join(10)
    assert alloc.allocation() == {10: 3}


def test_later_peer_gets_leftover_after_equal_floor():
    alloc = FairChannelAllocator(total_channels=5, max_channels_per_peer=3)
    alloc.join(1)
    alloc.join(2)
    assert alloc.allocation() == {1: 2, 2: 3}


def test_four_peers_share_five_channels_with_newest_priority():
    alloc = FairChannelAllocator(total_channels=5, max_channels_per_peer=3)
    for peer_id in (1, 2, 3, 4):
        alloc.join(peer_id)
    assert alloc.allocation() == {1: 1, 2: 1, 3: 1, 4: 2}


def test_capacity_rebalances_when_peer_leaves():
    alloc = FairChannelAllocator(total_channels=5, max_channels_per_peer=3)
    for peer_id in (1, 2, 3, 4):
        alloc.join(peer_id)
    alloc.leave(4)
    assert alloc.allocation() == {1: 1, 2: 2, 3: 2}


def test_per_peer_override_can_lower_demand():
    alloc = FairChannelAllocator(total_channels=5, max_channels_per_peer=3)
    alloc.join(1, max_channels=1)
    alloc.join(2)
    assert alloc.allocation() == {1: 1, 2: 3}


def test_server_jwt_allocator_prefers_one_peer_per_jwt():
    alloc = ServerJwtAllocator(server_count=4, max_peers_per_server=4)
    for peer_id in (10, 20, 30):
        alloc.join(peer_id)
    assert alloc.assignment() == {10: 0, 20: 1, 30: 2}
    assert [slot.peer_ids for slot in alloc.slots()] == [(10,), (20,), (30,), ()]


def test_server_jwt_allocator_shares_after_all_jwts_are_used():
    alloc = ServerJwtAllocator(server_count=2, max_peers_per_server=4)
    for peer_id in range(1, 6):
        alloc.join(peer_id)
    assert alloc.assignment() == {1: 0, 2: 1, 3: 0, 4: 1, 5: 0}
    assert [slot.peer_ids for slot in alloc.slots()] == [(1, 3, 5), (2, 4)]


def test_server_jwt_allocator_rejects_beyond_capacity():
    alloc = ServerJwtAllocator(server_count=2, max_peers_per_server=2)
    for peer_id in range(1, 6):
        alloc.join(peer_id)
    assert alloc.assignment() == {1: 0, 2: 1, 3: 0, 4: 1, 5: None}


def test_server_jwt_allocator_rebalances_when_peer_leaves():
    alloc = ServerJwtAllocator(server_count=2, max_peers_per_server=2)
    for peer_id in (1, 2, 3, 4):
        alloc.join(peer_id)
    alloc.leave(2)
    assert alloc.assignment() == {1: 0, 3: 1, 4: 0}


def test_server_jwt_allocator_zero_servers_rejects_all():
    alloc = ServerJwtAllocator(server_count=0, max_peers_per_server=4)
    alloc.join(1)
    assert alloc.assignment() == {1: None}
    assert alloc.total_capacity == 0
