"""Fair server-side capacity allocation for mesh peers.

The client's Bale JWT is only a bootstrap identity: it proves who joined
the mesh first. Bale currently allows only one stable active call per
client account, so server JWTs increase multi-user capacity, not the
throughput of a single client. The practical policy is therefore:

* one active peer should prefer one dedicated server JWT;
* when server JWTs are scarce, multiple peers may share a server JWT up
  to an operator-defined ceiling;
* if that ceiling is reached, new peers must wait or be rejected.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeerDemand:
    peer_id: int
    joined_order: int
    max_channels: int


class FairChannelAllocator:
    """Allocate a finite server account pool across active peers.

    Policy:
    - no peer receives more than ``max_channels_per_peer``;
    - every active peer gets an equal floor while capacity allows;
    - leftovers go to later joiners first, which gives newcomers a short
      catch-up advantage without starving existing peers.
    """

    def __init__(
        self,
        *,
        total_channels: int,
        max_channels_per_peer: int = 3,
    ) -> None:
        if total_channels < 0:
            raise ValueError("total_channels must be >= 0")
        if max_channels_per_peer < 1:
            raise ValueError("max_channels_per_peer must be >= 1")
        self.total_channels = total_channels
        self.max_channels_per_peer = max_channels_per_peer
        self._peers: dict[int, PeerDemand] = {}
        self._next_order = 0

    def join(self, peer_id: int, *, max_channels: int | None = None) -> None:
        if peer_id in self._peers:
            return
        limit = self.max_channels_per_peer if max_channels is None else max_channels
        if limit < 1:
            raise ValueError("max_channels must be >= 1")
        self._peers[peer_id] = PeerDemand(
            peer_id=peer_id,
            joined_order=self._next_order,
            max_channels=min(limit, self.max_channels_per_peer),
        )
        self._next_order += 1

    def leave(self, peer_id: int) -> None:
        self._peers.pop(peer_id, None)

    def allocation(self) -> dict[int, int]:
        peers = sorted(self._peers.values(), key=lambda p: p.joined_order)
        if not peers or self.total_channels <= 0:
            return {p.peer_id: 0 for p in peers}

        allocation = {p.peer_id: 0 for p in peers}
        remaining = self.total_channels
        eligible = list(peers)

        while remaining > 0 and eligible:
            if remaining >= len(eligible):
                for peer in eligible:
                    allocation[peer.peer_id] += 1
                remaining -= len(eligible)
                eligible = [
                    peer for peer in eligible
                    if allocation[peer.peer_id] < peer.max_channels
                ]
                continue

            # Not enough for a full round: favor later arrivals.
            for peer in sorted(eligible, key=lambda p: p.joined_order, reverse=True):
                if remaining <= 0:
                    break
                if allocation[peer.peer_id] >= peer.max_channels:
                    continue
                allocation[peer.peer_id] += 1
                remaining -= 1
            break

        return allocation

    def peers(self) -> list[int]:
        return [
            peer.peer_id
            for peer in sorted(self._peers.values(), key=lambda p: p.joined_order)
        ]


@dataclass(frozen=True)
class ServerJwtSlot:
    index: int
    label: str
    peer_ids: tuple[int, ...]


class ServerJwtAllocator:
    """Assign active peers to a finite pool of server JWT slots.

    This is the current production-safe model: a peer gets one server
    account lane. We do not try to bond several Bale calls for one peer
    because Bale drops/invalidates concurrent calls for the same client
    account in practice.
    """

    def __init__(
        self,
        *,
        server_count: int,
        max_peers_per_server: int = 4,
        labels: list[str] | None = None,
    ) -> None:
        if server_count < 0:
            raise ValueError("server_count must be >= 0")
        if max_peers_per_server < 1:
            raise ValueError("max_peers_per_server must be >= 1")
        self.server_count = server_count
        self.max_peers_per_server = max_peers_per_server
        self._labels = labels or [f"server-{i + 1}" for i in range(server_count)]
        if len(self._labels) != server_count:
            raise ValueError("labels length must match server_count")
        self._peer_order: dict[int, int] = {}
        self._next_order = 0

    @property
    def total_capacity(self) -> int:
        return self.server_count * self.max_peers_per_server

    def join(self, peer_id: int) -> int | None:
        if peer_id not in self._peer_order:
            self._peer_order[peer_id] = self._next_order
            self._next_order += 1
        return self.assignment().get(peer_id)

    def leave(self, peer_id: int) -> None:
        self._peer_order.pop(peer_id, None)

    def assignment(self) -> dict[int, int | None]:
        peers = [
            peer_id for peer_id, _order in sorted(
                self._peer_order.items(), key=lambda item: item[1]
            )
        ]
        assignment: dict[int, int | None] = {}
        if self.server_count == 0:
            return {peer_id: None for peer_id in peers}

        # First pass: spread peers one-per-server for best isolation.
        for pos, peer_id in enumerate(peers):
            if pos < self.server_count:
                assignment[peer_id] = pos
                continue
            lane = pos % self.server_count
            if pos // self.server_count >= self.max_peers_per_server:
                assignment[peer_id] = None
            else:
                assignment[peer_id] = lane
        return assignment

    def slots(self) -> list[ServerJwtSlot]:
        assignment = self.assignment()
        slots: list[ServerJwtSlot] = []
        for idx in range(self.server_count):
            peers = tuple(
                peer_id for peer_id, slot in assignment.items() if slot == idx
            )
            slots.append(ServerJwtSlot(index=idx, label=self._labels[idx], peer_ids=peers))
        return slots
