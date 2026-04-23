"""Relay pairing cache for the product control plane."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from baleobala.control.keychain import SecretBackend, default_secret_backend
from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


@dataclass(frozen=True)
class PairingExchange:
    version: str
    exchange_type: str
    profile_id: str
    pair_code: str
    name: str
    role: str
    relay_mode: str
    backend_preference: str
    transport_preference: str
    peer_id: int | None = None
    peer_name: str | None = None
    secret_value: str | None = None
    provisioning_status: str = "pending"
    validation_error: str = ""
    issued_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PairingExchange":
        return cls(
            version=str(data.get("version", "1")),
            exchange_type=str(data.get("exchange_type", "request")),
            profile_id=str(data.get("profile_id", "")),
            pair_code=str(data.get("pair_code", "")),
            name=str(data.get("name", "relay")),
            role=str(data.get("role", "client")),
            relay_mode=str(data.get("relay_mode", "proxy")),
            backend_preference=str(data.get("backend_preference", data.get("relay_mode", "proxy"))),
            transport_preference=str(data.get("transport_preference", "auto")),
            peer_id=data.get("peer_id"),
            peer_name=data.get("peer_name"),
            secret_value=data.get("secret_value"),
            provisioning_status=str(data.get("provisioning_status", "pending")),
            validation_error=str(data.get("validation_error", "")),
            issued_at=float(data.get("issued_at", time.time())),
        )


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
    authorization_status: str = "none"
    relay_id: str = ""
    device_id: str = ""
    authorization_id: str = ""
    credential_epoch: str = ""
    credential_expires_at: float | None = None
    credential_refresh_after: float | None = None
    last_server_sync_at: float | None = None
    revoked_at: float | None = None
    server_error: str = ""
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
        def _float(name: str) -> float | None:
            value = data.get(name)
            if value in {None, ""}:
                return None
            return float(value)

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
            authorization_status=str(data.get("authorization_status", "none")),
            relay_id=str(data.get("relay_id", "")),
            device_id=str(data.get("device_id", "")),
            authorization_id=str(data.get("authorization_id", "")),
            credential_epoch=str(data.get("credential_epoch", "")),
            credential_expires_at=_float("credential_expires_at"),
            credential_refresh_after=_float("credential_refresh_after"),
            last_server_sync_at=_float("last_server_sync_at"),
            revoked_at=_float("revoked_at"),
            server_error=str(data.get("server_error", "")),
            validation_error=str(data.get("validation_error", "")),
            peer_validated_at=_float("peer_validated_at"),
            last_transport=str(data.get("last_transport", "")),
            last_backend=str(data.get("last_backend", "")),
            status=str(data.get("status", "pending")),
            paired_at=_float("paired_at"),
            last_used_at=_float("last_used_at"),
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

    def _update(self, profile_id: str, **changes: Any) -> PairingRecord:
        items = self._load_all()
        updated: PairingRecord | None = None
        for idx, item in enumerate(items):
            if item.profile_id != profile_id:
                continue
            updated = replace(item, updated_at=time.time(), **changes)
            items[idx] = updated
            break
        if updated is None:
            raise LookupError(f"no pairing profile {profile_id!r} found")
        self._save_all(items)
        return updated

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
            authorization_status="none",
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
        matched = next((item for item in self._load_all() if item.pair_code == code), None)
        if matched is None:
            raise LookupError(f"no pairing code {code!r} found")
        return self._update(
            matched.profile_id,
            name=name or matched.name,
            peer_id=peer_id if peer_id is not None else matched.peer_id,
            peer_name=peer_name if peer_name is not None else matched.peer_name,
            backend_preference=backend_preference or matched.backend_preference or matched.relay_mode,
            transport_preference=transport_preference or matched.transport_preference,
            secret_name=secret_name if secret_name is not None else matched.secret_name,
            provisioning_status="complete" if validate else "accepted",
            authorization_status="approved" if validate else "pending",
            validation_error="",
            server_error="",
            peer_validated_at=time.time() if validate else matched.peer_validated_at,
            status="paired" if validate else "accepted",
            paired_at=time.time(),
            last_used_at=time.time(),
        )

    def export_request(self, profile_id: str) -> PairingExchange:
        record = self.get(profile_id)
        if record is None:
            raise LookupError(f"no pairing profile {profile_id!r} found")
        return PairingExchange(
            version="1",
            exchange_type="request",
            profile_id=record.profile_id,
            pair_code=record.pair_code,
            name=record.name,
            role=record.role,
            relay_mode=record.relay_mode,
            backend_preference=record.backend_preference,
            transport_preference=record.transport_preference,
            peer_id=record.peer_id,
            peer_name=record.peer_name,
            secret_value=self.load_secret(profile_id),
            provisioning_status=record.provisioning_status,
            validation_error=record.validation_error,
        )

    def accept_request(
        self,
        exchange: PairingExchange,
        *,
        name: str | None = None,
        peer_id: int | None = None,
        peer_name: str | None = None,
        backend_preference: str | None = None,
        transport_preference: str | None = None,
    ) -> PairingExchange:
        if exchange.exchange_type != "request":
            raise ValueError("pairing exchange must be a request")
        record = self.accept(
            exchange.pair_code,
            name=name or exchange.name,
            peer_id=peer_id if peer_id is not None else exchange.peer_id,
            peer_name=peer_name if peer_name is not None else exchange.peer_name,
            backend_preference=backend_preference or exchange.backend_preference,
            transport_preference=transport_preference or exchange.transport_preference,
            validate=True,
        )
        if record.secret_name and exchange.secret_value:
            self._secret_backend.save(record.secret_name, exchange.secret_value)
        return PairingExchange(
            version=exchange.version,
            exchange_type="response",
            profile_id=record.profile_id,
            pair_code=record.pair_code,
            name=record.name,
            role=record.role,
            relay_mode=record.relay_mode,
            backend_preference=record.backend_preference,
            transport_preference=record.transport_preference,
            peer_id=record.peer_id,
            peer_name=record.peer_name,
            secret_value=self.load_secret(record.profile_id),
            provisioning_status=record.provisioning_status,
            validation_error=record.validation_error,
        )

    def apply_response(self, exchange: PairingExchange) -> PairingRecord:
        if exchange.exchange_type != "response":
            raise ValueError("pairing exchange must be a response")
        record = self.accept(
            exchange.pair_code,
            name=exchange.name,
            peer_id=exchange.peer_id,
            peer_name=exchange.peer_name,
            backend_preference=exchange.backend_preference,
            transport_preference=exchange.transport_preference,
            validate=exchange.provisioning_status == "complete",
        )
        if record.secret_name and exchange.secret_value:
            self._secret_backend.save(record.secret_name, exchange.secret_value)
        return record

    def save_provisioning(
        self,
        profile_id: str,
        *,
        relay_id: str | None = None,
        device_id: str | None = None,
        authorization_id: str | None = None,
        authorization_status: str | None = None,
        provisioning_status: str | None = None,
        credential_epoch: str | None = None,
        credential_expires_at: float | None = None,
        credential_refresh_after: float | None = None,
        peer_id: int | None = None,
        peer_name: str | None = None,
        secret_value: str | None = None,
        server_error: str | None = None,
        revoked_at: float | None = None,
    ) -> PairingRecord:
        current = self.get(profile_id)
        if current is None:
            raise LookupError(f"no pairing profile {profile_id!r} found")
        if current.secret_name and secret_value is not None:
            self._secret_backend.save(current.secret_name, secret_value)
        status = current.status
        if authorization_status == "approved" and provisioning_status == "complete":
            status = "paired"
        elif authorization_status in {"pending", "rejected", "revoked"}:
            status = "pending"
        return self._update(
            profile_id,
            relay_id=relay_id if relay_id is not None else current.relay_id,
            device_id=device_id if device_id is not None else current.device_id,
            authorization_id=authorization_id if authorization_id is not None else current.authorization_id,
            authorization_status=authorization_status if authorization_status is not None else current.authorization_status,
            provisioning_status=provisioning_status if provisioning_status is not None else current.provisioning_status,
            credential_epoch=credential_epoch if credential_epoch is not None else current.credential_epoch,
            credential_expires_at=credential_expires_at if credential_expires_at is not None else current.credential_expires_at,
            credential_refresh_after=credential_refresh_after if credential_refresh_after is not None else current.credential_refresh_after,
            peer_id=peer_id if peer_id is not None else current.peer_id,
            peer_name=peer_name if peer_name is not None else current.peer_name,
            server_error=server_error if server_error is not None else current.server_error,
            revoked_at=revoked_at if revoked_at is not None else current.revoked_at,
            status=status,
            paired_at=time.time() if status == "paired" else current.paired_at,
            peer_validated_at=time.time() if status == "paired" else current.peer_validated_at,
            last_server_sync_at=time.time(),
            last_used_at=current.last_used_at if status != "paired" else time.time(),
        )

    def mark_server_error(self, profile_id: str, message: str) -> PairingRecord:
        return self._update(profile_id, server_error=message, last_server_sync_at=time.time())

    def touch(self, profile_id: str) -> PairingRecord | None:
        if self.get(profile_id) is None:
            return None
        return self._update(profile_id, last_used_at=time.time())

    def update_runtime_metadata(
        self,
        profile_id: str,
        *,
        backend: str | None = None,
        transport: str | None = None,
        validation_error: str | None = None,
    ) -> PairingRecord | None:
        current = self.get(profile_id)
        if current is None:
            return None
        return self._update(
            profile_id,
            last_transport=transport or current.last_transport,
            last_backend=backend or current.last_backend,
            validation_error=validation_error if validation_error is not None else current.validation_error,
            last_used_at=time.time(),
        )

    def connectable(self, profile_id: str | None = None) -> PairingRecord | None:
        candidate = self.get(profile_id) if profile_id else self.active()
        if candidate is None:
            return None
        if candidate.revoked_at is not None:
            return None
        if candidate.status != "paired" or candidate.provisioning_status != "complete":
            return None
        if candidate.authorization_status not in {"approved", "none"}:
            return None
        if candidate.credential_expires_at is not None and candidate.credential_expires_at <= time.time():
            return None
        if candidate.peer_id is None and candidate.role != "relay":
            return None
        if candidate.secret_name and self._secret_backend.load(candidate.secret_name) is None:
            return None
        return candidate

    def remove(self, profile_id: str) -> None:
        items = self._load_all()
        removed = next((item for item in items if item.profile_id == profile_id), None)
        self._save_all([item for item in items if item.profile_id != profile_id])
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
        return max(paired, key=lambda item: item.last_used_at or item.paired_at or item.updated_at or item.created_at)

    def _secret_name_for(self, name: str) -> str:
        slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "pairing"
        return f"pairing.{slug}.{secrets.token_hex(3)}"
