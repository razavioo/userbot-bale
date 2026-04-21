"""Shared control-service facade for CLI and desktop flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from baleobala.control.auth import AuthRecord, AuthStore
from baleobala.control.backend import VpnBackend, backend_for_profile
from baleobala.control.pairing import PairingRecord, PairingStore
from baleobala.control.vpn import VpnProfile, VpnStore


BackendFactory = Callable[[VpnProfile], VpnBackend]


@dataclass(frozen=True)
class ControlSnapshot:
    auth: dict[str, str]
    vpn: dict[str, str]
    pairing: dict[str, str]
    backend: dict[str, str]


class ControlService:
    """Own the shared product state used by both CLI and GUI."""

    def __init__(
        self,
        *,
        auth_store: AuthStore | None = None,
        pairing_store: PairingStore | None = None,
        vpn_store: VpnStore | None = None,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        self.auth_store = auth_store or AuthStore()
        self.pairing_store = pairing_store or PairingStore()
        self.vpn_store = vpn_store or VpnStore()
        self._backend_factory = backend_factory or backend_for_profile

    def load_auth(self) -> AuthRecord | None:
        return self.auth_store.load()

    def save_auth_jwt(
        self,
        jwt: str,
        *,
        user_id: int | None = None,
        phone: str | None = None,
    ) -> AuthRecord:
        return self.auth_store.save_jwt(jwt, user_id=user_id, phone=phone)

    def clear_auth(self) -> None:
        self.auth_store.clear()

    def load_pairing(self, profile_id: str | None = None) -> PairingRecord | None:
        if profile_id is not None:
            return self.pairing_store.get(profile_id)
        return self.pairing_store.active()

    def load_profile(self) -> VpnProfile | None:
        return self.vpn_store.load()

    def ensure_profile(self) -> VpnProfile:
        return self.vpn_store.ensure_default()

    def save_profile(self, profile: VpnProfile) -> None:
        self.vpn_store.save(profile)

    def backend(self, profile: VpnProfile | None = None) -> VpnBackend:
        active_profile = profile or self.vpn_store.load() or self.vpn_store.ensure_default()
        return self._backend_factory(active_profile)

    def status(self) -> ControlSnapshot:
        profile = self.vpn_store.load() or self.vpn_store.ensure_default()
        backend = self._backend_factory(profile)
        pairing = self.pairing_store.active()
        return ControlSnapshot(
            auth=self.auth_store.status(),
            vpn=self.vpn_store.status(),
            pairing={"state": "empty"} if pairing is None else {
                "state": pairing.status,
                "profile_id": pairing.profile_id,
                "name": pairing.name,
                "role": pairing.role,
            },
            backend=backend.status(),
        )

