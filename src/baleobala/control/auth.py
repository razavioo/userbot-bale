"""User auth/session persistence for Bale and future login flows."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from baleobala.control.paths import config_dir
from baleobala.control.keychain import SecretBackend, default_secret_backend
from baleobala.control.store import JsonStore
from baleobala.vpn.jwt_util import inspect as inspect_jwt


@dataclass(frozen=True)
class AuthRecord:
    provider: str
    jwt: str
    user_id: int | None = None
    phone: str | None = None
    secret_name: str | None = None
    issued_at: float | None = None
    expires_at: float | None = None
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
            issued_at=(
                float(data["issued_at"])
                if data.get("issued_at") not in {None, ""}
                else None
            ),
            expires_at=(
                float(data["expires_at"])
                if data.get("expires_at") not in {None, ""}
                else None
            ),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )

    def seconds_until_expiry(self) -> float | None:
        if self.expires_at is None:
            return None
        return self.expires_at - time.time()

    def is_expired(self) -> bool:
        ttl = self.seconds_until_expiry()
        return ttl is not None and ttl < 0


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

    def _record_for_jwt(
        self,
        jwt: str,
        *,
        provider: str = "bale",
        user_id: int | None = None,
        phone: str | None = None,
        secret_name: str | None = None,
    ) -> AuthRecord:
        info = inspect_jwt(jwt)
        return AuthRecord(
            provider=provider,
            jwt=jwt,
            user_id=user_id,
            phone=phone,
            secret_name=secret_name or self._secret_name,
            issued_at=float(info.iat) if info.iat is not None else None,
            expires_at=float(info.exp) if info.exp is not None else None,
        )

    def _load_metadata(self) -> AuthRecord | None:
        payload = self._store.load(default=None)
        if not payload:
            return None
        return AuthRecord.from_dict(payload)

    def load(self) -> AuthRecord | None:
        record = self._load_metadata()
        if record is None:
            return None
        secret_name = record.secret_name or self._secret_name
        jwt = self._secret_backend.load(secret_name)
        if jwt is None:
            return None
        record = AuthRecord(
            provider=record.provider,
            jwt=jwt,
            user_id=record.user_id,
            phone=record.phone,
            secret_name=secret_name,
            issued_at=record.issued_at,
            expires_at=record.expires_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        if record.is_expired():
            return None
        return record

    def save(self, record: AuthRecord) -> None:
        secret_name = record.secret_name or self._secret_name
        self._secret_backend.save(secret_name, record.jwt)
        self._store.save(self._metadata_for_save(record))

    def save_jwt(self, jwt: str, *, provider: str = "bale", user_id: int | None = None, phone: str | None = None) -> AuthRecord:
        record = self._record_for_jwt(
            jwt,
            provider=provider,
            user_id=user_id,
            phone=phone,
            secret_name=self._secret_name,
        )
        self.save(record)
        self._export_to_accounts(jwt)
        return record

    def _export_to_accounts(self, jwt: str) -> None:
        import re
        from pathlib import Path
        
        accounts_dir = Path.home() / ".baleobala" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if this exact JWT is already saved to avoid duplicates
        for f in accounts_dir.glob("account-*.jwt"):
            try:
                if f.read_text(encoding="utf-8").strip() == jwt.strip():
                    return
            except OSError:
                pass

        max_num = 0
        for f in accounts_dir.glob("account-*.jwt"):
            m = re.search(r'account-(\d+)\.jwt', f.name)
            if m:
                num = int(m.group(1))
                if num > max_num:
                    max_num = num
                    
        target_file = accounts_dir / f"account-{max_num + 1}.jwt"
        target_file.write_text(jwt.strip() + "\n", encoding="utf-8")

    def clear(self) -> None:
        self._secret_backend.delete(self._secret_name)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        metadata = self._load_metadata()
        if metadata is None:
            return {"state": "empty", "secret_backend": type(self._secret_backend).__name__}
        secret_name = metadata.secret_name or self._secret_name
        jwt = self._secret_backend.load(secret_name)
        if jwt is None:
            return {
                "state": "missing-secret",
                "provider": metadata.provider,
                "user_id": str(metadata.user_id) if metadata.user_id is not None else "unknown",
                "phone": metadata.phone or "unknown",
                "secret_backend": type(self._secret_backend).__name__,
            }
        record = AuthRecord(
            provider=metadata.provider,
            jwt=jwt,
            user_id=metadata.user_id,
            phone=metadata.phone,
            secret_name=secret_name,
            issued_at=metadata.issued_at,
            expires_at=metadata.expires_at,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )
        ttl = record.seconds_until_expiry()
        if record.is_expired():
            return {
                "state": "expired",
                "provider": record.provider,
                "user_id": str(record.user_id) if record.user_id is not None else "unknown",
                "phone": record.phone or "unknown",
                "jwt": record.jwt[:6] + "…" if record.jwt else "missing",
                "expires_in": f"{ttl:.0f}s" if ttl is not None else "unknown",
                "secret_backend": type(self._secret_backend).__name__,
            }
        masked = record.jwt[:6] + "…" if record.jwt else "missing"
        return {
            "state": "configured",
            "provider": record.provider,
            "user_id": str(record.user_id) if record.user_id is not None else "unknown",
            "phone": record.phone or "unknown",
            "jwt": masked,
            "expires_in": f"{ttl:.0f}s" if ttl is not None else "unknown",
            "secret_backend": type(self._secret_backend).__name__,
        }
