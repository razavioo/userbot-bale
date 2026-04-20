"""User auth/session persistence for Bale and future login flows."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from baleobala.control.paths import config_dir
from baleobala.control.keychain import SecretBackend, default_secret_backend
from baleobala.control.store import JsonStore


@dataclass(frozen=True)
class AuthRecord:
    provider: str
    jwt: str
    user_id: int | None = None
    phone: str | None = None
    secret_name: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthRecord":
        return cls(
            provider=str(data.get("provider", "bale")),
            jwt=str(data.get("jwt", "")),
            user_id=data.get("user_id"),
            phone=data.get("phone"),
            secret_name=data.get("secret_name"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


class AuthStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "auth.json")
        self._store = JsonStore(self.path)
        self._secret_backend: SecretBackend = default_secret_backend()
        self._secret_name = "auth.jwt"

    def _metadata_for_save(self, record: AuthRecord) -> dict[str, Any]:
        payload = record.to_dict()
        payload.pop("jwt", None)
        payload.setdefault("secret_name", record.secret_name or self._secret_name)
        return payload

    def load(self) -> AuthRecord | None:
        payload = self._store.load(default=None)
        if not payload:
            return None
        record = AuthRecord.from_dict(payload)
        secret_name = record.secret_name or self._secret_name
        jwt = self._secret_backend.load(secret_name)
        if jwt is None:
            return None
        return AuthRecord(
            provider=record.provider,
            jwt=jwt,
            user_id=record.user_id,
            phone=record.phone,
            secret_name=secret_name,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def save(self, record: AuthRecord) -> None:
        secret_name = record.secret_name or self._secret_name
        self._secret_backend.save(secret_name, record.jwt)
        self._store.save(self._metadata_for_save(record))

    def save_jwt(self, jwt: str, *, provider: str = "bale", user_id: int | None = None, phone: str | None = None) -> AuthRecord:
        record = AuthRecord(provider=provider, jwt=jwt, user_id=user_id, phone=phone, secret_name=self._secret_name)
        self.save(record)
        return record

    def clear(self) -> None:
        self._secret_backend.delete(self._secret_name)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        record = self.load()
        if record is None:
            return {"state": "empty", "secret_backend": type(self._secret_backend).__name__}
        masked = record.jwt[:6] + "…" if record.jwt else "missing"
        return {
            "state": "configured",
            "provider": record.provider,
            "user_id": str(record.user_id) if record.user_id is not None else "unknown",
            "phone": record.phone or "unknown",
            "jwt": masked,
            "secret_backend": type(self._secret_backend).__name__,
        }
