"""Shared control-service facade for CLI and desktop flows."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
from typing import Callable

import baleobala.control.android as android
from baleobala.control.auth import AuthRecord, AuthStore
from baleobala.control.backend import VpnBackend, backend_for_profile
import baleobala.control.macos as macos
from baleobala.control.credential_watcher import CredentialWatcher
from baleobala.control.mesh import MeshProvisionRecord, MeshProvisionStore
from baleobala.control.probe import ProbeResult
from baleobala.control.pairing import PairingExchange, PairingRecord, PairingStore
from baleobala.control.relay_directory import RelayDirectory, RelayDirectoryEntry, relay_id_for_name
from baleobala.control.provisioning import (
    DeviceAuthorization,
    ProvisioningService,
    RelayEnrollment,
)
from baleobala.control.vpn import VpnProfile, VpnStore


BackendFactory = Callable[[VpnProfile], VpnBackend]


@dataclass(frozen=True)
class ControlSnapshot:
    auth: dict[str, str]
    vpn: dict[str, str]
    pairing: dict[str, str]
    mesh: dict[str, str]
    backend: dict[str, str]
    connection: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionSnapshot:
    profile: VpnProfile | None
    pairing: PairingRecord | None
    backend: dict[str, str]
    probe: dict[str, str]
    connection: dict[str, str] = field(default_factory=dict)


class ControlService:
    """Own the shared product state used by both CLI and GUI."""

    def __init__(
        self,
        *,
        auth_store: AuthStore | None = None,
        pairing_store: PairingStore | None = None,
        mesh_store: MeshProvisionStore | None = None,
        provisioning: ProvisioningService | None = None,
        vpn_store: VpnStore | None = None,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        self.auth_store = auth_store or AuthStore()
        self.pairing_store = pairing_store or PairingStore()
        self.mesh_store = mesh_store or MeshProvisionStore()
        self.provisioning = provisioning or ProvisioningService()
        self.vpn_store = vpn_store or VpnStore()
        self._backend_factory = backend_factory or backend_for_profile
        self.relay_directory = RelayDirectory()
        self._active_backend: VpnBackend | None = None
        self._active_profile_id: str | None = None
        self._credential_watcher: CredentialWatcher | None = None

    def _profile_from_pairing(self, profile: VpnProfile, pairing: PairingRecord) -> VpnProfile:
        return VpnProfile(
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

    def _runtime_profile(self, profile_id: str | None = None) -> VpnProfile:
        profile = self.vpn_store.load() or self.vpn_store.ensure_default()
        if profile_id is None or profile.profile_id == profile_id:
            return profile
        pairing = self.pairing_store.get(profile_id)
        if pairing is not None:
            return self._profile_from_pairing(profile, pairing)
        return profile

    def _current_profile(self, profile_id: str | None = None) -> VpnProfile:
        if profile_id is not None:
            return self._runtime_profile(profile_id)
        if self._active_profile_id is not None:
            active_profile = self._runtime_profile(self._active_profile_id)
            if active_profile.profile_id == self._active_profile_id:
                return active_profile
        return self._runtime_profile()

    def _pairing_for_profile(self, profile: VpnProfile) -> PairingRecord | None:
        if profile.pairing_id:
            return self.pairing_store.get(profile.pairing_id)
        return self.pairing_store.active()

    def _profile_with_pairing_secret(self, profile: VpnProfile, pairing: PairingRecord | None) -> VpnProfile:
        if pairing is None or not pairing.secret_name or profile.proxy_secret:
            return profile
        secret = self.pairing_store.load_secret(pairing.profile_id)
        if not secret:
            return profile
        return replace(profile, proxy_secret=secret)

    def _connection_gate(
        self,
        profile: VpnProfile,
        pairing: PairingRecord | None,
        *,
        auth_status: dict[str, str] | None = None,
    ) -> dict[str, str]:
        auth_status = auth_status or self.auth_store.status()
        gate: dict[str, str] = {
            "state": "ready",
            "code": "",
            "title": "",
            "message": "",
            "next_step": f"run `baleobala vpn up` or open `baleobala gui`.",
            "backend": profile.backend,
            "profile_id": profile.profile_id,
            "pairing_id": pairing.profile_id if pairing is not None else "",
            "auth_state": auth_status.get("state", "empty"),
            "pairing_state": pairing.status if pairing is not None else "empty",
            "authorization_status": pairing.authorization_status if pairing is not None else "",
            "provisioning_status": pairing.provisioning_status if pairing is not None else "",
        }

        if profile.backend in {"direct", "windows-proxy"}:
            gate["title"] = "Direct proxy ready" if profile.backend == "direct" else "Windows proxy ready"
            gate["message"] = "This backend does not require relay provisioning."
            return gate

        if pairing is None:
            gate.update(
                {
                    "state": "blocked",
                    "code": "pairing_missing",
                    "title": "Relay pairing needed",
                    "message": "No relay pairing is ready yet. Create or accept a pairing first.",
                    "next_step": "run `baleobala pair enroll --name home-relay`.",
                }
            )
            return gate

        revoked = pairing.revoked_at is not None or pairing.authorization_status == "revoked" or pairing.provisioning_status == "revoked" or pairing.status == "revoked"
        rejected = pairing.authorization_status == "rejected" or pairing.provisioning_status == "rejected" or pairing.status == "rejected"
        pending = pairing.authorization_status == "pending" or pairing.provisioning_status in {"pending", "enrolled"}
        incomplete = pairing.provisioning_status not in {"complete", "accepted", "paired"}

        if pairing.relay_id:
            if auth_status.get("state") == "expired":
                gate.update(
                    {
                        "state": "blocked",
                        "code": "auth_expired",
                        "title": "Session expired",
                        "message": "Saved Bale session expired. Sign in again to refresh relay provisioning.",
                        "next_step": "run `baleobala auth bale-login --phone +98912xxxxxxx --save`.",
                    }
                )
                return gate
            if auth_status.get("state") in {"empty", "missing-secret"}:
                gate.update(
                    {
                        "state": "blocked",
                        "code": "auth_missing",
                        "title": "Sign in again",
                        "message": "No saved Bale session is available. Sign in before requesting or refreshing relay access.",
                        "next_step": "run `baleobala auth bale-login --phone +98912xxxxxxx --save`.",
                    }
                )
                return gate

        if revoked:
            gate.update(
                {
                    "state": "blocked",
                    "code": "access_revoked",
                    "title": "Relay access revoked",
                    "message": "Relay access was revoked for this device. Ask the relay owner to approve it again.",
                    "next_step": f"run `baleobala pair sync --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        if rejected:
            gate.update(
                {
                    "state": "blocked",
                    "code": "access_rejected",
                    "title": "Relay request rejected",
                    "message": "Relay access was rejected. Ask the relay owner to approve the request again.",
                    "next_step": f"run `baleobala pair request-access --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        if pending:
            gate.update(
                {
                    "state": "blocked",
                    "code": "provisioning_pending",
                    "title": "Relay approval pending",
                    "message": "Relay provisioning is still pending approval. Request access, approve it, then sync credentials.",
                    "next_step": f"run `baleobala pair request-access --profile-id {pairing.profile_id}`, then `baleobala pair approve --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        if incomplete:
            gate.update(
                {
                    "state": "blocked",
                    "code": "provisioning_missing",
                    "title": "Relay provisioning incomplete",
                    "message": "Relay provisioning is incomplete. Request access, approve it, and sync credentials.",
                    "next_step": f"run `baleobala pair sync --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        if pairing.credential_expires_at is not None and pairing.credential_expires_at <= time.time():
            gate.update(
                {
                    "state": "blocked",
                    "code": "credentials_expired",
                    "title": "Relay credentials expired",
                    "message": "Relay credentials expired. Refresh the pairing credentials.",
                    "next_step": f"run `baleobala pair sync --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        if pairing.secret_name and self.pairing_store.load_secret(pairing.profile_id) is None:
            gate.update(
                {
                    "state": "blocked",
                    "code": "credentials_missing",
                    "title": "Relay credentials missing",
                    "message": "Relay credentials are missing from the secret store. Sync the pairing again.",
                    "next_step": f"run `baleobala pair sync --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        if pairing.peer_id is None and pairing.role != "relay":
            gate.update(
                {
                    "state": "blocked",
                    "code": "peer_assignment_missing",
                    "title": "Relay peer missing",
                    "message": "Relay provisioning has not assigned a peer yet. Sync the pairing again after approval.",
                    "next_step": f"run `baleobala pair sync --profile-id {pairing.profile_id}`.",
                }
            )
            return gate

        return gate

    def _clear_active_backend(self, *, shutdown: bool = True) -> None:
        self._stop_credential_watcher()
        backend = self._active_backend
        self._active_backend = None
        self._active_profile_id = None
        if shutdown and backend is not None:
            try:
                backend.down()
            except Exception:
                pass

    def _start_credential_watcher(self, profile: VpnProfile, pairing: PairingRecord | None, backend: VpnBackend) -> None:
        if pairing is None:
            return
        if profile.backend == "linux-tun" and hasattr(backend, "set_credential_refresh_context"):
            return
        if profile.backend not in {"packet-tunnel", "android-vpn"}:
            return
        self._stop_credential_watcher()
        self._credential_watcher = CredentialWatcher(
            self.pairing_store,
            self,
            pairing.profile_id,
            lambda _record: None,
        )
        self._credential_watcher.start()

    def _stop_credential_watcher(self) -> None:
        if self._credential_watcher is None:
            return
        self._credential_watcher.stop()
        self._credential_watcher = None

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

    def issue_mesh_assignment(
        self,
        peer_id: int,
        *,
        pool_cidr: str,
        profile_id: str | None = None,
        peer_name: str | None = None,
        transport: str = "",
        session_id: int | None = None,
    ) -> MeshProvisionRecord:
        return self.mesh_store.issue_assignment(
            peer_id,
            pool_cidr=pool_cidr,
            profile_id=profile_id,
            peer_name=peer_name,
            transport=transport,
            session_id=session_id,
        )

    def activate_mesh_assignment(
        self,
        peer_id: int,
        *,
        transport: str = "",
        session_id: int | None = None,
    ) -> MeshProvisionRecord:
        return self.mesh_store.activate(
            peer_id,
            transport=transport,
            session_id=session_id,
        )

    def release_mesh_assignment(
        self,
        peer_id: int,
        *,
        error: str = "",
    ) -> MeshProvisionRecord | None:
        return self.mesh_store.release(peer_id, error=error)

    def lookup_mesh_assignment(self, peer_id: int) -> MeshProvisionRecord | None:
        return self.mesh_store.get(peer_id)

    def begin_pairing(
        self,
        name: str,
        *,
        role: str = "client",
        peer_id: int | None = None,
        peer_name: str | None = None,
        relay_name: str | None = None,
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
            relay_name=relay_name,
            relay_mode=relay_mode,
            backend_preference=backend_preference,
            transport_preference=transport_preference,
            secret_name=secret_name,
        )

    def enroll_pairing(
        self,
        name: str,
        *,
        role: str = "client",
        peer_id: int | None = None,
        peer_name: str | None = None,
        relay_name: str | None = None,
        relay_mode: str = "proxy",
        backend_preference: str | None = None,
        transport_preference: str = "auto",
    ) -> PairingRecord:
        record = self.pairing_store.begin(
            name,
            role=role,
            peer_id=peer_id,
            peer_name=peer_name,
            relay_name=relay_name,
            relay_mode=relay_mode,
            backend_preference=backend_preference,
            transport_preference=transport_preference,
        )
        enrollment = self.provisioning.enroll_relay(
            self.load_auth(),
            name=name,
            role=role,
            relay_mode=relay_mode,
            backend_preference=backend_preference or relay_mode,
            transport_preference=transport_preference,
            peer_id=peer_id,
        )
        return self.pairing_store.save_provisioning(
            record.profile_id,
            relay_id=enrollment.relay_id,
            device_id=self.provisioning.ensure_device_id(),
            authorization_status="none",
            provisioning_status="enrolled",
            peer_id=enrollment.peer_id,
            peer_name=peer_name or enrollment.name,
        )

    def list_relays(self) -> list[RelayEnrollment]:
        return self.provisioning.list_relays(self.load_auth())

    def request_pairing_access(self, profile_id: str) -> PairingRecord:
        record = self._pairing_or_raise(profile_id)
        authz = self.provisioning.request_authorization(
            self.load_auth(),
            relay_id=record.relay_id,
            device_id=self.provisioning.ensure_device_id(),
        )
        return self.pairing_store.save_provisioning(
            record.profile_id,
            relay_id=record.relay_id,
            device_id=authz.device_id,
            authorization_id=authz.authorization_id,
            authorization_status=authz.status,
            provisioning_status="pending",
            server_error="",
        )

    def approve_pairing_access(self, profile_id: str) -> PairingRecord:
        record = self._pairing_or_raise(profile_id)
        authz = self.provisioning.approve_authorization(
            self.load_auth(),
            relay_id=record.relay_id,
            authorization_id=record.authorization_id,
        )
        return self.sync_pairing(profile_id, authorization=authz)

    def reject_pairing_access(self, profile_id: str, *, reason: str = "") -> PairingRecord:
        record = self._pairing_or_raise(profile_id)
        authz = self.provisioning.reject_authorization(
            self.load_auth(),
            relay_id=record.relay_id,
            authorization_id=record.authorization_id,
            reason=reason,
        )
        return self.pairing_store.save_provisioning(
            profile_id,
            authorization_status=authz.status,
            provisioning_status="rejected",
            server_error=authz.rejected_reason or "authorization rejected",
        )

    def revoke_pairing_device(self, profile_id: str) -> PairingRecord:
        record = self._pairing_or_raise(profile_id)
        authz = self.provisioning.revoke_device(
            self.load_auth(),
            relay_id=record.relay_id,
            device_id=record.device_id or self.provisioning.ensure_device_id(),
        )
        return self.pairing_store.save_provisioning(
            profile_id,
            authorization_status=authz.status,
            provisioning_status="revoked",
            revoked_at=authz.revoked_at,
            server_error="device revoked",
        )

    def sync_pairing(
        self,
        profile_id: str,
        *,
        authorization: DeviceAuthorization | None = None,
    ) -> PairingRecord:
        record = self._pairing_or_raise(profile_id)
        if not record.relay_id:
            return record
        authz = authorization
        if authz is None and record.authorization_id:
            authz = self.provisioning.get_authorization(
                self.load_auth(),
                relay_id=record.relay_id,
                authorization_id=record.authorization_id,
            )
        if authz is None:
            return self.pairing_store.mark_server_error(profile_id, "authorization has not been requested")
        peer = self.provisioning.get_peer(
            self.load_auth(),
            relay_id=record.relay_id,
            device_id=record.device_id or self.provisioning.ensure_device_id(),
        )
        secret_value = peer.credential_epoch.secret_value if peer.credential_epoch is not None else None
        credential_epoch = peer.credential_epoch.epoch_id if peer.credential_epoch is not None else ""
        credential_expires_at = peer.credential_epoch.expires_at if peer.credential_epoch is not None else None
        credential_refresh_after = peer.credential_epoch.refresh_after if peer.credential_epoch is not None else None
        server_error = ""
        if authz.status == "rejected":
            server_error = authz.rejected_reason or "authorization rejected"
        elif authz.status == "revoked":
            server_error = "device revoked"
        elif authz.status != "approved":
            server_error = "waiting for relay-owner approval"
        return self.pairing_store.save_provisioning(
            profile_id,
            device_id=record.device_id or self.provisioning.ensure_device_id(),
            authorization_id=authz.authorization_id,
            authorization_status=authz.status,
            provisioning_status=peer.provisioning_status,
            credential_epoch=credential_epoch,
            credential_expires_at=credential_expires_at,
            credential_refresh_after=credential_refresh_after,
            peer_id=peer.peer_id,
            peer_name=peer.peer_name,
            secret_value=secret_value,
            revoked_at=authz.revoked_at,
            server_error=server_error,
        )

    def refresh_pairing_credentials(self, profile_id: str) -> PairingRecord:
        record = self._pairing_or_raise(profile_id)
        epoch = self.provisioning.refresh_credentials(self.load_auth(), relay_id=record.relay_id)
        return self.pairing_store.save_provisioning(
            profile_id,
            credential_epoch=epoch.epoch_id,
            credential_expires_at=epoch.expires_at,
            credential_refresh_after=epoch.refresh_after,
            secret_value=epoch.secret_value,
            provisioning_status="complete",
        )

    def accept_pairing(
        self,
        code: str,
        *,
        name: str | None = None,
        peer_id: int | None = None,
        peer_name: str | None = None,
        relay_name: str | None = None,
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

    def export_pairing_invite(self, profile_id: str) -> str:
        return self.pairing_store.export_invite_link(profile_id)

    def import_pairing_invite(self, link: str) -> PairingRecord:
        return self.pairing_store.import_invite_link(link)

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

    def lookup_relay(self, name: str) -> RelayDirectoryEntry | None:
        return self.relay_directory.lookup(name)

    def list_relays(self) -> list[RelayDirectoryEntry]:
        return self.relay_directory.list()

    def publish_relay(
        self,
        name: str,
        *,
        profile_id: str | None = None,
        peer_id: int | None = None,
        owner: str = "",
        transport_preference: str = "auto",
        backend_preference: str = "proxy",
        endpoint_hint: str = "",
    ) -> RelayDirectoryEntry:
        auth = self.load_auth()
        profile = self.vpn_store.load() or self.vpn_store.ensure_default()
        pairing = self.pairing_store.get(profile_id) if profile_id else None
        if pairing is None:
            pairing = self.pairing_store.active() or self.pairing_store.connectable()
        if peer_id is None:
            if pairing is not None and pairing.peer_id is not None:
                peer_id = pairing.peer_id
            elif profile.peer_id is not None:
                peer_id = profile.peer_id
        if peer_id is None:
            raise ValueError("relay publish needs a peer_id or a paired relay profile")
        if not owner:
            if auth is not None and auth.phone:
                owner = auth.phone
            elif auth is not None and auth.user_id is not None:
                owner = f"user-{auth.user_id}"
            else:
                owner = "local-user"
        if not backend_preference:
            backend_preference = profile.backend
        if not endpoint_hint:
            endpoint_hint = f"{profile.listen_host}:{profile.listen_port}"
        entry = RelayDirectoryEntry(
            relay_id=relay_id_for_name(name),
            name=name,
            peer_id=peer_id,
            owner=owner,
            transport_preference=transport_preference,
            backend_preference=backend_preference,
            endpoint_hint=endpoint_hint,
        )
        return self.relay_directory.register(entry)

    def remove_relay(self, identifier: str) -> RelayDirectoryEntry | None:
        return self.relay_directory.remove(identifier)

    def backend(self, profile: VpnProfile | None = None) -> VpnBackend:
        active_profile = profile or self.vpn_store.load() or self.vpn_store.ensure_default()
        return self._backend_factory(active_profile)

    def resolve_profile(self, profile_id: str | None = None) -> VpnProfile:
        profile = self.vpn_store.load() or self.vpn_store.ensure_default()
        target_profile_id = profile_id or profile.pairing_id
        if target_profile_id:
            pairing = self.pairing_store.get(target_profile_id)
            if pairing is not None and getattr(pairing, "relay_id", ""):
                pairing = self.sync_pairing(target_profile_id)
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

    def resolve_relay_pairing(self, relay_name: str, *, pair_name: str | None = None) -> PairingRecord:
        relay = self.relay_directory.lookup(relay_name)
        if relay is None:
            raise LookupError(f"no relay {relay_name!r} found")
        pairing = next(
            (
                item
                for item in self.pairing_store.list()
                if item.peer_id == relay.peer_id or item.name == relay.name
            ),
            None,
        )
        if pairing is not None:
            return pairing
        return self.begin_pairing(
            pair_name or relay.name,
            role="client",
            peer_id=relay.peer_id,
            peer_name=relay.name,
            relay_name=relay.name,
            backend_preference=relay.backend_preference,
            transport_preference=relay.transport_preference,
        )

    def start_connection(self, profile_id: str | None = None) -> ConnectionSnapshot:
        profile = self._runtime_profile(profile_id)
        auth_status = self.auth_store.status()
        pairing = self._pairing_for_profile(profile)
        gate = self._connection_gate(profile, pairing, auth_status=auth_status)
        if gate["state"] == "blocked":
            raise RuntimeError(gate["message"])

        if profile.backend == "linux-tun":
            target_pairing_id = profile.pairing_id or (pairing.profile_id if pairing is not None else None)
            if target_pairing_id is not None:
                profile = self.resolve_profile(target_pairing_id)
            else:
                profile = self.resolve_profile()
            pairing = self.pairing_store.connectable(profile.pairing_id)
            gate = self._connection_gate(profile, pairing, auth_status=auth_status)
            if gate["state"] == "blocked":
                raise RuntimeError(gate["message"])

        if profile.backend == "packet-tunnel":
            profile = self._profile_with_pairing_secret(profile, pairing)
            tunnel_config = macos.packet_tunnel_configuration(profile, pairing)
            if not macos.tunnel_profile_installed(tunnel_config):
                macos.install_tunnel_profile(tunnel_config)
        if profile.backend == "android-vpn":
            profile = self._profile_with_pairing_secret(profile, pairing)
            tunnel_config = android.vpn_service_configuration(profile, pairing)
            if not android.vpn_profile_installed(tunnel_config):
                android.install_vpn_profile(tunnel_config)
        auth = self.load_auth()
        backend = self.backend(profile)
        if hasattr(backend, "set_credential_refresh_context"):
            backend.set_credential_refresh_context(self.pairing_store, self.refresh_pairing_credentials)  # type: ignore[attr-defined]
        self._clear_active_backend()
        try:
            status = backend.up(profile, auth, pairing)
        except Exception:
            try:
                backend.down()
            except Exception:
                pass
            raise
        self._active_backend = backend
        self._active_profile_id = profile.profile_id
        self._start_credential_watcher(profile, pairing, backend)
        if pairing is not None:
            self.pairing_store.update_runtime_metadata(
                pairing.profile_id,
                backend=status.get("backend"),
                transport=status.get("transport_selected"),
                validation_error=status.get("last_error", ""),
            )
        probe = backend.probe().to_dict()
        return ConnectionSnapshot(profile=profile, pairing=pairing, backend=status, probe=probe, connection=gate)

    def stop_connection(self, profile_id: str | None = None) -> ConnectionSnapshot:
        profile = self._current_profile(profile_id)
        pairing = self._pairing_for_profile(profile)
        gate = self._connection_gate(profile, pairing)
        backend = self._active_backend or self.backend(profile)
        self._stop_credential_watcher()
        try:
            backend.down()
        finally:
            if self._active_backend is backend:
                self._clear_active_backend(shutdown=False)
            elif self._active_backend is not None and self._active_profile_id in {None, profile.profile_id}:
                self._clear_active_backend()
        return ConnectionSnapshot(
            profile=profile,
            pairing=pairing,
            backend=backend.status(),
            probe=backend.probe().to_dict(),
            connection=gate,
        )

    def probe_connection(self, profile_id: str | None = None) -> ProbeResult:
        profile = self._current_profile(profile_id)
        backend = self._active_backend or self.backend(profile)
        return backend.probe()

    def reconcile_runtime(self) -> ConnectionSnapshot:
        profile = self._current_profile()
        pairing = self._pairing_for_profile(profile)
        gate = self._connection_gate(profile, pairing)
        backend = self._active_backend or self.backend(profile)
        status = backend.status()
        probe = backend.probe().to_dict()
        if self._active_backend is backend and status.get("state") != "running":
            self._clear_active_backend(shutdown=False)
        if status.get("state") == "running" and probe.get("ok") == "no":
            backend.down()
            if self._active_backend is backend or self._active_backend is not None:
                self._clear_active_backend(shutdown=False)
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
        return ConnectionSnapshot(profile=profile, pairing=pairing, backend=status, probe=probe, connection=gate)

    def status(self) -> ControlSnapshot:
        profile = self._current_profile()
        pairing = self._pairing_for_profile(profile)
        backend = self._active_backend or self._backend_factory(profile)
        gate = self._connection_gate(profile, pairing)
        auth_status = self.auth_store.status()
        return ControlSnapshot(
            auth=auth_status,
            vpn=self.vpn_store.status(),
            pairing={
                "state": "empty",
            }
            if pairing is None
            else {
                "state": pairing.status,
                "profile_id": pairing.profile_id,
                "name": pairing.name,
                "role": pairing.role,
                "relay_mode": pairing.relay_mode,
                "backend_preference": pairing.backend_preference,
                "transport_preference": pairing.transport_preference,
                "provisioning_status": pairing.provisioning_status,
                "authorization_status": pairing.authorization_status,
                "relay_id": pairing.relay_id,
                "peer_name": pairing.peer_name or "",
                "peer_id": str(pairing.peer_id) if pairing.peer_id is not None else "",
                "device_id": pairing.device_id,
                "authorization_id": pairing.authorization_id,
                "credential_epoch": pairing.credential_epoch,
                "credential_expires_at": str(pairing.credential_expires_at) if pairing.credential_expires_at is not None else "",
                "credential_refresh_after": str(pairing.credential_refresh_after) if pairing.credential_refresh_after is not None else "",
                "server_error": pairing.server_error,
                "validation_error": pairing.validation_error,
                "revoked_at": str(pairing.revoked_at) if pairing.revoked_at is not None else "",
            },
            mesh=self.mesh_store.status(),
            backend=backend.status(),
            connection=gate,
        )

    def _pairing_or_raise(self, profile_id: str) -> PairingRecord:
        record = self.pairing_store.get(profile_id)
        if record is None:
            raise LookupError(f"no pairing profile {profile_id!r} found")
        return record
