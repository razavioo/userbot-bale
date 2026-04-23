"""Persistence for Bale MTProto session state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from baleobala.bale.mtproto.authkey import MtprotoAuthKey
from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


@dataclass(frozen=True)
class PersistedMtprotoSession:
    endpoint_host: str
    endpoint_port: int
    endpoint_scheme: str
    endpoint_pin: str
    auth_key_id: str
    auth_key_hex: str
    session_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersistedMtprotoSession":
        return cls(
            endpoint_host=str(data.get("endpoint_host", "")),
            endpoint_port=int(data.get("endpoint_port", 0)),
            endpoint_scheme=str(data.get("endpoint_scheme", "tls")),
            endpoint_pin=str(data.get("endpoint_pin", "")),
            auth_key_id=str(data.get("auth_key_id", "")),
            auth_key_hex=str(data.get("auth_key_hex", "")),
            session_id=str(data.get("session_id", "")),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )

    def auth_key(self) -> MtprotoAuthKey:
        return MtprotoAuthKey(
            key_id=self.auth_key_id,
            key_hex=self.auth_key_hex,
            fingerprint=self.endpoint_pin,
            created_at=self.created_at,
        )


class MtprotoSessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "bale_mtproto_session.json")
        self._store = JsonStore(self.path)

    def load(self) -> PersistedMtprotoSession | None:
        payload = self._store.load(default=None)
        if not isinstance(payload, dict):
            return None
        session = PersistedMtprotoSession.from_dict(payload)
        if not session.auth_key_id or not session.auth_key_hex:
            return None
        return session

    def save(self, session: PersistedMtprotoSession) -> None:
        self._store.save(session.to_dict())
