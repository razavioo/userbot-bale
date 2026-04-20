"""VPN profile and product-flow orchestration helpers."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
from typing import Any

from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


def default_vpn_backend() -> str:
    if os.environ.get("BALEOBALA_VPN_BACKEND"):
        return os.environ["BALEOBALA_VPN_BACKEND"]
    if os.sys.platform == "darwin":
        return "packet-tunnel"
    return "proxy"


@dataclass(frozen=True)
class VpnProfile:
    profile_id: str
    name: str
    backend: str = field(default_factory=default_vpn_backend)
    role: str = "client"
    pairing_id: str | None = None
    peer_id: int | None = None
    peer_name: str | None = None
    answer: bool = False
    auto_start: bool = False
    listen_host: str = "127.0.0.1"
    listen_port: int = 1080
    protocol: str = "fast"
    volume: int = 50
    proxy_secret: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VpnProfile":
        return cls(
            profile_id=str(data.get("profile_id", "default")),
            name=str(data.get("name", "default")),
            backend=str(data.get("backend", default_vpn_backend())),
            role=str(data.get("role", "client")),
            pairing_id=data.get("pairing_id"),
            peer_id=data.get("peer_id"),
            peer_name=data.get("peer_name"),
            answer=bool(data.get("answer", False)),
            auto_start=bool(data.get("auto_start", False)),
            listen_host=str(data.get("listen_host", "127.0.0.1")),
            listen_port=int(data.get("listen_port", 1080)),
            protocol=str(data.get("protocol", "fast")),
            volume=int(data.get("volume", 50)),
            proxy_secret=data.get("proxy_secret"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


class VpnStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "vpn.json")
        self._store = JsonStore(self.path)

    def load(self) -> VpnProfile | None:
        payload = self._store.load(default=None)
        if not payload:
            return None
        return VpnProfile.from_dict(payload)

    def save(self, profile: VpnProfile) -> None:
        self._store.save(profile.to_dict())

    def ensure_default(self) -> VpnProfile:
        profile = self.load()
        if profile is not None:
            return profile
        profile = VpnProfile(profile_id="default", name="default", backend=default_vpn_backend())
        self.save(profile)
        return profile

    def status(self) -> dict[str, str]:
        profile = self.load()
        if profile is None:
            return {"state": "empty"}
        return {
            "state": "configured",
            "profile_id": profile.profile_id,
            "name": profile.name,
            "backend": profile.backend,
            "role": profile.role,
            "listen": f"{profile.listen_host}:{profile.listen_port}",
            "pairing_id": profile.pairing_id or "none",
            "peer_id": str(profile.peer_id) if profile.peer_id is not None else "none",
            "peer_name": profile.peer_name or "none",
            "answer": "yes" if profile.answer else "no",
            "auto_start": "yes" if profile.auto_start else "no",
        }
