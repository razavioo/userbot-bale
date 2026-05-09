"""In-memory relay registry with JSON snapshot persistence.

The coordinator owns the authoritative view of which relay slots are online,
which ones are bound to a client session, and when each was last heard from.
State is held in memory; periodic snapshots to JSON allow recovery across
restarts. Snapshot schema mirrors the style of MeshProvisionStore.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


SNAPSHOT_VERSION = 1


@dataclass
class RelaySlot:
    relay_id: str
    peer_id: int
    capacity: int = 1
    in_use: list[int] = field(default_factory=list)
    last_heartbeat: float = field(default_factory=time.time)

    def is_free(self) -> bool:
        return len(self.in_use) < self.capacity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelaySlot":
        return cls(
            relay_id=str(data.get("relay_id", "")),
            peer_id=int(data.get("peer_id", 0)),
            capacity=int(data.get("capacity", 1)),
            in_use=[int(p) for p in data.get("in_use", [])],
            last_heartbeat=float(data.get("last_heartbeat", time.time())),
        )


@dataclass
class Session:
    session_id: str
    client_peer_id: int
    relay_id: str
    started_at: float = field(default_factory=time.time)
    expires_at: float | None = None  # set when assignment is issued; cleared when relay confirms join

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        expires = data.get("expires_at")
        return cls(
            session_id=str(data.get("session_id", "")),
            client_peer_id=int(data.get("client_peer_id", 0)),
            relay_id=str(data.get("relay_id", "")),
            started_at=float(data.get("started_at", time.time())),
            expires_at=float(expires) if expires not in (None, "") else None,
        )


class RelayRegistry:
    """Thread-safe in-memory registry with optional JSON snapshot."""

    def __init__(
        self,
        *,
        snapshot_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        relay_secrets: dict[str, str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._relays: dict[str, RelaySlot] = {}
        self._sessions: dict[str, Session] = {}
        self._secrets: dict[str, str] = dict(relay_secrets) if relay_secrets else {}
        self._clock = clock
        self._store: JsonStore | None
        if snapshot_path is None:
            self._store = None
            self.snapshot_path: Path | None = None
        else:
            self.snapshot_path = Path(snapshot_path)
            self._store = JsonStore(self.snapshot_path)
            self._load_snapshot()

    # ---- relay lifecycle -------------------------------------------------

    def register(self, slot: RelaySlot) -> RelaySlot:
        with self._lock:
            existing = self._relays.get(slot.relay_id)
            if existing is not None:
                # preserve in_use bindings; trust new capacity/peer_id
                slot = RelaySlot(
                    relay_id=slot.relay_id,
                    peer_id=slot.peer_id,
                    capacity=slot.capacity,
                    in_use=list(existing.in_use),
                    last_heartbeat=self._clock(),
                )
            else:
                slot = RelaySlot(
                    relay_id=slot.relay_id,
                    peer_id=slot.peer_id,
                    capacity=slot.capacity,
                    in_use=list(slot.in_use),
                    last_heartbeat=self._clock(),
                )
            self._relays[slot.relay_id] = slot
            self._save()
            return slot

    def heartbeat(self, relay_id: str, *, in_use: list[int] | None = None) -> RelaySlot | None:
        with self._lock:
            slot = self._relays.get(relay_id)
            if slot is None:
                return None
            slot.last_heartbeat = self._clock()
            if in_use is not None:
                slot.in_use = [int(p) for p in in_use]
            self._save()
            return slot

    def mark_offline(self, relay_id: str) -> None:
        with self._lock:
            self._relays.pop(relay_id, None)
            # drop any sessions that pointed at this relay
            stale = [sid for sid, s in self._sessions.items() if s.relay_id == relay_id]
            for sid in stale:
                self._sessions.pop(sid, None)
            self._save()

    def prune_stale(self, *, timeout_secs: float) -> list[str]:
        """Remove relays whose last_heartbeat is older than timeout. Returns removed relay_ids."""
        cutoff = self._clock() - timeout_secs
        removed: list[str] = []
        with self._lock:
            for relay_id, slot in list(self._relays.items()):
                if slot.last_heartbeat < cutoff:
                    removed.append(relay_id)
            for relay_id in removed:
                self._relays.pop(relay_id, None)
                stale = [sid for sid, s in self._sessions.items() if s.relay_id == relay_id]
                for sid in stale:
                    self._sessions.pop(sid, None)
            if removed:
                self._save()
        return removed

    # ---- session lifecycle -----------------------------------------------

    def reserve_session(
        self,
        *,
        session_id: str,
        client_peer_id: int,
        relay_id: str,
        expires_in_secs: int,
    ) -> Session:
        with self._lock:
            slot = self._relays.get(relay_id)
            if slot is None:
                raise LookupError(f"unknown relay_id {relay_id!r}")
            if not slot.is_free():
                raise RuntimeError(f"relay {relay_id} is at capacity")
            slot.in_use.append(int(client_peer_id))
            session = Session(
                session_id=session_id,
                client_peer_id=int(client_peer_id),
                relay_id=relay_id,
                started_at=self._clock(),
                expires_at=self._clock() + float(expires_in_secs),
            )
            self._sessions[session_id] = session
            self._save()
            return session

    def release_session(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return None
            slot = self._relays.get(session.relay_id)
            if slot is not None and session.client_peer_id in slot.in_use:
                slot.in_use.remove(session.client_peer_id)
            self._save()
            return session

    def expire_pending(self) -> list[Session]:
        """Drop sessions whose expires_at has passed without confirmation."""
        now = self._clock()
        expired: list[Session] = []
        with self._lock:
            for sid, session in list(self._sessions.items()):
                if session.expires_at is not None and session.expires_at < now:
                    expired.append(session)
                    self._sessions.pop(sid, None)
                    slot = self._relays.get(session.relay_id)
                    if slot is not None and session.client_peer_id in slot.in_use:
                        slot.in_use.remove(session.client_peer_id)
            if expired:
                self._save()
        return expired

    # ---- queries ---------------------------------------------------------

    def list_relays(self) -> list[RelaySlot]:
        with self._lock:
            return [self._copy_slot(s) for s in self._relays.values()]

    def get_relay(self, relay_id: str) -> RelaySlot | None:
        with self._lock:
            slot = self._relays.get(relay_id)
            return self._copy_slot(slot) if slot else None

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return Session(**asdict(session)) if session else None

    def list_sessions(self) -> list[Session]:
        with self._lock:
            return [Session(**asdict(s)) for s in self._sessions.values()]

    # ---- relay secrets (A6 auth) -----------------------------------------

    def get_secret(self, relay_id: str) -> str | None:
        """Return the HMAC secret for a relay, or None if not enrolled."""
        with self._lock:
            return self._secrets.get(relay_id)

    def set_secret(self, relay_id: str, secret_hex: str) -> None:
        """Register or update the HMAC secret for a relay."""
        with self._lock:
            self._secrets[relay_id] = secret_hex

    def known_relay_ids(self) -> set[str]:
        """Return relay_ids that have a registered secret."""
        with self._lock:
            return set(self._secrets.keys())

    def total_capacity(self) -> int:
        with self._lock:
            return sum(s.capacity for s in self._relays.values())

    def total_in_use(self) -> int:
        with self._lock:
            return sum(len(s.in_use) for s in self._relays.values())

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _copy_slot(slot: RelaySlot) -> RelaySlot:
        return RelaySlot(
            relay_id=slot.relay_id,
            peer_id=slot.peer_id,
            capacity=slot.capacity,
            in_use=list(slot.in_use),
            last_heartbeat=slot.last_heartbeat,
        )

    def _save(self) -> None:
        if self._store is None:
            return
        payload = {
            "version": SNAPSHOT_VERSION,
            "relays": [s.to_dict() for s in self._relays.values()],
            "sessions": [s.to_dict() for s in self._sessions.values()],
        }
        self._store.save(payload)

    def _load_snapshot(self) -> None:
        if self._store is None:
            return
        payload = self._store.load(default={"version": SNAPSHOT_VERSION, "relays": [], "sessions": []})
        if not isinstance(payload, dict):
            return
        for entry in payload.get("relays", []):
            try:
                slot = RelaySlot.from_dict(entry)
                self._relays[slot.relay_id] = slot
            except (TypeError, ValueError):
                continue
        for entry in payload.get("sessions", []):
            try:
                session = Session.from_dict(entry)
                self._sessions[session.session_id] = session
            except (TypeError, ValueError):
                continue


def default_snapshot_path() -> Path:
    return config_dir() / "coordinator-state.json"
