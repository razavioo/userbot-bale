"""Security provider implementations for the tunnel runtime."""

from __future__ import annotations

from dataclasses import dataclass

from baleobala.runtime.interfaces import SecurityProvider


@dataclass(frozen=True)
class NullSecurityProvider:
    """Identity provider used while the real E2E crypto is still pending."""

    session_id: str = "null"

    def start_handshake(self, role: str) -> bytes:
        return role.encode("ascii", errors="strict")

    def seal(self, payload: bytes) -> bytes:
        return bytes(payload)

    def open(self, payload: bytes) -> bytes:
        return bytes(payload)

    def session_info(self) -> dict[str, str]:
        return {"provider": "null", "session_id": self.session_id}

