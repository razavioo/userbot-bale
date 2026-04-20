from __future__ import annotations

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


def test_vpn_store_default_and_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import VpnStore
    from baleobala.control.vpn import default_vpn_backend

    store = VpnStore()
    profile = store.ensure_default()
    assert profile.profile_id == "default"
    assert store.status()["backend"] == default_vpn_backend()
    assert store.status()["auto_start"] == "no"


def test_packet_tunnel_backend_tracks_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control import VpnProfile
    from baleobala.control.backend import MacOSPacketTunnelBackend

    backend = MacOSPacketTunnelBackend()
    profile = VpnProfile(profile_id="p1", name="client", backend="packet-tunnel")

    status = backend.up(profile)
    assert status["backend"] == "packet-tunnel"
    assert status["state"] == "running"
    assert status["profile_id"] == "p1"
    backend.down()
    assert backend.status()["state"] == "stopped"


def test_local_tunnel_service_ipc_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    from baleobala.control.tunnel_service import LocalTunnelService
    import socket

    service = LocalTunnelService()
    state = service.start(profile_id="p1", backend="packet-tunnel", pairing_id="pair-1")
    assert state.state == "running"
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

    socket_path = runtime.status().endpoint.removeprefix("unix://")

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

    parser = build_parser()
    subcommands = {}
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            subcommands = choices
            break

    for cmd in ("auth", "pair", "relay", "vpn"):
        assert cmd in subcommands
    vpn_parser = subcommands["vpn"]
    vpn_subcommands = {}
    for action in vpn_parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            vpn_subcommands = choices
            break
    assert "agent" in vpn_subcommands


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

    class Args:
        vpn_cmd = "up"
        profile_id = None

    assert cli.cmd_vpn(Args()) == 0
    captured_io = capsys.readouterr()
    assert "macOS system proxy active" in captured_io.err
    assert captured["args"].peer_id == 777
    assert captured["args"].answer is False
    assert captured["proxy_start"] is True
    assert captured["proxy_stop"] is True


def test_vpn_up_autoselects_active_pairing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
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

    class Args:
        vpn_cmd = "up"
        profile_id = None

    assert cli.cmd_vpn(Args()) == 0
    assert captured["server_start"] is True
    assert captured["proxy_start"] is True
    assert captured["proxy_stop"] is True
    assert captured["server_stop"] is True
    # pairing is applied to profile
    stored = VpnStore().load()
    assert stored is not None
    assert stored.pairing_id == active_pairing.profile_id
    assert stored.backend == "direct"


def test_vpn_up_prefers_direct_on_darwin(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, PairingStore, VpnStore
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    pairing_store = PairingStore()
    pairing = pairing_store.begin("relay-a", role="client", peer_id=777, relay_mode="proxy")
    pairing_store.accept(pairing.pair_code)
    VpnStore().ensure_default()

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

    class Args:
        vpn_cmd = "up"
        profile_id = None
        backend = None

    assert cli.cmd_vpn(Args()) == 0
    err = capsys.readouterr().err
    assert "macOS direct proxy active" in err or "macOS system proxy active" in err
    assert captured["server_start"] is True
    assert captured["proxy_start"] is True
    assert captured["server_stop"] is True
    assert captured["proxy_stop"] is True


def test_vpn_up_rejects_packet_tunnel_on_darwin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BALEOBALA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")

    from baleobala.control import AuthStore, VpnStore
    from baleobala.control.vpn import VpnProfile
    import baleobala.cli as cli

    AuthStore().save_jwt("jwt-token", user_id=99, phone="+989")
    VpnStore().save(
        VpnProfile(profile_id="default", name="default", backend="packet-tunnel")
    )

    class Args:
        vpn_cmd = "up"
        profile_id = None
        backend = None

    import pytest
    with pytest.raises(SystemExit) as excinfo:
        cli.cmd_vpn(Args())
    assert "packet-tunnel" in str(excinfo.value).lower()
    assert "direct" in str(excinfo.value).lower()
