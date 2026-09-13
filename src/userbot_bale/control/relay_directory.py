"""Local relay discovery registry backed by a JSON file."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from userbot_bale.control.paths import config_dir
from userbot_bale.control.store import JsonStore


@dataclass(frozen=True)
class RelayDirectoryEntry:
    relay_id: str
    name: str
    peer_id: int
    owner: str = ""
    transport_preference: str = "auto"
    backend_preference: str = "proxy"
    endpoint_hint: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelayDirectoryEntry":
        peer_id = data.get("peer_id", 0)
        try:
            peer_id = int(peer_id)
        except (TypeError, ValueError):
            peer_id = 0
        return cls(
            relay_id=str(data.get("relay_id", secrets.token_hex(4))),
            name=str(data.get("name", "relay")),
            peer_id=peer_id,
            owner=str(data.get("owner", "")),
            transport_preference=str(data.get("transport_preference", "auto")),
            backend_preference=str(data.get("backend_preference", "proxy")),
            endpoint_hint=str(data.get("endpoint_hint", "")),
            updated_at=float(data.get("updated_at", time.time())),
        )


class RelayDirectory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "relay_directory.json")
        self._store = JsonStore(self.path)

    def _load_all(self) -> list[RelayDirectoryEntry]:
        payload = self._store.load(default={"items": []})
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [RelayDirectoryEntry.from_dict(item) for item in items]

    def _save_all(self, items: list[RelayDirectoryEntry]) -> None:
        self._store.save({"items": [item.to_dict() for item in items]})

    def register(self, entry: RelayDirectoryEntry) -> RelayDirectoryEntry:
        items = self._load_all()
        updated: list[RelayDirectoryEntry] = []
        replaced = False
        for item in items:
            if item.relay_id == entry.relay_id or item.name == entry.name:
                updated.append(replace(entry, updated_at=time.time()))
                replaced = True
            else:
                updated.append(item)
        if not replaced:
            updated.append(replace(entry, updated_at=time.time()))
        self._save_all(updated)
        return updated[-1] if not replaced else next(item for item in updated if item.relay_id == entry.relay_id or item.name == entry.name)

    def lookup(self, name: str) -> RelayDirectoryEntry | None:
        for item in self._load_all():
            if item.name == name:
                return item
        return None

    def get(self, relay_id: str) -> RelayDirectoryEntry | None:
        for item in self._load_all():
            if item.relay_id == relay_id:
                return item
        return None

    def list(self) -> list[RelayDirectoryEntry]:
        return sorted(
            self._load_all(),
            key=lambda item: (-item.updated_at, item.name.lower(), item.relay_id),
        )

    def remove(self, identifier: str) -> RelayDirectoryEntry | None:
        items = self._load_all()
        removed = next((item for item in items if item.name == identifier or item.relay_id == identifier), None)
        if removed is None:
            return None
        self._save_all([item for item in items if item.name != identifier and item.relay_id != identifier])
        return removed


def relay_id_for_name(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "relay"
    return f"relay.{slug}.{secrets.token_hex(3)}"
