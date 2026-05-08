"""Durable mesh provisioning records."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore
from baleobala.vpn.mesh.allocator import Assignment, IpAllocator


@dataclass(frozen=True)
class MeshProvisionRecord:
    peer_id: int
    pool_cidr: str
    slot: int
    prefix: str
    gateway_ip: str
    client_ip: str
    provisioning_status: str = "pending"
    profile_id: str | None = None
    peer_name: str | None = None
    transport: str = ""
    session_id: int | None = None
    validation_error: str = ""
    issued_at: float = field(default_factory=time.time)
    last_seen_at: float | None = None
    activated_at: float | None = None
    released_at: float | None = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MeshProvisionRecord":
        return cls(
            peer_id=int(data.get("peer_id", 0)),
            pool_cidr=str(data.get("pool_cidr", "10.77.0.0/16")),
            slot=int(data.get("slot", 0)),
            prefix=str(data.get("prefix", "")),
            gateway_ip=str(data.get("gateway_ip", "")),
            client_ip=str(data.get("client_ip", "")),
            provisioning_status=str(data.get("provisioning_status", "pending")),
            profile_id=data.get("profile_id"),
            peer_name=data.get("peer_name"),
            transport=str(data.get("transport", "")),
            session_id=(
                int(data["session_id"])
                if data.get("session_id") not in {None, ""}
                else None
            ),
            validation_error=str(data.get("validation_error", "")),
            issued_at=float(data.get("issued_at", time.time())),
            last_seen_at=(
                float(data["last_seen_at"])
                if data.get("last_seen_at") not in {None, ""}
                else None
            ),
            activated_at=(
                float(data["activated_at"])
                if data.get("activated_at") not in {None, ""}
                else None
            ),
            released_at=(
                float(data["released_at"])
                if data.get("released_at") not in {None, ""}
                else None
            ),
            updated_at=float(data.get("updated_at", time.time())),
        )

    def assignment(self) -> Assignment:
        return Assignment(
            peer_id=self.peer_id,
            slot=self.slot,
            gateway=self.gateway_ip,
            client=self.client_ip,
            prefix=self.prefix,
            pool_cidr=self.pool_cidr,
        )


class MeshProvisionStore:
    # Stale "active" assignments older than this are auto-released on
    # process startup. Without this, an unclean shutdown leaves slot 0
    # (10.77.0.2 — the hardcoded TUN address used by the Android and
    # macOS clients) reserved indefinitely; new connections then get
    # slot 1+ (10.77.0.6, .10, …) which the clients can't address. With
    # auto-release every fresh service start cleanly reuses slot 0.
    STARTUP_STALE_TTL_SECS = 60

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "mesh.json")
        self._store = JsonStore(self.path)
        self._auto_release_stale_on_startup()

    def _auto_release_stale_on_startup(self) -> None:
        items = self._load_all()
        if not items:
            return
        now = time.time()
        cutoff = now - self.STARTUP_STALE_TTL_SECS
        changed = False
        for idx, item in enumerate(items):
            if item.provisioning_status in {"released", "failed"}:
                continue
            last_activity = max(
                item.last_seen_at or 0.0,
                item.activated_at or 0.0,
                item.updated_at or 0.0,
            )
            if last_activity >= cutoff:
                continue
            items[idx] = MeshProvisionRecord(
                peer_id=item.peer_id,
                pool_cidr=item.pool_cidr,
                slot=item.slot,
                prefix=item.prefix,
                gateway_ip=item.gateway_ip,
                client_ip=item.client_ip,
                provisioning_status="released",
                profile_id=item.profile_id,
                peer_name=item.peer_name,
                transport=item.transport,
                session_id=item.session_id,
                validation_error=item.validation_error,
                issued_at=item.issued_at,
                last_seen_at=item.last_seen_at,
                activated_at=item.activated_at,
                released_at=now,
                updated_at=now,
            )
            changed = True
        if changed:
            self._save_all(items)

    def _load_all(self) -> list[MeshProvisionRecord]:
        payload = self._store.load(default={"items": []})
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [MeshProvisionRecord.from_dict(item) for item in items]

    def _save_all(self, items: list[MeshProvisionRecord]) -> None:
        self._store.save({"items": [item.to_dict() for item in items]})

    def list(self) -> list[MeshProvisionRecord]:
        return self._load_all()

    def get(self, peer_id: int) -> MeshProvisionRecord | None:
        for item in self._load_all():
            if item.peer_id == peer_id:
                return item
        return None

    def active(self) -> list[MeshProvisionRecord]:
        return [
            item for item in self._load_all()
            if item.provisioning_status in {"assigned", "active"}
        ]

    def issue_assignment(
        self,
        peer_id: int,
        *,
        pool_cidr: str,
        profile_id: str | None = None,
        peer_name: str | None = None,
        transport: str = "",
        session_id: int | None = None,
    ) -> MeshProvisionRecord:
        items = self._load_all()
        allocator = IpAllocator(pool_cidr)
        for item in items:
            if item.pool_cidr != pool_cidr:
                continue
            if item.provisioning_status == "released":
                continue
            allocator.reserve(item.slot, item.peer_id)
        current = next((item for item in items if item.peer_id == peer_id), None)
        if current is not None and current.pool_cidr == pool_cidr:
            assignment = current.assignment()
            issued_at = current.issued_at
        else:
            assignment = allocator.assign(peer_id)
            issued_at = time.time()
        now = time.time()
        updated = MeshProvisionRecord(
            peer_id=peer_id,
            pool_cidr=pool_cidr,
            slot=assignment.slot,
            prefix=assignment.prefix,
            gateway_ip=assignment.gateway,
            client_ip=assignment.client,
            provisioning_status="assigned",
            profile_id=profile_id,
            peer_name=peer_name,
            transport=transport,
            session_id=session_id,
            validation_error="",
            issued_at=issued_at,
            last_seen_at=now,
            activated_at=current.activated_at if current is not None else None,
            released_at=None,
            updated_at=now,
        )
        items = [item for item in items if item.peer_id != peer_id]
        items.append(updated)
        self._save_all(items)
        return updated

    def activate(
        self,
        peer_id: int,
        *,
        transport: str = "",
        session_id: int | None = None,
    ) -> MeshProvisionRecord:
        items = self._load_all()
        for idx, item in enumerate(items):
            if item.peer_id != peer_id:
                continue
            now = time.time()
            items[idx] = MeshProvisionRecord(
                peer_id=item.peer_id,
                pool_cidr=item.pool_cidr,
                slot=item.slot,
                prefix=item.prefix,
                gateway_ip=item.gateway_ip,
                client_ip=item.client_ip,
                provisioning_status="active",
                profile_id=item.profile_id,
                peer_name=item.peer_name,
                transport=transport or item.transport,
                session_id=session_id if session_id is not None else item.session_id,
                validation_error="",
                issued_at=item.issued_at,
                last_seen_at=now,
                activated_at=now,
                released_at=None,
                updated_at=now,
            )
            self._save_all(items)
            return items[idx]
        raise LookupError(f"no mesh provisioning for peer_id={peer_id}")

    def release(self, peer_id: int, *, error: str = "") -> MeshProvisionRecord | None:
        items = self._load_all()
        for idx, item in enumerate(items):
            if item.peer_id != peer_id:
                continue
            now = time.time()
            items[idx] = MeshProvisionRecord(
                peer_id=item.peer_id,
                pool_cidr=item.pool_cidr,
                slot=item.slot,
                prefix=item.prefix,
                gateway_ip=item.gateway_ip,
                client_ip=item.client_ip,
                provisioning_status="failed" if error else "released",
                profile_id=item.profile_id,
                peer_name=item.peer_name,
                transport=item.transport,
                session_id=item.session_id,
                validation_error=error,
                issued_at=item.issued_at,
                last_seen_at=now,
                activated_at=item.activated_at,
                released_at=now,
                updated_at=now,
            )
            self._save_all(items)
            return items[idx]
        return None

    def touch(self, peer_id: int) -> MeshProvisionRecord | None:
        items = self._load_all()
        for idx, item in enumerate(items):
            if item.peer_id != peer_id:
                continue
            now = time.time()
            items[idx] = MeshProvisionRecord(
                peer_id=item.peer_id,
                pool_cidr=item.pool_cidr,
                slot=item.slot,
                prefix=item.prefix,
                gateway_ip=item.gateway_ip,
                client_ip=item.client_ip,
                provisioning_status=item.provisioning_status,
                profile_id=item.profile_id,
                peer_name=item.peer_name,
                transport=item.transport,
                session_id=item.session_id,
                validation_error=item.validation_error,
                issued_at=item.issued_at,
                last_seen_at=now,
                activated_at=item.activated_at,
                released_at=item.released_at,
                updated_at=now,
            )
            self._save_all(items)
            return items[idx]
        return None

    def status(self) -> dict[str, str]:
        items = self.active()
        if not items:
            return {"state": "empty"}
        latest = max(items, key=lambda item: item.updated_at)
        return {
            "state": latest.provisioning_status,
            "peer_id": str(latest.peer_id),
            "client_ip": latest.client_ip,
            "gateway_ip": latest.gateway_ip,
            "prefix": latest.prefix,
            "pool_cidr": latest.pool_cidr,
            "transport": latest.transport or "unknown",
            "validation_error": latest.validation_error,
        }


def mesh_assignment_to_cidr(address: str, prefix: str) -> str:
    network = ipaddress.ip_network(prefix, strict=True)
    return f"{address}/{network.prefixlen}"
