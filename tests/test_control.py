from __future__ import annotations

import base64
import json
import time
from typing import Any

def _mk_jwt(**claims) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."

def test_auth_store_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import AuthStore

    store = AuthStore()
    record = store.save_jwt("jwt-token", user_id=42, phone="+989")
    loaded = store.load()

    assert loaded == record
    assert store.status()["state"] == "configured"
    assert "jwt-token" not in store.path.read_text(encoding="utf-8")

    store.clear()
    assert store.load() is None


def test_auth_store_tracks_token_expiry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import AuthStore

    jwt = _mk_jwt(exp=int(time.time()) + 3600, iat=int(time.time()) - 10)
    store = AuthStore()
    record = store.save_jwt(jwt, user_id=42, phone="+989")

    assert record.expires_at is not None
    assert record.issued_at is not None

    loaded = store.load()
    assert loaded is not None
    assert loaded.expires_at == record.expires_at
    assert store.status()["state"] == "configured"
    assert "expires_in" in store.status()


def test_observability_redacts_secrets() -> None:
    from baleobala.control.observability import redact_value

    payload = {
        "jwt": "secret-token",
        "Authorization": "Bearer abc",
        "nested": ["proxy_secret=demo", "cookie=session=1"],
    }
    redacted = redact_value(payload)
    assert redacted["jwt"] == "jwt=<redacted>"
    assert redacted["Authorization"] == "Authorization=<redacted>"
    assert redacted["nested"][0] == "proxy_secret=<redacted>"


def test_auth_store_treats_expired_token_as_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import AuthStore

    jwt = _mk_jwt(exp=1, iat=1)
    store = AuthStore()
    store.save_jwt(jwt, user_id=42, phone="+989")

    assert store.load() is None
    status = store.status()
    assert status["state"] == "expired"
    assert "expires_in" in status


def test_pairing_store_begin_accept(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import PairingStore

    store = PairingStore()
    pending = store.begin("relay-a", role="client", peer_id=7)
    assert pending.status == "pending"
    assert store.list()[0] == pending

    accepted = store.accept(pending.pair_code, name="relay-a-ready")
    assert accepted.status == "paired"
    assert accepted.name == "relay-a-ready"
    assert store.active() == accepted


def test_relay_directory_register_lookup_remove(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import RelayDirectory, RelayDirectoryEntry

    directory = RelayDirectory()
    entry = directory.register(
        RelayDirectoryEntry(
            relay_id="relay.home.1",
            name="home-vpn",
            peer_id=77,
            owner="operator",
            transport_preference="auto",
            backend_preference="proxy",
            endpoint_hint="10.0.0.1:1080",
        )
    )

    assert directory.lookup("home-vpn") == entry
    assert directory.get("relay.home.1") == entry
    assert directory.list()[0] == entry
    removed = directory.remove("home-vpn")
    assert removed == entry
    assert directory.lookup("home-vpn") is None


def test_pairing_store_invite_link_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    from baleobala.control import PairingStore

    store = PairingStore()
    pending = store.begin("relay-a", role="client", peer_id=7)
    link = store.export_invite_link(pending.profile_id)

    importer = PairingStore(path=tmp_path / "imported.json")
    record = importer.import_invite_link(link)

    assert record.profile_id == pending.profile_id
    assert record.status == "paired"
    assert record.peer_id == 7
    assert record.name == "relay-a"


def test_pairing_exchange_request_response_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    from baleobala.control import PairingStore

    initiator = PairingStore(path=tmp_path / "initiator.json")
    responder = PairingStore(path=tmp_path / "responder.json")

    pending = initiator.begin(
        "relay-a",
        role="client",
        peer_id=7,
        relay_mode="proxy",
        backend_preference="packet-tunnel",
    )
    request = initiator.export_request(pending.profile_id)
    assert request.exchange_type == "request"
    assert request.secret_value

    responder.add(pending)
    response = responder.accept_request(
        request,
        peer_name="relay-a-peer",
        backend_preference="packet-tunnel",
        transport_preference="auto",
    )
    assert response.exchange_type == "response"
    assert response.provisioning_status == "complete"

    accepted = initiator.apply_response(response)
    assert accepted.status == "paired"
    assert accepted.provisioning_status == "complete"
    assert accepted.peer_name == "relay-a-peer"
    assert initiator.connectable(accepted.profile_id) == accepted
    assert initiator.load_secret(accepted.profile_id) == response.secret_value


def test_pairing_store_prefers_most_recently_used_pairing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import PairingStore

    store = PairingStore()
    a = store.begin("relay-a", role="client", peer_id=7)
    b = store.begin("relay-b", role="client", peer_id=8)
    a = store.accept(a.pair_code)
    b = store.accept(b.pair_code)
    assert store.active() == b

    touched = store.touch(a.profile_id)
    assert touched is not None
    assert touched.profile_id == a.profile_id
    assert store.active() == touched


def test_vpn_store_default_and_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import VpnStore
    from baleobala.control.vpn import default_vpn_backend

    store = VpnStore()
    profile = store.ensure_default()
    assert profile.profile_id == "default"
    assert store.status()["backend"] == default_vpn_backend()
    assert store.status()["auto_start"] == "no"


def test_control_service_bootstraps_first_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")

    from baleobala.control import ControlService

    service = ControlService()
    snapshot = service.status()

    assert snapshot.auth["state"] == "empty"
    assert snapshot.vpn["state"] == "configured"
    assert snapshot.vpn["profile_id"] == "default"
    assert snapshot.pairing["state"] == "empty"
    assert snapshot.mesh["state"] == "empty"
    assert snapshot.backend["backend"] == "linux-tun"


def test_control_service_auth_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))

    from baleobala.control import ControlService

    service = ControlService()
    record = service.save_auth_jwt("jwt-token", phone="+989")

    assert record.phone == "+989"
    loaded = service.load_auth()
    assert loaded is not None
    assert loaded.jwt == "jwt-token"


def test_control_service_exports_and_applies_pairing_exchange(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService

    initiator = ControlService()
    pending = initiator.begin_pairing("relay-a", role="client", peer_id=9, relay_mode="proxy")
    request = initiator.export_pairing_request(pending.profile_id)

    responder = ControlService(pairing_store=type(initiator.pairing_store)(path=tmp_path / "remote-pairing.json"))
    responder.pairing_store.add(pending)
    response = responder.accept_pairing_request(request, peer_name="remote-relay")
    accepted = initiator.apply_pairing_response(response)

    assert accepted.profile_id == pending.profile_id
    assert accepted.status == "paired"
    assert accepted.peer_name == "remote-relay"


def test_control_service_enroll_request_approve_sync_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService

    service = ControlService()
    service.save_auth_jwt("jwt-token", user_id=77, phone="+989")

    enrolled = service.enroll_pairing("relay-a", role="client", relay_mode="proxy")
    assert enrolled.relay_id
    assert enrolled.authorization_status == "none"
    assert enrolled.provisioning_status == "enrolled"

    pending = service.request_pairing_access(enrolled.profile_id)
    assert pending.authorization_id
    assert pending.authorization_status == "pending"

    approved = service.approve_pairing_access(enrolled.profile_id)
    assert approved.authorization_status == "approved"
    assert approved.provisioning_status == "complete"
    assert approved.credential_epoch
    assert approved.credential_expires_at is not None
    assert service.pairing_store.connectable(approved.profile_id) is not None


def test_control_service_revoked_pairing_is_not_connectable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService

    service = ControlService()
    service.save_auth_jwt("jwt-token", user_id=77, phone="+989")

    record = service.enroll_pairing("relay-a", role="client", relay_mode="proxy")
    record = service.request_pairing_access(record.profile_id)
    record = service.approve_pairing_access(record.profile_id)
    assert service.pairing_store.connectable(record.profile_id) is not None

    revoked = service.revoke_pairing_device(record.profile_id)
    assert revoked.authorization_status == "revoked"
    assert revoked.provisioning_status == "revoked"
    assert service.pairing_store.connectable(record.profile_id) is None


def test_control_service_status_keeps_file_based_pairings_ready(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService, PairingRecord, PairingStore, VpnProfile, VpnStore

    class FakeBackend:
        def status(self):
            return {
                "backend": "proxy",
                "state": "running",
                "route_ready": "yes",
                "dns_ready": "yes",
                "carrier_bypass_ready": "yes",
                "egress_ready": "yes",
            }

    pairing_store = PairingStore()
    pairing = pairing_store.add(
        PairingRecord(
            profile_id="pair-compat",
            name="relay",
            role="client",
            pair_code="pair-code",
            relay_mode="proxy",
            peer_id=7,
            peer_name="relay",
            backend_preference="proxy",
            transport_preference="auto",
            provisioning_status="complete",
            authorization_status="none",
            status="paired",
        )
    )
    vpn_store = VpnStore()
    vpn_store.save(
        VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend="proxy",
            role="client",
            pairing_id=pairing.profile_id,
            peer_id=pairing.peer_id,
            peer_name=pairing.peer_name,
        )
    )

    service = ControlService(
        pairing_store=pairing_store,
        vpn_store=vpn_store,
        backend_factory=lambda profile: FakeBackend(),
    )

    snapshot = service.status()

    assert snapshot.pairing["authorization_status"] == "none"
    assert snapshot.connection["state"] == "ready"


def test_control_service_status_surfaces_expired_auth_and_revoked_pairing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService, VpnProfile, VpnStore

    class FakeBackend:
        def status(self):
            return {
                "backend": "proxy",
                "state": "running",
                "route_ready": "yes",
                "dns_ready": "yes",
                "carrier_bypass_ready": "yes",
                "egress_ready": "yes",
            }

    now = int(time.time())
    service = ControlService(backend_factory=lambda profile: FakeBackend())
    service.save_auth_jwt(_mk_jwt(exp=now + 3600, iat=now - 10), user_id=77, phone="+989")

    enrolled = service.enroll_pairing("relay-a", role="client", relay_mode="proxy")
    service.request_pairing_access(enrolled.profile_id)
    approved = service.approve_pairing_access(enrolled.profile_id)

    vpn_store = VpnStore()
    vpn_store.save(
        VpnProfile(
            profile_id=approved.profile_id,
            name=approved.name,
            backend="proxy",
            role="client",
            pairing_id=approved.profile_id,
            peer_id=approved.peer_id,
            peer_name=approved.peer_name,
        )
    )

    service.save_auth_jwt(_mk_jwt(exp=now - 1, iat=now - 3600), user_id=77, phone="+989")
    expired = service.status()

    assert expired.connection["state"] == "blocked"
    assert expired.connection["code"] == "auth_expired"

    service.save_auth_jwt(_mk_jwt(exp=now + 3600, iat=now - 10), user_id=77, phone="+989")
    revoked = service.revoke_pairing_device(approved.profile_id)
    assert revoked.authorization_status == "revoked"

    snapshot = service.status()
    assert snapshot.connection["state"] == "blocked"
    assert snapshot.connection["code"] == "access_revoked"


def test_control_service_stop_uses_active_backend_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService, PairingRecord, PairingStore, VpnProfile, VpnStore

    class FakeProbe:
        def __init__(self, ok: str = "yes") -> None:
            self.ok = ok

        def to_dict(self):
            return {
                "ok": self.ok,
                "detail": "probe ok" if self.ok == "yes" else "probe failed",
            }

    class FakeBackend:
        def __init__(self) -> None:
            self.state = "stopped"
            self.down_calls = 0
            self.probe_ok = "yes"
            self.profile_id = ""
            self.pairing_id = ""

        def up(self, profile, auth_record=None, pairing=None):  # noqa: ANN001
            self.state = "running"
            self.profile_id = profile.profile_id
            self.pairing_id = pairing.profile_id if pairing is not None else ""
            return {
                "backend": "proxy",
                "state": "running",
                "profile_id": self.profile_id,
                "pairing_id": self.pairing_id,
            }

        def down(self):
            self.down_calls += 1
            self.state = "stopped"

        def status(self):
            return {
                "backend": "proxy",
                "state": self.state,
                "profile_id": self.profile_id,
                "pairing_id": self.pairing_id,
            }

        def probe(self):
            return FakeProbe(self.probe_ok)

    pairing_store = PairingStore()
    pairing = pairing_store.add(
        PairingRecord(
            profile_id="pair-stop",
            name="relay",
            role="client",
            pair_code="pair-code",
            relay_mode="proxy",
            peer_id=7,
            peer_name="relay",
            backend_preference="proxy",
            transport_preference="auto",
            provisioning_status="complete",
            authorization_status="none",
            status="paired",
        )
    )
    vpn_store = VpnStore()
    vpn_store.save(
        VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend="proxy",
            role="client",
            pairing_id=pairing.profile_id,
            peer_id=pairing.peer_id,
            peer_name=pairing.peer_name,
        )
    )

    backends: list[FakeBackend] = []

    def factory(profile):  # noqa: ANN001
        backend = FakeBackend()
        backends.append(backend)
        return backend

    service = ControlService(pairing_store=pairing_store, vpn_store=vpn_store, backend_factory=factory)

    snapshot = service.start_connection()
    assert snapshot.backend["state"] == "running"
    assert len(backends) == 1

    stopped = service.stop_connection()
    assert stopped.backend["state"] == "stopped"
    assert len(backends) == 1
    assert backends[0].down_calls == 1


def test_control_service_reconcile_uses_active_backend_for_probe_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")

    from baleobala.control import ControlService, PairingRecord, PairingStore, VpnProfile, VpnStore

    class FakeProbe:
        def __init__(self, ok: str = "yes") -> None:
            self.ok = ok

        def to_dict(self):
            return {
                "ok": self.ok,
                "detail": "probe ok" if self.ok == "yes" else "probe failed",
            }

    class FakeBackend:
        def __init__(self) -> None:
            self.state = "stopped"
            self.down_calls = 0
            self.probe_ok = "yes"
            self.profile_id = ""
            self.pairing_id = ""

        def up(self, profile, auth_record=None, pairing=None):  # noqa: ANN001
            self.state = "running"
            self.profile_id = profile.profile_id
            self.pairing_id = pairing.profile_id if pairing is not None else ""
            return {
                "backend": "proxy",
                "state": "running",
                "profile_id": self.profile_id,
                "pairing_id": self.pairing_id,
            }

        def down(self):
            self.down_calls += 1
            self.state = "stopped"

        def status(self):
            return {
                "backend": "proxy",
                "state": self.state,
                "profile_id": self.profile_id,
                "pairing_id": self.pairing_id,
            }

        def probe(self):
            return FakeProbe(self.probe_ok)

    pairing_store = PairingStore()
    pairing = pairing_store.add(
        PairingRecord(
            profile_id="pair-reconcile",
            name="relay",
            role="client",
            pair_code="pair-code",
            relay_mode="proxy",
            peer_id=7,
            peer_name="relay",
            backend_preference="proxy",
            transport_preference="auto",
            provisioning_status="complete",
            authorization_status="none",
            status="paired",
        )
    )
    vpn_store = VpnStore()
    vpn_store.save(
        VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend="proxy",
            role="client",
            pairing_id=pairing.profile_id,
            peer_id=pairing.peer_id,
            peer_name=pairing.peer_name,
        )
    )

    backends: list[FakeBackend] = []

    def factory(profile):  # noqa: ANN001
        backend = FakeBackend()
        backends.append(backend)
        return backend

    service = ControlService(pairing_store=pairing_store, vpn_store=vpn_store, backend_factory=factory)

    snapshot = service.start_connection()
    assert snapshot.backend["state"] == "running"
    assert len(backends) == 1

    backends[0].probe_ok = "no"
    reconciled = service.reconcile_runtime()

    assert len(backends) == 1
    assert backends[0].down_calls == 1
    assert reconciled.backend["state"] == "degraded"
    assert reconciled.backend["last_error"] == "probe failed"


def test_packet_tunnel_backend_tracks_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import TunnelServiceState, VpnProfile
    from baleobala.control.backend import MacOSPacketTunnelBackend

    class FakeService:
        def __init__(self) -> None:
            self.started = False
            self._state_store = type("Store", (), {"save": lambda self, payload: None})()

        def start(self, *, profile_id: str, backend: str, pairing_id: str | None = None):  # noqa: ANN001
            self.started = True
            return TunnelServiceState(
                state="running",
                endpoint="unix:///tmp/packet-tunnel.sock",
                profile_id=profile_id,
                backend=backend,
                pairing_id=pairing_id,
                transport_selected="packet-tunnel",
                call_established="yes",
                data_flow_ok="yes",
                route_ready="yes",
                dns_ready="yes",
            )

        def status(self):
            return TunnelServiceState(
                state="running" if self.started else "stopped",
                endpoint="unix:///tmp/packet-tunnel.sock" if self.started else None,
                transport_selected="packet-tunnel" if self.started else "",
                call_established="yes" if self.started else "no",
                data_flow_ok="yes" if self.started else "no",
                route_ready="yes" if self.started else "no",
                dns_ready="yes" if self.started else "no",
            )

        def stop(self):
            self.started = False

    backend = MacOSPacketTunnelBackend(service=FakeService())
    profile = VpnProfile(profile_id="p1", name="client", backend="packet-tunnel")

    status = backend.up(profile)
    assert status["backend"] == "packet-tunnel"
    assert status["state"] == "running"
    assert status["transport_selected"] == "packet-tunnel"
    assert status["call_established"] == "yes"
    assert status["data_flow_ok"] == "yes"
    assert status["route_ready"] == "yes"
    assert status["dns_ready"] == "yes"
    assert status["profile_id"] == "p1"
    backend.down()
    assert backend.status()["state"] == "stopped"


def test_macos_install_tunnel_profile_writes_shared_container(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.macos import (
        install_tunnel_profile,
        load_tunnel_profile,
        packet_tunnel_configuration,
        tunnel_profile_installed,
        tunnel_profile_path,
    )
    from baleobala.control.vpn import VpnProfile

    payload = packet_tunnel_configuration(VpnProfile(profile_id="p1", name="baleobala"))
    assert payload["tunnelIPv4Address"] == "10.77.0.2"
    assert payload["includedIPv4Routes"] == ["0.0.0.0/0"]
    assert payload["includedIPv6Routes"] == []
    assert payload["excludedRoutes"] == ["127.0.0.0/8"]
    result = install_tunnel_profile(payload)
    assert result["state"] == "installed"
    assert tunnel_profile_path().exists()
    assert load_tunnel_profile() == payload
    assert tunnel_profile_installed(payload) is True


def test_pairing_store_tracks_managed_provisioning_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    from baleobala.control import PairingStore

    store = PairingStore()
    pending = store.begin(
        "relay-a",
        role="client",
        peer_id=7,
        relay_mode="proxy",
        backend_preference="packet-tunnel",
        transport_preference="auto",
        secret_name="pair.secret",
    )
    accepted = store.accept(pending.pair_code, peer_name="relay-a", validate=True)

    assert accepted.provisioning_status == "complete"
    assert accepted.backend_preference == "packet-tunnel"
    assert accepted.secret_name == "pair.secret"
    assert store.connectable(accepted.profile_id) == accepted
    assert store.load_secret(accepted.profile_id) is not None


def test_control_service_tracks_durable_mesh_assignments(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import ControlService

    service = ControlService()
    assigned = service.issue_mesh_assignment(77, pool_cidr="10.77.0.0/24", transport="dc", session_id=0x115e)
    assert assigned.client_ip == "10.77.0.2"
    assert assigned.provisioning_status == "assigned"

    looked_up = service.lookup_mesh_assignment(77)
    assert looked_up is not None
    assert looked_up.slot == assigned.slot

    active = service.activate_mesh_assignment(77, transport="dc", session_id=0x115e)
    assert active.provisioning_status == "active"

    service.release_mesh_assignment(77)
    released = service.lookup_mesh_assignment(77)
    assert released is not None
    assert released.provisioning_status == "released"


def test_mesh_assignments_are_sticky_across_service_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import ControlService

    first = ControlService()
    issued = first.issue_mesh_assignment(99, pool_cidr="10.77.0.0/24")

    second = ControlService()
    reissued = second.issue_mesh_assignment(99, pool_cidr="10.77.0.0/24")
    assert reissued.slot == issued.slot
    assert reissued.client_ip == issued.client_ip


def test_control_service_reconcile_clears_stale_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, ControlService, PairingStore, VpnStore
    from baleobala.control.vpn import VpnProfile

    class Bridge:
        def start(self):
            pass

        def send(self, data):  # noqa: ANN001
            return len(data)

        def recv(self, timeout=None):  # noqa: ANN001
            return None

        def close(self):
            pass

        @property
        def closed(self):
            return False

    monkeypatch.setattr("baleobala.control.macos.tunnel_profile_installed", lambda config: True)
    monkeypatch.setattr(
        "baleobala.control.backend._build_packet_tunnel_service",
        lambda profile, auth_record, pairing: __import__(
            "baleobala.control.tunnel_service", fromlist=["CarrierTunnelService"]
        ).CarrierTunnelService(Bridge(), socket_path=tmp_path / "carrier.sock", manage_bridge=False),
    )

    AuthStore().save_jwt("jwt-token", user_id=42, phone="+989")
    pairing = PairingStore().begin("relay-a", role="client", peer_id=7, relay_mode="packet-tunnel")
    pairing = PairingStore().accept(pairing.pair_code, validate=True)
    VpnStore().save(
        VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend="packet-tunnel",
            pairing_id=pairing.profile_id,
            peer_id=pairing.peer_id,
            peer_name=pairing.peer_name,
        )
    )

    service = ControlService()
    snapshot = service.start_connection()
    assert snapshot.backend["state"] == "running"

    runtime_file = tmp_path / "carrier_tunnel_service.json"
    assert runtime_file.exists()
    payload = json.loads(runtime_file.read_text(encoding="utf-8"))
    payload["endpoint"] = "unix:///tmp/does-not-exist.sock"
    runtime_file.write_text(json.dumps(payload), encoding="utf-8")

    reconciled = service.reconcile_runtime()
    assert reconciled.backend["state"] == "degraded"
    assert "No such file" in reconciled.backend["last_error"] or "does-not-exist" in reconciled.backend["last_error"]


def test_bundle_analyzer_classifies_missing_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    analysis = analyze_bundle(tmp_path / "missing")
    assert analysis.classification == "infra_flake"
    assert analysis.ok == "no"


def _write_bundle_fixture(bundle, payload: dict[str, object]) -> None:
    bundle.mkdir()
    for name, body in {
        "summary.json": {"ok": payload.get("smoke", {}).get("ok", "no"), "smoke_kind": payload.get("scenario", {}).get("smoke_kind", "")},
        "scenario.json": payload.get("scenario", {}),
        "environment.json": {"platform": "linux"},
        "process_status.json": {"started": payload.get("started", []), "stopped": payload.get("stopped", [])},
        "markers.json": {"markers": payload.get("markers", [])},
        "smoke.json": payload.get("smoke", {}),
        "teardown.json": payload.get("teardown", {}),
        "route_snapshot.json": payload.get("route_snapshot", {}),
        "dns_snapshot.json": payload.get("dns_snapshot", {}),
        "command_transcript.json": {"items": []},
    }.items():
        (bundle / name).write_text(json.dumps(body), encoding="utf-8")
    (bundle / "events.jsonl").write_text("", encoding="utf-8")
    (bundle / "server.log").write_text("call_established\n", encoding="utf-8")
    (bundle / "client.log").write_text("transport_selected=dc\n", encoding="utf-8")
    (bundle / "verdict.json").write_text(json.dumps(payload), encoding="utf-8")


def test_bundle_analyzer_accepts_complete_success_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle"
    _write_bundle_fixture(
        bundle,
        {
            "scenario": {"kind": "proxy-pair"},
            "setup": {"ok": "yes"},
            "smoke": {"ok": "yes", "last_error": ""},
            "teardown": {"ok": "yes"},
            "started": [{"name": "server"}],
            "stopped": [{"name": "server"}],
            "markers": ["call_established", "transport_selected=dc"],
            "failure_class": "",
            "failure_code": "",
            "last_success_stage": "teardown",
            "failed_stage": "",
            "log_tails": {"server": "call_established\ntransport_selected=dc\n"},
        },
    )

    analysis = analyze_bundle(bundle)
    assert analysis.classification == "accepted_flow"
    assert analysis.ok == "yes"


def test_bundle_analyzer_uses_failure_class_for_carrier_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle-carrier"
    _write_bundle_fixture(
        bundle,
        {
            "scenario": {"kind": "proxy-pair"},
            "setup": {"ok": "yes"},
            "smoke": {"ok": "no", "last_error": "process readiness timeout"},
            "teardown": {"ok": "yes"},
            "started": [{"name": "server"}],
            "stopped": [{"name": "server"}],
            "markers": ["call_established"],
            "failure_class": "transport_runtime",
            "failure_code": "transport_closed_early",
            "log_tails": {"server": "call_established\n"},
        },
    )

    analysis = analyze_bundle(bundle)
    assert analysis.classification == "carrier_instability"
    assert analysis.reason == "transport_closed_early"
    assert analysis.failure_code == "transport_closed_early"


def test_bundle_analyzer_reports_missing_bundle_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle-missing-artifacts"
    bundle.mkdir()
    (bundle / "verdict.json").write_text(json.dumps({"markers": []}), encoding="utf-8")
    analysis = analyze_bundle(bundle)
    assert analysis.classification == "infra_flake"
    assert analysis.reason == "missing artifacts"


def test_bundle_analyzer_reports_invalid_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle-invalid-json"
    bundle.mkdir()
    (bundle / "verdict.json").write_text("{", encoding="utf-8")
    analysis = analyze_bundle(bundle)
    assert analysis.reason == "invalid JSON"


def test_merge_status_with_bundle_prefers_session_artifact_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import merge_status_with_bundle

    bundle = tmp_path / "bundle-merge"
    _write_bundle_fixture(
        bundle,
        {
            "scenario": {"kind": "proxy-pair"},
            "setup": {"ok": "yes"},
            "smoke": {"ok": "yes", "last_error": ""},
            "teardown": {"ok": "yes"},
            "started": [{"name": "server"}],
            "stopped": [{"name": "server"}],
            "markers": ["call_established", "transport_selected=dc"],
            "failure_class": "",
            "log_tails": {"server": "call_established\ntransport_selected=dc\n"},
        },
    )

    merged = merge_status_with_bundle(
        {
            "backend": "linux-tun",
            "state": "running",
            "call_established": "no",
            "transport_selected": "",
            "data_flow_ok": "no",
            "recovery_state": "recovering",
            "transport_previous": "audio",
            "failover_count": "2",
            "teardown_clean": "no",
            "failure_class": "",
        },
        bundle,
    )
    assert merged["backend"] == "proxy-pair"
    assert merged["call_established"] == "yes"
    assert merged["transport_selected"] == "dc"
    assert merged["data_flow_ok"] == "yes"
    assert merged["recovery_state"] == "recovering"
    assert merged["transport_previous"] == "audio"
    assert merged["failover_count"] == "2"
    assert merged["teardown_clean"] == "yes"
    assert merged["artifact_bundle"] == str(bundle)
    assert merged["route_ready"] == "yes"
    assert merged["dns_ready"] == "yes"
    assert merged["carrier_bypass_ready"] == "no"
    assert merged["egress_ready"] == "no"


def test_merge_status_with_bundle_reports_full_device_bypass_and_egress(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import merge_status_with_bundle

    bundle = tmp_path / "bundle-full-device"
    _write_bundle_fixture(
        bundle,
        {
            "scenario": {
                "kind": "tunnel-pair",
                "smoke_kind": "tunnel-payload",
                "full_device_checks": ["route", "dns", "tcp-egress"],
                "network_plan": {
                    "dns_servers": ["1.1.1.1"],
                    "carrier_hosts": ["next-ws.bale.ai"],
                    "setup_host_routes": True,
                    "client": {"tun": "vpn0", "routes": ["0.0.0.0/1", "128.0.0.0/1"]},
                },
            },
            "setup": {"ok": "yes"},
            "smoke": {"ok": "yes", "last_error": ""},
            "teardown": {"ok": "yes"},
            "started": [{"name": "server"}],
            "stopped": [{"name": "server"}],
            "markers": ["call_established", "transport_selected=dc"],
            "failure_class": "",
            "failure_code": "",
            "route_snapshot": {"client": {"stdout": "0.0.0.0/1 dev vpn0\n128.0.0.0/1 dev vpn0\nnext-ws.bale.ai via 172.29.0.2 dev bb-vc\n"}},
            "dns_snapshot": {"client": {"stdout": "nameserver 1.1.1.1\n"}},
        },
    )

    merged = merge_status_with_bundle({"backend": "linux-tun", "state": "running"}, bundle)
    assert merged["route_ready"] == "yes"
    assert merged["dns_ready"] == "yes"
    assert merged["carrier_bypass_ready"] == "yes"
    assert merged["egress_ready"] == "yes"


def test_bundle_analyzer_classifies_dns_snapshot_regression(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle-dns"
    _write_bundle_fixture(
        bundle,
        {
            "scenario": {
                "kind": "tunnel-pair",
                "smoke_kind": "tunnel-payload",
                "full_device_checks": ["route", "dns", "tcp-egress"],
                "network_plan": {
                    "dns_servers": ["1.1.1.1", "9.9.9.9"],
                    "client": {"tun": "vpn0", "routes": ["0.0.0.0/1", "128.0.0.0/1"]},
                },
            },
            "setup": {"ok": "yes"},
            "smoke": {"ok": "yes", "last_error": ""},
            "teardown": {"ok": "yes"},
            "started": [{"name": "server"}],
            "stopped": [{"name": "server"}],
            "markers": ["call_established", "transport_selected=dc"],
            "failure_class": "",
            "failure_code": "",
            "route_snapshot": {"client": {"stdout": "0.0.0.0/1 dev vpn0\n128.0.0.0/1 dev vpn0\n"}},
            "dns_snapshot": {"client": {"stdout": "nameserver 1.1.1.1\n"}},
        },
    )

    analysis = analyze_bundle(bundle)
    assert analysis.classification == "infra_flake"
    assert analysis.reason == "dns_config_failed"
    assert analysis.failure_code == "dns_config_failed"

    from baleobala.control.analyzer import merge_status_with_bundle

    merged = merge_status_with_bundle({"backend": "linux-tun", "state": "running"}, bundle)
    assert merged["route_ready"] == "yes"
    assert merged["dns_ready"] == "no"
    assert merged["carrier_bypass_ready"] == "no"
    assert merged["egress_ready"] == "yes"


def test_bundle_analyzer_classifies_route_snapshot_regression(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle-route"
    _write_bundle_fixture(
        bundle,
        {
            "scenario": {
                "kind": "tunnel-pair",
                "smoke_kind": "tunnel-payload",
                "full_device_checks": ["route", "dns", "tcp-egress"],
                "network_plan": {
                    "dns_servers": ["1.1.1.1"],
                    "client": {"tun": "vpn0", "routes": ["0.0.0.0/1", "128.0.0.0/1"]},
                },
            },
            "setup": {"ok": "yes"},
            "smoke": {"ok": "yes", "last_error": ""},
            "teardown": {"ok": "yes"},
            "started": [{"name": "server"}],
            "stopped": [{"name": "server"}],
            "markers": ["call_established", "transport_selected=dc"],
            "failure_class": "",
            "failure_code": "",
            "route_snapshot": {"client": {"stdout": ""}},
            "dns_snapshot": {"client": {"stdout": "nameserver 1.1.1.1\n"}},
        },
    )

    analysis = analyze_bundle(bundle)
    assert analysis.classification == "infra_flake"
    assert analysis.reason == "route_program_failed"
    assert analysis.failure_code == "route_program_failed"

    from baleobala.control.analyzer import merge_status_with_bundle

    merged = merge_status_with_bundle({"backend": "linux-tun", "state": "running"}, bundle)
    assert merged["route_ready"] == "no"
    assert merged["dns_ready"] == "yes"
    assert merged["carrier_bypass_ready"] == "no"
    assert merged["egress_ready"] == "yes"


def test_product_verdict_prefers_bundle_analysis(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import BundleAnalysis, build_product_verdict

    verdict = build_product_verdict(
        {
            "backend": "linux-tun",
            "state": "running",
            "call_established": "yes",
            "transport_selected": "dc",
            "data_flow_ok": "yes",
            "teardown_clean": "yes",
            "route_ready": "yes",
            "dns_ready": "yes",
            "carrier_bypass_ready": "yes",
            "egress_ready": "yes",
            "failure_class": "",
            "artifact_bundle": "/tmp/bundle",
        },
        BundleAnalysis("product_bug", "payload flow failed", "/tmp/bundle", "no"),
    )
    assert verdict.ok == "no"
    assert verdict.analysis_classification == "product_bug"


def test_product_verdict_leaves_failure_class_empty_for_accepted_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import BundleAnalysis, build_product_verdict

    verdict = build_product_verdict(
        {
            "backend": "proxy-pair",
            "state": "running",
            "call_established": "yes",
            "transport_selected": "dc",
            "data_flow_ok": "yes",
            "teardown_clean": "yes",
            "route_ready": "yes",
            "dns_ready": "yes",
            "carrier_bypass_ready": "yes",
            "egress_ready": "yes",
            "failure_class": "",
            "artifact_bundle": "/tmp/bundle",
        },
        BundleAnalysis("accepted_flow", "bundle indicates accepted flow", "/tmp/bundle", "yes"),
    )
    assert verdict.ok == "yes"
    assert verdict.failure_class == ""
    assert verdict.route_ready == "yes"
    assert verdict.dns_ready == "yes"
    assert verdict.carrier_bypass_ready == "yes"
    assert verdict.egress_ready == "yes"
    assert verdict.analysis_classification == "accepted_flow"


def test_local_tunnel_service_ipc_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.tunnel_service import LocalTunnelService
    import socket

    service = LocalTunnelService()
    state = service.start(profile_id="p1", backend="packet-tunnel", pairing_id="pair-1")
    assert state.state == "running"
    assert service.endpoint is not None

    assert service.endpoint is not None
    socket_path = service.endpoint.removeprefix("unix://")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall(b"ping")
        assert client.recv(16) == b"pong\n"

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall(b"status")
        assert b'"state": "running"' in client.recv(4096)

    service.stop()
    assert service.status().state == "stopped"


def test_local_tunnel_service_clears_stale_socket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.tunnel_service import LocalTunnelService

    socket_path = tmp_path / "stale.sock"
    socket_path.write_text("stale", encoding="utf-8")

    service = LocalTunnelService(socket_path=socket_path)
    state = service.start(profile_id="p1", backend="packet-tunnel")
    assert state.state == "running"
    assert state.endpoint is not None
    assert state.endpoint.startswith("unix://")
    service.stop()


def test_tunnel_services_default_to_shared_container_socket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SHARED_CONTAINER", "/tmp/baleobala-shared")
    from baleobala.control.tunnel_service import CarrierTunnelService, LocalTunnelService

    class Bridge:
        def start(self):
            pass

        def send(self, data):  # noqa: ANN001
            return len(data)

        def recv(self, timeout=None):  # noqa: ANN001
            return None

        def close(self):
            pass

        @property
        def closed(self):
            return False

    local = LocalTunnelService()
    carrier = CarrierTunnelService(Bridge())
    local_state = local.start(profile_id="p1", backend="packet-tunnel")
    carrier_state = carrier.start(profile_id="p1", backend="packet-tunnel")
    assert "/tmp/baleobala-shared" in str(local_state.endpoint or "")
    assert "/tmp/baleobala-shared" in str(carrier_state.endpoint or "")
    local.stop()
    carrier.stop()


def test_carrier_tunnel_service_bridges_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    import socket
    from baleobala.control.tunnel_service import CarrierTunnelService
    from baleobala.runtime import MemoryByteChannel, NullSecurityProvider, TunnelRole, TunnelSession

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT, security=NullSecurityProvider(session_id="left"))
    right = TunnelSession(right_ch, role=TunnelRole.SERVER, security=NullSecurityProvider(session_id="right"))

    class SessionBridge:
        def __init__(self, session):  # noqa: ANN001
            self._session = session

        def start(self):
            self._session.open()

        def send(self, data):  # noqa: ANN001
            return self._session.send(data)

        def recv(self, timeout=None):  # noqa: ANN001
            return self._session.recv(timeout=timeout)

        def close(self):
            self._session.close()

        @property
        def closed(self):
            return self._session.closed

    runtime = CarrierTunnelService(SessionBridge(left), socket_path=tmp_path / "carrier.sock", manage_bridge=True)
    runtime.start(profile_id="p1", backend="packet-tunnel", pairing_id="pair-1")
    right.open()

    endpoint = runtime.status().endpoint
    assert endpoint is not None
    socket_path = endpoint.removeprefix("unix://")

    def recv_exact(sock, size):  # noqa: ANN001
        buf = bytearray()
        while len(buf) < size:
            chunk = sock.recv(size - len(buf))
            if not chunk:
                raise EOFError
            buf.extend(chunk)
        return bytes(buf)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall((5).to_bytes(4, "big") + b"hello")
        assert right.recv(timeout=1.0) == b"hello"
        right.send(b"world")
        assert recv_exact(client, 4) == (5).to_bytes(4, "big")
        assert recv_exact(client, 5) == b"world"

    runtime.stop()


def test_carrier_tunnel_service_tracks_control_status_frames(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    import json
    import socket

    from baleobala.control.probe import probe_endpoint
    from baleobala.control.tunnel_service import CarrierTunnelService

    class Bridge:
        def start(self):
            pass

        def send(self, data):  # noqa: ANN001
            return len(data)

        def recv(self, timeout=None):  # noqa: ANN001
            return None

        def close(self):
            pass

        @property
        def closed(self):
            return False

    runtime = CarrierTunnelService(Bridge(), socket_path=tmp_path / "carrier.sock")
    state = runtime.start(profile_id="p1", backend="packet-tunnel", pairing_id="pair-1")
    endpoint = state.endpoint
    assert endpoint is not None
    socket_path = endpoint.removeprefix("unix://")

    message = json.dumps(
        {
            "type": "status",
            "version": "1",
            "state": "running",
            "transport_selected": "packet-tunnel",
            "call_established": "yes",
            "data_flow_ok": "yes",
            "recovery_state": "healthy",
            "transport_previous": "audio",
            "failover_count": "3",
            "recovering_since": "123.4",
            "carrier_session_id": "0xabc",
            "peer_coordination": "active",
            "route_ready": "yes",
            "dns_ready": "yes",
            "last_error": "",
        }
    ).encode("utf-8")

    def recv_exact(sock, size):  # noqa: ANN001
        buf = bytearray()
        while len(buf) < size:
            chunk = sock.recv(size - len(buf))
            if not chunk:
                raise EOFError
            buf.extend(chunk)
        return bytes(buf)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall(len(message).to_bytes(4, "big") + message)
        header = recv_exact(client, 4)
        reply = json.loads(recv_exact(client, int.from_bytes(header, "big")).decode("utf-8"))

    assert reply["transport_selected"] == "packet-tunnel"
    assert reply["recovery_state"] == "healthy"
    assert reply["transport_previous"] == "audio"
    assert reply["failover_count"] == "3"
    assert reply["peer_coordination"] == "active"
    assert runtime.status().call_established == "yes"
    assert runtime.status().route_ready == "yes"
    assert runtime.status().peer_coordination == "active"

    probe = probe_endpoint(endpoint)
    assert probe.ok is True
    assert probe.payload is not None
    assert probe.payload["transport_selected"] == "packet-tunnel"
    assert probe.payload["call_established"] == "yes"
    assert probe.payload["recovery_state"] == "healthy"

    runtime.stop()


def test_build_parser_exposes_control_plane_commands() -> None:
    from baleobala.cli import build_parser

    def _subparser_choices(parser):
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                return choices
        return {}

    def _arg_choices(parser, dest):
        for action in parser._actions:
            if getattr(action, "dest", None) == dest:
                return action.choices
        raise AssertionError(f"missing argument {dest!r}")

    parser = build_parser()
    subcommands = _subparser_choices(parser)

    for cmd in ("auth", "pair", "relay", "vpn", "tunnel"):
        assert cmd in subcommands
    vpn_parser = subcommands["vpn"]
    vpn_subcommands = _subparser_choices(vpn_parser)
    assert "agent" in vpn_subcommands
    assert "probe" in vpn_subcommands
    assert "smoke" in vpn_subcommands
    assert "live-smoke" in vpn_subcommands
    assert "netns-plan" in vpn_subcommands
    assert "netns-run" in vpn_subcommands
    assert "netns-process-plan" in vpn_subcommands
    assert "netns-process-run" in vpn_subcommands
    assert "netns-session" in vpn_subcommands
    assert "netns-scenario" in vpn_subcommands

    vpn_up_parser = vpn_subcommands["up"]
    assert "linux-tun" in _arg_choices(vpn_up_parser, "backend")
    live_smoke_parser = vpn_subcommands["live-smoke"]
    live_smoke_opts = {action.dest for action in live_smoke_parser._actions}
    assert "ws_ca_file" in live_smoke_opts
    assert "ws_ca_path" in live_smoke_opts
    assert "ws_ssl_no_verify" in live_smoke_opts
    netns_session_parser = vpn_subcommands["netns-session"]
    netns_session_opts = {action.dest for action in netns_session_parser._actions}
    assert "full_device" in netns_session_opts
    assert "dns_server" in netns_session_opts
    assert "carrier_host" in netns_session_opts
    assert "skip_nat_setup" in netns_session_opts
    assert "skip_host_route_setup" in netns_session_opts

    relay_parser = subcommands["relay"]
    relay_subcommands = _subparser_choices(relay_parser)
    relay_enable_parser = relay_subcommands["enable"]
    assert "linux-tun" in _arg_choices(relay_enable_parser, "backend")
    assert "publish" in relay_subcommands
    assert "list" in relay_subcommands
    assert "remove" in relay_subcommands

    pair_parser = subcommands["pair"]
    pair_subcommands = _subparser_choices(pair_parser)
    assert "invite" in pair_subcommands
    assert "join" in pair_subcommands
    assert "relay" in {action.dest for action in pair_subcommands["start"]._actions}
    assert "relay" in {action.dest for action in pair_subcommands["enroll"]._actions}
    assert "relay" in {action.dest for action in vpn_up_parser._actions}

    tunnel_parser = subcommands["tunnel"]
    tunnel_subcommands = _subparser_choices(tunnel_parser)
    for cmd in ("up", "exit-node", "exit-node-mesh", "loopback"):
        assert cmd in tunnel_subcommands


def test_default_backends_choose_linux_tun_on_linux(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    from baleobala.control.backend import default_backend_name
    from baleobala.control.vpn import default_vpn_backend

    assert default_backend_name() == "linux-tun"
    assert default_vpn_backend() == "linux-tun"


def test_vpn_profile_roundtrip_includes_peer_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import VpnProfile, VpnStore

    profile = VpnProfile(
        profile_id="p1",
        name="relay",
        backend="proxy",
        role="relay",
        pairing_id="pair-1",
        peer_id=123,
        peer_name="home-relay",
        answer=True,
    )
    store = VpnStore()
    store.save(profile)
    loaded = store.load()

    assert loaded == profile
    assert store.status()["answer"] == "yes"


def test_macos_launch_agent_install_writes_plist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import VpnProfile, VpnStore
    from baleobala.control.macos_launchd import MacOSLaunchAgentManager

    VpnStore().save(
        VpnProfile(
            profile_id="relay-1",
            name="relay",
            backend="proxy",
            role="client",
            pairing_id="relay-1",
            peer_id=99,
            answer=False,
        )
    )

    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(tuple(cmd))
        return __import__("subprocess").CompletedProcess(cmd, 0, stdout="", stderr="")

    plist_path = tmp_path / "LaunchAgents" / "com.baleobala.vpn.plist"
    manager = MacOSLaunchAgentManager(plist_path=plist_path, runner=fake_run)
    installed = manager.install()

    assert installed == plist_path
    assert plist_path.exists()
    payload = plist_path.read_bytes()
    assert b"baleobala.cli" in payload
    assert ("launchctl", "bootstrap", f"gui/{__import__('os').getuid()}", str(plist_path)) in calls


def test_macos_launch_agent_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.macos_launchd import MacOSLaunchAgentManager

    manager = MacOSLaunchAgentManager(plist_path=tmp_path / "LaunchAgents" / "com.baleobala.vpn.plist")
    status = manager.status()
    assert status["installed"] == "no"


def test_macos_system_proxy_session_roundtrip(monkeypatch) -> None:
    from baleobala.control.macos import MacOSSystemProxySession

    outputs = {
        ("networksetup", "-getwebproxy", "Wi-Fi"): "Enabled: No\nServer: \nPort: 0\n",
        ("networksetup", "-getsecurewebproxy", "Wi-Fi"): "Enabled: No\nServer: \nPort: 0\n",
        ("networksetup", "-getsocksfirewallproxy", "Wi-Fi"): "Enabled: No\nServer: \nPort: 0\n",
        ("networksetup", "-getproxybypassdomains", "Wi-Fi"): "There aren't any Bypass Domains set on Wi-Fi.\n",
        ("networksetup", "-listallnetworkservices"): "An asterisk (*) denotes that a network service is disabled.\nWi-Fi\n",
    }
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(tuple(cmd))
        stdout = outputs.get(tuple(cmd), "")
        return __import__("subprocess").CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("baleobala.control.macos.subprocess.run", fake_run)

    session = MacOSSystemProxySession(listen_host="127.0.0.1", listen_port=1080)
    session.start()
    assert session.active is True
    assert session.snapshots[0].name == "Wi-Fi"
    session.stop()
    assert session.active is False

    assert ("networksetup", "-setwebproxy", "Wi-Fi", "127.0.0.1", "1080") in calls
    assert ("networksetup", "-setwebproxystate", "Wi-Fi", "on") in calls
    assert ("networksetup", "-setsecurewebproxy", "Wi-Fi", "127.0.0.1", "1080") in calls
    assert ("networksetup", "-setsecurewebproxystate", "Wi-Fi", "on") in calls
    assert ("networksetup", "-setsocksfirewallproxy", "Wi-Fi", "127.0.0.1", "1080") in calls
    assert ("networksetup", "-setsocksfirewallproxystate", "Wi-Fi", "on") in calls
    assert ("networksetup", "-setwebproxystate", "Wi-Fi", "off") in calls
    assert ("networksetup", "-setsecurewebproxystate", "Wi-Fi", "off") in calls
    assert ("networksetup", "-setsocksfirewallproxystate", "Wi-Fi", "off") in calls


def test_macos_system_proxy_restore_saved_state(tmp_path, monkeypatch) -> None:
    from baleobala.control.macos import MacOSSystemProxySession

    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    state_file = tmp_path / "macos_system_proxy.json"
    outputs = {
        ("networksetup", "-getwebproxy", "Wi-Fi"): "Enabled: No\nServer: \nPort: 0\n",
        ("networksetup", "-getsecurewebproxy", "Wi-Fi"): "Enabled: No\nServer: \nPort: 0\n",
        ("networksetup", "-getsocksfirewallproxy", "Wi-Fi"): "Enabled: No\nServer: \nPort: 0\n",
        ("networksetup", "-getproxybypassdomains", "Wi-Fi"): "There aren't any Bypass Domains set on Wi-Fi.\n",
    }
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(tuple(cmd))
        stdout = outputs.get(tuple(cmd), "")
        return __import__("subprocess").CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    session = MacOSSystemProxySession(
        listen_host="127.0.0.1",
        listen_port=1080,
        services=["Wi-Fi"],
        state_path=state_file,
        runner=fake_run,
    )
    session.start()
    assert state_file.exists()

    restored = MacOSSystemProxySession.restore_saved_state(state_path=state_file, runner=fake_run)
    assert restored is True
    assert not state_file.exists()
    assert ("networksetup", "-setwebproxystate", "Wi-Fi", "off") in calls


def test_macos_system_proxy_services_from_env(monkeypatch) -> None:
    from baleobala.control.macos import MacOSSystemProxySession

    monkeypatch.setenv("BALEOBALA_MACOS_PROXY_SERVICES", "Wi-Fi, V2BOX")
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(tuple(cmd))
        stdout = ""
        if cmd[1].startswith("-get"):
            stdout = "Enabled: No\nServer: \nPort: 0\n"
        return __import__("subprocess").CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    session = MacOSSystemProxySession(listen_host="127.0.0.1", listen_port=1080, runner=fake_run)
    session.start()
    session.stop()

    assert ("networksetup", "-listallnetworkservices") not in calls
    assert ("networksetup", "-setwebproxy", "Wi-Fi", "127.0.0.1", "1080") in calls
    assert ("networksetup", "-setwebproxystate", "Wi-Fi", "on") in calls
    assert ("networksetup", "-setwebproxy", "V2BOX", "127.0.0.1", "1080") in calls
    assert ("networksetup", "-setwebproxystate", "V2BOX", "on") in calls


def test_macos_system_proxy_privileged_setters_use_osascript(monkeypatch) -> None:
    from baleobala.control.macos import MacOSSystemProxySession

    monkeypatch.setenv("BALEOBALA_MACOS_PROXY_PRIVILEGED", "1")
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(tuple(cmd))
        stdout = ""
        if cmd[:2] != ["osascript", "-e"] and cmd[1].startswith("-get"):
            stdout = "Enabled: No\nServer: \nPort: 0\n"
        return __import__("subprocess").CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    session = MacOSSystemProxySession(
        listen_host="127.0.0.1",
        listen_port=1080,
        services=["Wi-Fi"],
        runner=fake_run,
    )
    session.start()

    assert ("networksetup", "-getwebproxy", "Wi-Fi") in calls
    privileged_calls = [call for call in calls if call[:2] == ("osascript", "-e")]
    assert len(privileged_calls) == 1
    assert "with administrator privileges" in privileged_calls[0][2]
    assert "networksetup -setwebproxy Wi-Fi 127.0.0.1 1080" in privileged_calls[0][2]
    assert "networksetup -setwebproxystate Wi-Fi on" in privileged_calls[0][2]
    assert "networksetup -setsocksfirewallproxystate Wi-Fi on" in privileged_calls[0][2]


def test_macos_system_proxy_privileged_restore_is_batched(tmp_path, monkeypatch) -> None:
    from baleobala.control.macos import MacOSSystemProxySession

    monkeypatch.setenv("BALEOBALA_MACOS_PROXY_PRIVILEGED", "1")
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    state_file = tmp_path / "macos_system_proxy.json"
    state_file.write_text(
        __import__("json").dumps(
            {
                "items": [
                    {
                        "name": "Wi-Fi",
                        "web": {"enabled": False, "server": None, "port": None},
                        "secure_web": {"enabled": False, "server": None, "port": None},
                        "socks": {"enabled": False, "server": None, "port": None},
                        "bypass_domains": [],
                    },
                    {
                        "name": "V2BOX",
                        "web": {"enabled": False, "server": None, "port": None},
                        "secure_web": {"enabled": False, "server": None, "port": None},
                        "socks": {"enabled": False, "server": None, "port": None},
                        "bypass_domains": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, check=True, capture_output=True, text=True):  # noqa: ANN001
        calls.append(tuple(cmd))
        return __import__("subprocess").CompletedProcess(cmd, 0, stdout="", stderr="")

    assert MacOSSystemProxySession.restore_saved_state(state_path=state_file, runner=fake_run) is True
    privileged_calls = [call for call in calls if call[:2] == ("osascript", "-e")]
    assert len(privileged_calls) == 1
    assert "networksetup -setwebproxystate Wi-Fi off" in privileged_calls[0][2]
    assert "networksetup -setwebproxystate V2BOX off" in privileged_calls[0][2]


def test_vpn_up_uses_saved_pairing_and_auth(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, PairingStore, VpnStore
    import baleobala.cli as cli

    auth_store = AuthStore()
    auth_store.save_jwt("jwt-token", user_id=99, phone="+989")
    pairing_store = PairingStore()
    pairing = pairing_store.begin("relay-a", role="client", peer_id=777, relay_mode="proxy")
    pairing_store.accept(pairing.pair_code)
    vpn_store = VpnStore()
    vpn_store.save(
        __import__("baleobala.control.vpn", fromlist=["VpnProfile"]).VpnProfile(
            profile_id=pairing.profile_id,
            name="client",
            backend="proxy",
            role="client",
            pairing_id=pairing.profile_id,
            peer_id=777,
            answer=False,
            listen_host="127.0.0.1",
            listen_port=1080,
            protocol="fast",
            volume=50,
        )
    )

    captured = {}

    def fake_client(args):  # noqa: ANN001
        captured["args"] = args
        return 0

    class FakeProxySession:
        def __init__(self, **kwargs):  # noqa: ANN001
            captured["proxy_kwargs"] = kwargs
        def start(self):
            captured["proxy_start"] = True
        def stop(self):
            captured["proxy_stop"] = True
        def status(self):
            return {"proxy": "127.0.0.1:1080"}

    monkeypatch.setattr(cli, "cmd_bale_proxy_client", fake_client)
    monkeypatch.setattr("baleobala.control.macos.MacOSSystemProxySession", FakeProxySession)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None)) == 0
    captured_io = capsys.readouterr()
    assert "macOS system proxy active" in captured_io.err
    assert captured["args"].peer_id == 777
    assert captured["args"].answer is False
    assert captured["proxy_start"] is True
    assert captured["proxy_stop"] is True


def test_vpn_up_autoselects_active_pairing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, PairingStore, VpnStore
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    pairing_store = PairingStore()
    pending = pairing_store.begin("relay-a", role="client", peer_id=777, relay_mode="proxy")
    pairing_store.accept(pending.pair_code)
    active_pairing = pairing_store.active()
    VpnStore().ensure_default()

    captured = {}

    def fake_start_connection(self, profile_id=None):  # noqa: ANN001,ARG001
        from baleobala.control import ConnectionSnapshot, VpnStore

        stored = VpnStore().load() or VpnStore().ensure_default()
        captured["stored_profile"] = stored
        return ConnectionSnapshot(
            profile=stored,
            pairing=active_pairing,
            backend={
                "backend": "packet-tunnel",
                "state": "running",
                "endpoint": "unix://carrier.sock",
            },
            probe={},
            connection={},
        )

    monkeypatch.setattr("baleobala.control.service.ControlService.start_connection", fake_start_connection)
    monkeypatch.setattr("baleobala.control.service.ControlService.stop_connection", lambda self, profile_id=None: None)
    monkeypatch.setattr(cli, "_hold_backend", lambda endpoint, *, label: 0)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None)) == 0
    assert captured["stored_profile"].backend == "packet-tunnel"
    assert captured["stored_profile"].pairing_id == active_pairing.profile_id
    # pairing is applied to profile
    stored = VpnStore().load()
    assert stored is not None
    assert active_pairing is not None
    assert stored.pairing_id == active_pairing.profile_id
    assert stored.backend == "packet-tunnel"


def test_vpn_up_prefers_packet_tunnel_on_darwin(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, PairingStore, VpnStore
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    pairing_store = PairingStore()
    pairing = pairing_store.begin("relay-a", role="client", peer_id=777, relay_mode="proxy")
    pairing_store.accept(pairing.pair_code)
    profile = VpnStore().ensure_default()
    assert profile.backend == "packet-tunnel"

    captured = {}

    def fake_start_connection(self, profile_id=None):  # noqa: ANN001,ARG001
        from baleobala.control import ConnectionSnapshot, VpnStore

        stored = VpnStore().load() or VpnStore().ensure_default()
        captured["stored_profile"] = stored
        return ConnectionSnapshot(
            profile=stored,
            pairing=pairing_store.active(),
            backend={
                "backend": "packet-tunnel",
                "state": "running",
                "endpoint": "unix://carrier.sock",
            },
            probe={},
            connection={},
        )

    monkeypatch.setattr("baleobala.control.service.ControlService.start_connection", fake_start_connection)
    monkeypatch.setattr("baleobala.control.service.ControlService.stop_connection", lambda self, profile_id=None: None)

    def fake_hold_backend(endpoint, *, label):  # noqa: ANN001
        captured["held"] = (endpoint, label)
        return 0

    monkeypatch.setattr(cli, "_hold_backend", fake_hold_backend)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None, backend=None)) == 0
    assert captured["held"][0] == "unix://carrier.sock"
    assert captured["stored_profile"].backend == "packet-tunnel"


def test_vpn_up_packet_tunnel_uses_active_pairing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, VpnStore
    from baleobala.control.vpn import VpnProfile
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    VpnStore().save(
        VpnProfile(profile_id="default", name="default", backend="packet-tunnel")
    )

    captured = {}

    def fake_start_connection(self, profile_id=None):  # noqa: ANN001,ARG001
        from baleobala.control import ConnectionSnapshot, VpnStore

        stored = VpnStore().load() or VpnStore().ensure_default()
        captured["stored_profile"] = stored
        return ConnectionSnapshot(
            profile=stored,
            pairing=None,
            backend={
                "backend": "packet-tunnel",
                "state": "running",
                "endpoint": "unix://carrier.sock",
            },
            probe={},
            connection={},
        )

    monkeypatch.setattr("baleobala.control.service.ControlService.start_connection", fake_start_connection)
    monkeypatch.setattr("baleobala.control.service.ControlService.stop_connection", lambda self, profile_id=None: None)
    monkeypatch.setattr(cli, "_hold_backend", lambda endpoint, *, label: 0)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None, backend=None)) == 0
    assert captured["stored_profile"].backend == "packet-tunnel"


def test_vpn_up_uses_linux_tun_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")

    from baleobala.control import AuthStore, PairingStore, VpnStore
    from baleobala.control.vpn import VpnProfile
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    pairing_store = PairingStore()
    pairing = pairing_store.begin("relay-a", role="client", peer_id=777, relay_mode="proxy")
    pairing_store.accept(pairing.pair_code)
    VpnStore().save(
        VpnProfile(
            profile_id=pairing.profile_id,
            name="client",
            backend="linux-tun",
            role="client",
            pairing_id=pairing.profile_id,
            peer_id=777,
            answer=False,
        )
    )

    events: list[Any] = []

    class FakeBackend:
        def up(self, profile):  # noqa: ANN001
            events.append(("up", profile.backend))
            return {"backend": "linux-tun", "state": "running"}

        def down(self):
            events.append(("down", None))

    def fake_backend_for_profile(profile):  # noqa: ANN001
        events.append(("factory", profile.backend))
        return FakeBackend()

    def fake_run_tunnel_session(args, *, is_exit_node):  # noqa: ANN001
        events.append((
            "session",
            {
                "cmd": args.tun,
                "addr": args.tun_addr,
                "identity": args.identity,
                "peer_id": args.peer_id,
                "is_exit_node": is_exit_node,
            },
        ))
        return 0

    monkeypatch.setattr("baleobala.control.backend.backend_for_profile", fake_backend_for_profile)
    monkeypatch.setattr("baleobala.vpn.cli._run_tunnel_session", fake_run_tunnel_session)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None, backend=None)) == 0
    assert events[0] == ("factory", "linux-tun")
    assert events[1] == ("up", "linux-tun")
    assert events[2][0] == "session"
    assert events[2][1]["cmd"] == "vpn0"
    assert events[2][1]["addr"] == "10.77.0.2/24"
    assert events[2][1]["peer_id"] == 777
    assert events[2][1]["is_exit_node"] is False
    assert events[-1] == ("down", None)


def test_vpn_up_linux_tun_exit_node_runs_nat_setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")

    from baleobala.control import AuthStore, PairingStore, VpnStore
    from baleobala.control.vpn import VpnProfile
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    pairing_store = PairingStore()
    pairing = pairing_store.begin("relay-a", role="relay", peer_id=777, relay_mode="proxy")
    pairing_store.accept(pairing.pair_code)
    VpnStore().save(
        VpnProfile(
            profile_id=pairing.profile_id,
            name="relay",
            backend="linux-tun",
            role="relay",
            pairing_id=pairing.profile_id,
            peer_id=777,
            answer=True,
        )
    )

    events: list[Any] = []

    class FakeBackend:
        def up(self, profile):  # noqa: ANN001
            events.append(("up", profile.backend))
            return {"backend": "linux-tun", "state": "running"}

        def down(self):
            events.append(("down", None))

    def fake_backend_for_profile(profile):  # noqa: ANN001
        events.append(("factory", profile.backend))
        return FakeBackend()

    def fake_nat_setup(tun, wan):  # noqa: ANN001
        events.append(("nat", (tun, wan)))

    def fake_run_tunnel_session(args, *, is_exit_node):  # noqa: ANN001
        events.append((
            "session",
            {
                "cmd": args.tun,
                "addr": args.tun_addr,
                "identity": args.identity,
                "answer": args.answer,
                "is_exit_node": is_exit_node,
            },
        ))
        return 0

    monkeypatch.setattr("baleobala.control.backend.backend_for_profile", fake_backend_for_profile)
    monkeypatch.setattr("baleobala.vpn.cli._run_nat_setup", fake_nat_setup)
    monkeypatch.setattr("baleobala.vpn.cli._run_tunnel_session", fake_run_tunnel_session)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None, backend=None)) == 0
    assert events[0] == ("factory", "linux-tun")
    assert events[1] == ("up", "linux-tun")
    assert events[2] == ("nat", ("vpn0", "eth0"))
    assert events[3][0] == "session"
    assert events[3][1]["addr"] == "10.77.0.1/24"
    assert events[3][1]["answer"] is True
    assert events[3][1]["is_exit_node"] is True
    assert events[-1] == ("down", None)
