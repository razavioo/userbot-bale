"""Shared control-service facade for CLI and desktop flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from baleobala.control.auth import AuthRecord, AuthStore
from baleobala.control.backend import MacOSPacketTunnelBackend, VpnBackend, backend_for_profile
from baleobala.control.probe import ProbeResult
from baleobala.control.pairing import PairingExchange, PairingRecord, PairingStore
from baleobala.control.tunnel_service import LocalTunnelService
from baleobala.control.vpn import VpnProfile, VpnStore


BackendFactory = Callable[[VpnProfile], VpnBackend]


@dataclass(frozen=True)
class ControlSnapshot:
    auth: dict[str, str]
    vpn: dict[str, str]
    pairing: dict[str, str]
    backend: dict[str, str]


@dataclass(frozen=True)
class ConnectionSnapshot:
    profile: VpnProfile | None
    pairing: PairingRecord | None
    backend: dict[str, str]
    probe: dict[str, str]


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

    def begin_pairing(
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
        return self.pairing_store.begin(
            name,
            role=role,
            peer_id=peer_id,
            peer_name=peer_name,
            relay_mode=relay_mode,
            backend_preference=backend_preference,
            transport_preference=transport_preference,
            secret_name=secret_name,
        )

    def accept_pairing(
        self,
        code: str,
        *,
        name: str | None = None,
        peer_id: int | None = None,
        peer_name: str | None = None,
    ) -> PairingRecord:
        return self.pairing_store.accept(
            code,
            name=name,
            peer_id=peer_id,
            peer_name=peer_name,
            validate=True,
        )

    def export_pairing_request(self, profile_id: str) -> PairingExchange:
        return self.pairing_store.export_request(profile_id)

    def accept_pairing_request(
        self,
        exchange: PairingExchange,
        *,
        name: str | None = None,
        peer_id: int | None = None,
        peer_name: str | None = None,
        backend_preference: str | None = None,
        transport_preference: str | None = None,
    ) -> PairingExchange:
        return self.pairing_store.accept_request(
            exchange,
            name=name,
            peer_id=peer_id,
            peer_name=peer_name,
            backend_preference=backend_preference,
            transport_preference=transport_preference,
        )

    def apply_pairing_response(self, exchange: PairingExchange) -> PairingRecord:
        return self.pairing_store.apply_response(exchange)

    def backend(self, profile: VpnProfile | None = None) -> VpnBackend:
        active_profile = profile or self.vpn_store.load() or self.vpn_store.ensure_default()
        backend = self._backend_factory(active_profile)
        if (
            active_profile.backend == "packet-tunnel"
            and isinstance(backend, MacOSPacketTunnelBackend)
            and getattr(backend, "_service", None) is None
        ):
            return MacOSPacketTunnelBackend(service=LocalTunnelService())
        return backend

    def resolve_profile(self, profile_id: str | None = None) -> VpnProfile:
        profile = self.vpn_store.load() or self.vpn_store.ensure_default()
        pairing = self.pairing_store.connectable(profile_id or profile.pairing_id)
        if pairing is None:
            pairing = self.pairing_store.connectable()
        if pairing is None:
            return profile
        resolved = VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend=pairing.backend_preference or profile.backend,
            role=pairing.role,
            pairing_id=pairing.profile_id,
            peer_id=pairing.peer_id,
            peer_name=pairing.peer_name,
            answer=pairing.role == "relay",
            auto_start=profile.auto_start,
            listen_host=profile.listen_host,
            listen_port=profile.listen_port,
            protocol=profile.protocol,
            volume=profile.volume,
            proxy_secret=profile.proxy_secret,
        )
        self.vpn_store.save(resolved)
        self.pairing_store.touch(pairing.profile_id)
        return resolved

    def start_connection(self, profile_id: str | None = None) -> ConnectionSnapshot:
        profile = self.resolve_profile(profile_id)
        auth = self.load_auth()
        backend = self.backend(profile)
        pairing = self.pairing_store.connectable(profile.pairing_id)
        status = backend.up(profile, auth, pairing)
        if pairing is not None:
            self.pairing_store.update_runtime_metadata(
                pairing.profile_id,
                backend=status.get("backend"),
                transport=status.get("transport_selected"),
                validation_error=status.get("last_error", ""),
            )
        probe = backend.probe().to_dict()
        return ConnectionSnapshot(profile=profile, pairing=pairing, backend=status, probe=probe)

    def stop_connection(self, profile_id: str | None = None) -> ConnectionSnapshot:
        profile = self.resolve_profile(profile_id)
        backend = self.backend(profile)
        backend.down()
        return ConnectionSnapshot(
            profile=profile,
            pairing=self.pairing_store.get(profile.pairing_id or profile.profile_id),
            backend=backend.status(),
            probe=backend.probe().to_dict(),
        )

    def probe_connection(self, profile_id: str | None = None) -> ProbeResult:
        profile = self.resolve_profile(profile_id)
        return self.backend(profile).probe()

    def reconcile_runtime(self) -> ConnectionSnapshot:
        profile = self.vpn_store.load() or self.vpn_store.ensure_default()
        pairing = self.pairing_store.get(profile.pairing_id) if profile.pairing_id else None
        backend = self.backend(profile)
        status = backend.status()
        probe = backend.probe().to_dict()
        if status.get("state") == "running" and probe.get("ok") == "no":
            backend.down()
            status = backend.status()
            status["state"] = "degraded"
            status["last_error"] = probe.get("detail", status.get("last_error", ""))
            if pairing is not None:
                self.pairing_store.update_runtime_metadata(
                    pairing.profile_id,
                    backend=status.get("backend"),
                    transport=status.get("transport_selected"),
                    validation_error=status.get("last_error", ""),
                )
        return ConnectionSnapshot(profile=profile, pairing=pairing, backend=status, probe=probe)

    def status(self) -> ControlSnapshot:
        profile = self.resolve_profile()
        backend = self._backend_factory(profile)
        pairing = self.pairing_store.connectable(profile.pairing_id) or self.pairing_store.active()
        return ControlSnapshot(
            auth=self.auth_store.status(),
            vpn=self.vpn_store.status(),
            pairing={"state": "empty"} if pairing is None else {
                "state": pairing.status,
                "profile_id": pairing.profile_id,
                "name": pairing.name,
                "role": pairing.role,
                "relay_mode": pairing.relay_mode,
                "backend_preference": pairing.backend_preference,
                "transport_preference": pairing.transport_preference,
                "provisioning_status": pairing.provisioning_status,
                "peer_name": pairing.peer_name or "",
            },
            backend=backend.status(),
        )
