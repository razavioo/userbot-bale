"""Relay pairing registry for the product control plane."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


@dataclass(frozen=True)
class PairingRecord:
    profile_id: str
    name: str
    role: str
    pair_code: str
    relay_mode: str = "proxy"
    peer_id: int | None = None
    peer_name: str | None = None
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PairingRecord":
        return cls(
            profile_id=str(data.get("profile_id", secrets.token_hex(4))),
            name=str(data.get("name", "relay")),
            role=str(data.get("role", "client")),
            pair_code=str(data.get("pair_code", "")),
            relay_mode=str(data.get("relay_mode", "proxy")),
            peer_id=data.get("peer_id"),
            peer_name=data.get("peer_name"),
            status=str(data.get("status", "pending")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


class PairingStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "pairing.json")
        self._store = JsonStore(self.path)

    def _load_all(self) -> list[PairingRecord]:
        payload = self._store.load(default={"items": []})
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [PairingRecord.from_dict(item) for item in items]

    def _save_all(self, items: list[PairingRecord]) -> None:
        self._store.save({"items": [item.to_dict() for item in items]})

    def list(self) -> list[PairingRecord]:
        return self._load_all()

    def get(self, profile_id: str) -> PairingRecord | None:
        for item in self._load_all():
            if item.profile_id == profile_id:
                return item
        return None

    def add(self, record: PairingRecord) -> PairingRecord:
        items = self._load_all()
        items = [item for item in items if item.profile_id != record.profile_id]
        items.append(record)
        self._save_all(items)
        return record

    def begin(self, name: str, *, role: str = "client", peer_id: int | None = None, peer_name: str | None = None, relay_mode: str = "proxy") -> PairingRecord:
        record = PairingRecord(
            profile_id=secrets.token_hex(4),
            name=name,
            role=role,
            pair_code=secrets.token_urlsafe(6),
            relay_mode=relay_mode,
            peer_id=peer_id,
            peer_name=peer_name,
            status="pending",
        )
        return self.add(record)

    def accept(self, code: str, *, name: str | None = None) -> PairingRecord:
        items = self._load_all()
        matched = None
        for idx, item in enumerate(items):
            if item.pair_code == code:
                matched = idx
                break
        if matched is None:
            raise LookupError(f"no pairing code {code!r} found")
        current = items[matched]
        updated = PairingRecord(
            profile_id=current.profile_id,
            name=name or current.name,
            role=current.role,
            pair_code=current.pair_code,
            relay_mode=current.relay_mode,
            peer_id=current.peer_id,
            peer_name=current.peer_name,
            status="paired",
            created_at=current.created_at,
            updated_at=time.time(),
        )
        items[matched] = updated
        self._save_all(items)
        return updated

    def remove(self, profile_id: str) -> None:
        items = [item for item in self._load_all() if item.profile_id != profile_id]
        self._save_all(items)

    def active(self) -> PairingRecord | None:
        for item in self._load_all():
            if item.status == "paired":
                return item
        return None
