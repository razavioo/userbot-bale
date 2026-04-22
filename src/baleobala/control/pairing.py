"""Relay pairing registry for the product control plane."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from baleobala.control.keychain import SecretBackend, default_secret_backend
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
    backend_preference: str = ""
    transport_preference: str = "auto"
    secret_name: str | None = None
    provisioning_status: str = "pending"
    validation_error: str = ""
    peer_validated_at: float | None = None
    last_transport: str = ""
    last_backend: str = ""
    status: str = "pending"
    paired_at: float | None = None
    last_used_at: float | None = None
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
            backend_preference=str(data.get("backend_preference", data.get("relay_mode", "proxy"))),
            transport_preference=str(data.get("transport_preference", "auto")),
            secret_name=data.get("secret_name"),
            provisioning_status=str(data.get("provisioning_status", data.get("status", "pending"))),
            validation_error=str(data.get("validation_error", "")),
            peer_validated_at=(
                float(data["peer_validated_at"])
                if data.get("peer_validated_at") not in {None, ""}
                else None
            ),
            last_transport=str(data.get("last_transport", "")),
            last_backend=str(data.get("last_backend", "")),
            status=str(data.get("status", "pending")),
            paired_at=(
                float(data["paired_at"])
                if data.get("paired_at") not in {None, ""}
                else None
            ),
            last_used_at=(
                float(data["last_used_at"])
                if data.get("last_used_at") not in {None, ""}
                else None
            ),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


class PairingStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "pairing.json")
        self._store = JsonStore(self.path)
        self._secret_backend: SecretBackend = default_secret_backend()

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

    def begin(
        self,
        name: str,
        *,
        role: str = "client",
        peer_id: int | None = None,
        peer_name: str | None = None,
        relay_mode: str = "proxy",
        backend_preference: str | None = None,
        transport_preference: str = "auto",
        secret_name: str | None = None,
    ) -> PairingRecord:
        record = PairingRecord(
            profile_id=secrets.token_hex(4),
            name=name,
            role=role,
            pair_code=secrets.token_urlsafe(6),
            relay_mode=relay_mode,
            peer_id=peer_id,
            peer_name=peer_name,
            backend_preference=backend_preference or relay_mode,
            transport_preference=transport_preference,
            secret_name=secret_name or self._secret_name_for(name),
            provisioning_status="pending",
            status="pending",
        )
        if record.secret_name:
            self._secret_backend.save(record.secret_name, secrets.token_urlsafe(24))
        return self.add(record)

    def accept(
        self,
        code: str,
        *,
        name: str | None = None,
        peer_id: int | None = None,
        peer_name: str | None = None,
        backend_preference: str | None = None,
        transport_preference: str | None = None,
        secret_name: str | None = None,
        validate: bool = True,
    ) -> PairingRecord:
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
            peer_id=peer_id if peer_id is not None else current.peer_id,
            peer_name=peer_name if peer_name is not None else current.peer_name,
            backend_preference=backend_preference or current.backend_preference or current.relay_mode,
            transport_preference=transport_preference or current.transport_preference,
            secret_name=secret_name if secret_name is not None else current.secret_name,
            provisioning_status="complete" if validate else "accepted",
            validation_error="",
            peer_validated_at=time.time() if validate else current.peer_validated_at,
            last_transport=current.last_transport,
            last_backend=current.last_backend,
            status="paired" if validate else "accepted",
            paired_at=time.time(),
            last_used_at=time.time(),
            created_at=current.created_at,
            updated_at=time.time(),
        )
        items[matched] = updated
        self._save_all(items)
        return updated

    def touch(self, profile_id: str) -> PairingRecord | None:
        items = self._load_all()
        updated = None
        for idx, item in enumerate(items):
            if item.profile_id != profile_id:
                continue
            updated = PairingRecord(
                profile_id=item.profile_id,
                name=item.name,
                role=item.role,
                pair_code=item.pair_code,
                relay_mode=item.relay_mode,
                peer_id=item.peer_id,
                peer_name=item.peer_name,
                backend_preference=item.backend_preference,
                transport_preference=item.transport_preference,
                secret_name=item.secret_name,
                provisioning_status=item.provisioning_status,
                validation_error=item.validation_error,
                peer_validated_at=item.peer_validated_at,
                last_transport=item.last_transport,
                last_backend=item.last_backend,
                status=item.status,
                paired_at=item.paired_at,
                last_used_at=time.time(),
                created_at=item.created_at,
                updated_at=time.time(),
            )
            items[idx] = updated
            break
        if updated is not None:
            self._save_all(items)
        return updated

    def update_runtime_metadata(
        self,
        profile_id: str,
        *,
        backend: str | None = None,
        transport: str | None = None,
        validation_error: str | None = None,
    ) -> PairingRecord | None:
        items = self._load_all()
        updated = None
        for idx, item in enumerate(items):
            if item.profile_id != profile_id:
                continue
            updated = PairingRecord(
                profile_id=item.profile_id,
                name=item.name,
                role=item.role,
                pair_code=item.pair_code,
                relay_mode=item.relay_mode,
                peer_id=item.peer_id,
                peer_name=item.peer_name,
                backend_preference=item.backend_preference,
                transport_preference=item.transport_preference,
                secret_name=item.secret_name,
                provisioning_status=item.provisioning_status,
                validation_error=validation_error if validation_error is not None else item.validation_error,
                peer_validated_at=item.peer_validated_at,
                last_transport=transport or item.last_transport,
                last_backend=backend or item.last_backend,
                status=item.status,
                paired_at=item.paired_at,
                last_used_at=time.time(),
                created_at=item.created_at,
                updated_at=time.time(),
            )
            items[idx] = updated
            break
        if updated is not None:
            self._save_all(items)
        return updated

    def connectable(self, profile_id: str | None = None) -> PairingRecord | None:
        candidate = self.get(profile_id) if profile_id else self.active()
        if candidate is None:
            return None
        if candidate.status != "paired" or candidate.provisioning_status != "complete":
            return None
        if candidate.peer_id is None and candidate.role != "relay":
            return None
        if candidate.secret_name and self._secret_backend.load(candidate.secret_name) is None:
            return None
        return candidate

    def remove(self, profile_id: str) -> None:
        items = self._load_all()
        removed = next((item for item in items if item.profile_id == profile_id), None)
        items = [item for item in items if item.profile_id != profile_id]
        self._save_all(items)
        if removed is not None and removed.secret_name:
            self._secret_backend.delete(removed.secret_name)

    def load_secret(self, profile_id: str) -> str | None:
        record = self.get(profile_id)
        if record is None or not record.secret_name:
            return None
        return self._secret_backend.load(record.secret_name)

    def active(self) -> PairingRecord | None:
        paired = [
            item
            for item in self._load_all()
            if item.status == "paired" and item.provisioning_status == "complete"
        ]
        if not paired:
            return None

        def _score(item: PairingRecord) -> float:
            return item.last_used_at or item.paired_at or item.updated_at or item.created_at

        return max(paired, key=_score)

    def _secret_name_for(self, name: str) -> str:
        slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "pairing"
        return f"pairing.{slug}.{secrets.token_hex(3)}"
