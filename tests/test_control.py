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


def test_packet_tunnel_backend_tracks_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import VpnProfile
    from baleobala.control.backend import MacOSPacketTunnelBackend

    backend = MacOSPacketTunnelBackend()
    profile = VpnProfile(profile_id="p1", name="client", backend="packet-tunnel")

    status = backend.up(profile)
    assert status["backend"] == "packet-tunnel"
    assert status["state"] == "running"
    assert status["transport_ready"] == "no"
    assert status["call_established"] == "no"
    assert status["teardown_clean"] == "no"
    assert status["profile_id"] == "p1"
    backend.down()
    assert backend.status()["state"] == "stopped"


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


def test_control_service_reconcile_clears_stale_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setenv("BALEOBALA_SECRET_BACKEND", "file")
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, ControlService, PairingStore, VpnStore
    from baleobala.control.vpn import VpnProfile

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

    runtime_file = tmp_path / "tunnel_service.json"
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


def test_bundle_analyzer_accepts_complete_success_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "verdict.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
    )

    analysis = analyze_bundle(bundle)
    assert analysis.classification == "accepted_flow"
    assert analysis.ok == "yes"


def test_bundle_analyzer_uses_failure_class_for_carrier_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import analyze_bundle

    bundle = tmp_path / "bundle-carrier"
    bundle.mkdir()
    (bundle / "verdict.json").write_text(
        json.dumps(
            {
                "scenario": {"kind": "proxy-pair"},
                "setup": {"ok": "yes"},
                "smoke": {"ok": "no", "last_error": "process readiness timeout"},
                "teardown": {"ok": "yes"},
                "started": [{"name": "server"}],
                "stopped": [{"name": "server"}],
                "markers": ["call_established"],
                "failure_class": "transport_timeout",
                "log_tails": {"server": "call_established\n"},
            }
        ),
        encoding="utf-8",
    )

    analysis = analyze_bundle(bundle)
    assert analysis.classification == "carrier_instability"
    assert analysis.reason == "transport_timeout"


def test_merge_status_with_bundle_prefers_session_artifact_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.analyzer import merge_status_with_bundle

    bundle = tmp_path / "bundle-merge"
    bundle.mkdir()
    (bundle / "verdict.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
    )

    merged = merge_status_with_bundle(
        {
            "backend": "linux-tun",
            "state": "running",
            "call_established": "no",
            "transport_selected": "",
            "data_flow_ok": "no",
            "teardown_clean": "no",
            "failure_class": "",
        },
        bundle,
    )
    assert merged["backend"] == "proxy-pair"
    assert merged["call_established"] == "yes"
    assert merged["transport_selected"] == "dc"
    assert merged["data_flow_ok"] == "yes"
    assert merged["teardown_clean"] == "yes"
    assert merged["artifact_bundle"] == str(bundle)


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
            "failure_class": "",
            "artifact_bundle": "/tmp/bundle",
        },
        BundleAnalysis("accepted_flow", "bundle indicates accepted flow", "/tmp/bundle", "yes"),
    )
    assert verdict.ok == "yes"
    assert verdict.failure_class == ""
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
    assert "netns-plan" in vpn_subcommands
    assert "netns-run" in vpn_subcommands
    assert "netns-process-plan" in vpn_subcommands
    assert "netns-process-run" in vpn_subcommands
    assert "netns-session" in vpn_subcommands
    assert "netns-scenario" in vpn_subcommands

    vpn_up_parser = vpn_subcommands["up"]
    assert "linux-tun" in _arg_choices(vpn_up_parser, "backend")

    relay_parser = subcommands["relay"]
    relay_subcommands = _subparser_choices(relay_parser)
    relay_enable_parser = relay_subcommands["enable"]
    assert "linux-tun" in _arg_choices(relay_enable_parser, "backend")

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

    assert ("networksetup", "-setwebproxy", "Wi-Fi", "127.0.0.1", "1080", "on") in calls
    assert ("networksetup", "-setsecurewebproxy", "Wi-Fi", "127.0.0.1", "1080", "on") in calls
    assert ("networksetup", "-setsocksfirewallproxy", "Wi-Fi", "127.0.0.1", "1080", "on") in calls
    assert ("networksetup", "-setwebproxy", "Wi-Fi", "off") in calls
    assert ("networksetup", "-setsecurewebproxy", "Wi-Fi", "off") in calls
    assert ("networksetup", "-setsocksfirewallproxy", "Wi-Fi", "off") in calls


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
    assert ("networksetup", "-setwebproxy", "Wi-Fi", "off") in calls


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

    class FakeServer:
        def __init__(self, **kwargs):
            captured["server_kwargs"] = kwargs
            self.bound_host = "127.0.0.1"
            self.bound_port = 1080

        def start(self):
            captured["server_start"] = True

        def wait_ready(self, timeout=2.0):
            return True

        def stop(self):
            captured["server_stop"] = True

    class FakeProxySession:
        def __init__(self, **kwargs):  # noqa: ANN001
            captured["proxy_kwargs"] = kwargs
        def start(self):
            captured["proxy_start"] = True
        def stop(self):
            captured["proxy_stop"] = True
            def status(self):
                return {"proxy": "127.0.0.1:1080"}

    monkeypatch.setattr("baleobala.control.macos.MacOSSystemProxySession", FakeProxySession)
    monkeypatch.setattr("baleobala.control.backend.DirectSocks5Server", FakeServer)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    def fake_start_packet_tunnel_runtime(profile, auth_record):  # noqa: ANN001,ARG001
        captured["profile"] = profile
        class Runtime:
            def start(self, **kwargs):  # noqa: ANN001
                captured["runtime_start"] = kwargs
                return __import__("types").SimpleNamespace(endpoint="unix://carrier.sock")

            def stop(self):
                captured["runtime_stop"] = True

        class Bridge:
            def close(self):
                captured["bridge_close"] = True

        return Runtime(), Bridge()

    monkeypatch.setattr(cli, "_start_packet_tunnel_runtime", fake_start_packet_tunnel_runtime)
    monkeypatch.setattr(cli, "_hold_backend", lambda endpoint, *, label: 0)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None)) == 0
    assert captured["profile"].backend == "packet-tunnel"
    assert captured["runtime_stop"] is True
    assert captured["bridge_close"] is True
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

    class FakeServer:
        def __init__(self, **kwargs):
            self.bound_host = "127.0.0.1"
            self.bound_port = 1080

        def start(self):
            captured["server_start"] = True

        def wait_ready(self, timeout=2.0):
            return True

        def stop(self):
            captured["server_stop"] = True

    class FakeProxySession:
        def __init__(self, **kwargs):
            pass
        def start(self):
            captured["proxy_start"] = True
        def stop(self):
            captured["proxy_stop"] = True
        def status(self):
            return {"proxy": "127.0.0.1:1080"}

    monkeypatch.setattr("baleobala.control.macos.MacOSSystemProxySession", FakeProxySession)
    monkeypatch.setattr("baleobala.control.backend.DirectSocks5Server", FakeServer)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    def fake_start_packet_tunnel_runtime(profile, auth_record):  # noqa: ANN001,ARG001
        class Runtime:
            def start(self, **kwargs):  # noqa: ANN001
                captured["runtime_start"] = kwargs
                return __import__("types").SimpleNamespace(endpoint="unix://carrier.sock")

            def stop(self):
                captured["runtime_stop"] = True

        class Bridge:
            def close(self):
                captured["bridge_close"] = True

        return Runtime(), Bridge()

    def fake_hold_backend(endpoint, *, label):  # noqa: ANN001
        captured["held"] = (endpoint, label)
        return 0

    monkeypatch.setattr(cli, "_start_packet_tunnel_runtime", fake_start_packet_tunnel_runtime)
    monkeypatch.setattr(cli, "_hold_backend", fake_hold_backend)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None, backend=None)) == 0
    assert captured["held"][0] == "unix://carrier.sock"
    assert captured["runtime_stop"] is True
    assert captured["bridge_close"] is True


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

    def fake_start_packet_tunnel_runtime(profile, auth_record):  # noqa: ANN001,ARG001
        captured["profile"] = profile
        class Runtime:
            def start(self, **kwargs):  # noqa: ANN001
                captured["runtime_start"] = kwargs
                return __import__("types").SimpleNamespace(endpoint="unix://carrier.sock")

            def stop(self):
                captured["runtime_stop"] = True

        class Bridge:
            def close(self):
                captured["bridge_close"] = True

        return Runtime(), Bridge()

    monkeypatch.setattr(cli, "_start_packet_tunnel_runtime", fake_start_packet_tunnel_runtime)
    monkeypatch.setattr(cli, "_hold_backend", lambda endpoint, *, label: 0)

    import argparse
    assert cli.cmd_vpn(argparse.Namespace(vpn_cmd="up", profile_id=None, backend=None)) == 0
    assert captured["profile"].backend == "packet-tunnel"
    assert captured["runtime_stop"] is True
    assert captured["bridge_close"] is True


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
