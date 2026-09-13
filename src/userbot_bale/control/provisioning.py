"""Server-backed relay provisioning modeled with a local control-plane store."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from userbot_bale.control.auth import AuthRecord
from userbot_bale.control.paths import config_dir
from userbot_bale.control.store import JsonStore


class ProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True)
class RelayEnrollment:
    relay_id: str
    owner_user_id: str
    owner_phone: str
    name: str
    role: str
    relay_mode: str
    backend_preference: str
    transport_preference: str
    peer_id: int
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeviceAuthorization:
    authorization_id: str
    relay_id: str
    device_id: str
    requested_by_user_id: str
    status: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    rejected_reason: str = ""
    revoked_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CredentialEpoch:
    epoch_id: str
    relay_id: str
    secret_value: str
    issued_at: float
    refresh_after: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProvisionedPeer:
    relay_id: str
    device_id: str
    peer_id: int | None
    peer_name: str | None
    authorization_status: str
    provisioning_status: str
    credential_epoch: CredentialEpoch | None = None


class ProvisioningService:
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        device_path: Path | None = None,
    ) -> None:
        self._store = JsonStore(state_path or (config_dir() / "provisioning-control-plane.json"))
        self._device_store = JsonStore(device_path or (config_dir() / "device.json"))

    def ensure_device_id(self) -> str:
        payload = self._device_store.load(default=None)
        if isinstance(payload, dict) and payload.get("device_id"):
            return str(payload["device_id"])
        device_id = f"dev.{secrets.token_hex(6)}"
        self._device_store.save({"device_id": device_id, "created_at": time.time()})
        return device_id

    def enroll_relay(
        self,
        auth: AuthRecord | None,
        *,
        name: str,
        role: str,
        relay_mode: str,
        backend_preference: str,
        transport_preference: str,
        peer_id: int | None = None,
    ) -> RelayEnrollment:
        owner_user_id, owner_phone = self._require_identity(auth)
        payload = self._load()
        relay = RelayEnrollment(
            relay_id=f"relay.{secrets.token_hex(4)}",
            owner_user_id=owner_user_id,
            owner_phone=owner_phone,
            name=name,
            role=role,
            relay_mode=relay_mode,
            backend_preference=backend_preference,
            transport_preference=transport_preference,
            peer_id=peer_id if peer_id is not None else secrets.randbelow(900000) + 100000,
        )
        payload["relays"].append(relay.to_dict())
        self._save(payload)
        return relay

    def list_relays(self, auth: AuthRecord | None) -> list[RelayEnrollment]:
        owner_user_id, _ = self._require_identity(auth)
        payload = self._load()
        return [
            RelayEnrollment(**item)
            for item in payload["relays"]
            if str(item.get("owner_user_id", "")) == owner_user_id
        ]

    def request_authorization(self, auth: AuthRecord | None, *, relay_id: str, device_id: str) -> DeviceAuthorization:
        owner_user_id, _ = self._require_identity(auth)
        payload = self._load()
        self._relay(payload, relay_id)
        existing = next(
            (
                item
                for item in payload["authorizations"]
                if item.get("relay_id") == relay_id and item.get("device_id") == device_id and item.get("revoked_at") in {None, ""}
            ),
            None,
        )
        if existing is not None:
            authz = DeviceAuthorization(**existing)
            if authz.status == "rejected":
                authz = DeviceAuthorization(
                    authorization_id=f"auth.{secrets.token_hex(4)}",
                    relay_id=relay_id,
                    device_id=device_id,
                    requested_by_user_id=owner_user_id,
                    status="pending",
                )
                payload["authorizations"].append(authz.to_dict())
                self._save(payload)
                return authz
            return authz
        authz = DeviceAuthorization(
            authorization_id=f"auth.{secrets.token_hex(4)}",
            relay_id=relay_id,
            device_id=device_id,
            requested_by_user_id=owner_user_id,
            status="pending",
        )
        payload["authorizations"].append(authz.to_dict())
        self._save(payload)
        return authz

    def get_authorization(self, auth: AuthRecord | None, *, relay_id: str, authorization_id: str) -> DeviceAuthorization:
        self._require_identity(auth)
        payload = self._load()
        item = next(
            (
                item
                for item in payload["authorizations"]
                if item.get("relay_id") == relay_id and item.get("authorization_id") == authorization_id
            ),
            None,
        )
        if item is None:
            raise ProvisioningError(f"authorization {authorization_id!r} not found")
        return DeviceAuthorization(**item)

    def approve_authorization(
        self,
        auth: AuthRecord | None,
        *,
        relay_id: str,
        authorization_id: str,
    ) -> DeviceAuthorization:
        owner_user_id, _ = self._require_identity(auth)
        payload = self._load()
        relay = self._relay(payload, relay_id)
        if relay.owner_user_id != owner_user_id:
            raise ProvisioningError("only the relay owner can approve access")
        authz = self.get_authorization(auth, relay_id=relay_id, authorization_id=authorization_id)
        updated = DeviceAuthorization(
            authorization_id=authz.authorization_id,
            relay_id=authz.relay_id,
            device_id=authz.device_id,
            requested_by_user_id=authz.requested_by_user_id,
            status="approved",
            created_at=authz.created_at,
            updated_at=time.time(),
        )
        self._replace_authorization(payload, updated)
        self._ensure_epoch(payload, relay_id=relay_id)
        self._save(payload)
        return updated

    def reject_authorization(
        self,
        auth: AuthRecord | None,
        *,
        relay_id: str,
        authorization_id: str,
        reason: str = "",
    ) -> DeviceAuthorization:
        owner_user_id, _ = self._require_identity(auth)
        payload = self._load()
        relay = self._relay(payload, relay_id)
        if relay.owner_user_id != owner_user_id:
            raise ProvisioningError("only the relay owner can reject access")
        authz = self.get_authorization(auth, relay_id=relay_id, authorization_id=authorization_id)
        updated = DeviceAuthorization(
            authorization_id=authz.authorization_id,
            relay_id=authz.relay_id,
            device_id=authz.device_id,
            requested_by_user_id=authz.requested_by_user_id,
            status="rejected",
            created_at=authz.created_at,
            updated_at=time.time(),
            rejected_reason=reason,
        )
        self._replace_authorization(payload, updated)
        self._save(payload)
        return updated

    def revoke_device(self, auth: AuthRecord | None, *, relay_id: str, device_id: str) -> DeviceAuthorization:
        owner_user_id, _ = self._require_identity(auth)
        payload = self._load()
        relay = self._relay(payload, relay_id)
        if relay.owner_user_id != owner_user_id:
            raise ProvisioningError("only the relay owner can revoke devices")
        item = next(
            (
                item
                for item in payload["authorizations"]
                if item.get("relay_id") == relay_id and item.get("device_id") == device_id and item.get("revoked_at") in {None, ""}
            ),
            None,
        )
        if item is None:
            raise ProvisioningError(f"device {device_id!r} is not authorized")
        authz = DeviceAuthorization(**item)
        updated = DeviceAuthorization(
            authorization_id=authz.authorization_id,
            relay_id=authz.relay_id,
            device_id=authz.device_id,
            requested_by_user_id=authz.requested_by_user_id,
            status="revoked",
            created_at=authz.created_at,
            updated_at=time.time(),
            revoked_at=time.time(),
        )
        self._replace_authorization(payload, updated)
        self._rotate_epoch(payload, relay_id=relay_id)
        self._save(payload)
        return updated

    def get_peer(self, auth: AuthRecord | None, *, relay_id: str, device_id: str) -> ProvisionedPeer:
        self._require_identity(auth)
        payload = self._load()
        relay = self._relay(payload, relay_id)
        authz = next(
            (
                DeviceAuthorization(**item)
                for item in payload["authorizations"]
                if item.get("relay_id") == relay_id and item.get("device_id") == device_id and item.get("revoked_at") in {None, ""}
            ),
            None,
        )
        if authz is None:
            return ProvisionedPeer(
                relay_id=relay_id,
                device_id=device_id,
                peer_id=None,
                peer_name=None,
                authorization_status="missing",
                provisioning_status="pending",
            )
        if authz.status != "approved":
            return ProvisionedPeer(
                relay_id=relay_id,
                device_id=device_id,
                peer_id=None,
                peer_name=None,
                authorization_status=authz.status,
                provisioning_status="pending" if authz.status == "pending" else authz.status,
            )
        epoch = self._ensure_epoch(payload, relay_id=relay_id)
        self._save(payload)
        return ProvisionedPeer(
            relay_id=relay_id,
            device_id=device_id,
            peer_id=relay.peer_id if relay.role != "relay" else None,
            peer_name=relay.name,
            authorization_status="approved",
            provisioning_status="complete",
            credential_epoch=epoch,
        )

    def refresh_credentials(self, auth: AuthRecord | None, *, relay_id: str) -> CredentialEpoch:
        self._require_identity(auth)
        payload = self._load()
        epoch = self._rotate_epoch(payload, relay_id=relay_id)
        self._save(payload)
        return epoch

    def current_epoch(self, auth: AuthRecord | None, *, relay_id: str) -> CredentialEpoch:
        self._require_identity(auth)
        payload = self._load()
        epoch = self._ensure_epoch(payload, relay_id=relay_id)
        self._save(payload)
        return epoch

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        payload = self._store.load(default={"relays": [], "authorizations": [], "epochs": []})
        if not isinstance(payload, dict):
            return {"relays": [], "authorizations": [], "epochs": []}
        payload.setdefault("relays", [])
        payload.setdefault("authorizations", [])
        payload.setdefault("epochs", [])
        return payload

    def _save(self, payload: dict[str, list[dict[str, Any]]]) -> None:
        self._store.save(payload)

    def _require_identity(self, auth: AuthRecord | None) -> tuple[str, str]:
        if auth is None:
            raise ProvisioningError("stored Bale auth is required for relay provisioning")
        owner_user_id = str(auth.user_id) if auth.user_id is not None else ""
        owner_phone = auth.phone or ""
        if not owner_user_id and not owner_phone:
            raise ProvisioningError("stored Bale auth is missing user identity metadata")
        return owner_user_id or owner_phone, owner_phone

    def _relay(self, payload: dict[str, list[dict[str, Any]]], relay_id: str) -> RelayEnrollment:
        item = next((item for item in payload["relays"] if item.get("relay_id") == relay_id), None)
        if item is None:
            raise ProvisioningError(f"relay {relay_id!r} not found")
        return RelayEnrollment(**item)

    def _ensure_epoch(self, payload: dict[str, list[dict[str, Any]]], *, relay_id: str) -> CredentialEpoch:
        active = [
            CredentialEpoch(**item)
            for item in payload["epochs"]
            if item.get("relay_id") == relay_id
        ]
        now = time.time()
        if active:
            epoch = max(active, key=lambda item: item.issued_at)
            if epoch.expires_at > now:
                return epoch
        return self._rotate_epoch(payload, relay_id=relay_id)

    def _rotate_epoch(self, payload: dict[str, list[dict[str, Any]]], *, relay_id: str) -> CredentialEpoch:
        now = time.time()
        epoch = CredentialEpoch(
            epoch_id=f"epoch.{secrets.token_hex(4)}",
            relay_id=relay_id,
            secret_value=secrets.token_urlsafe(24),
            issued_at=now,
            refresh_after=now + 900,
            expires_at=now + 1800,
        )
        payload["epochs"] = [item for item in payload["epochs"] if item.get("relay_id") != relay_id]
        payload["epochs"].append(epoch.to_dict())
        return epoch

    def _replace_authorization(self, payload: dict[str, list[dict[str, Any]]], updated: DeviceAuthorization) -> None:
        payload["authorizations"] = [
            updated.to_dict() if item.get("authorization_id") == updated.authorization_id else item
            for item in payload["authorizations"]
        ]
