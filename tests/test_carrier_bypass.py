from __future__ import annotations

from types import SimpleNamespace

from baleobala.bale import LiveKitSession
from baleobala.control import linux as linux_mod


class _FakeResolver:
    instances: list["_FakeResolver"] = []

    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []
        type(self).instances.append(self)

    def add_bypass_host(self, hostname: str) -> None:
        self.added.append(hostname)

    def remove_bypass_host(self, hostname: str) -> None:
        self.removed.append(hostname)


class _FakeSession:
    def __init__(self, url: str, token: str, identity: str) -> None:
        self.url = url
        self.token = token
        self.identity = identity
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def carrier_hosts(self) -> set[str]:
        return {"next-ws.bale.ai", "sfu.example.org"}


class _FakeTun:
    def close(self) -> None:
        pass


class _FakeKeepalive:
    def __init__(self, session, interval: float) -> None:  # noqa: ANN001
        self.session = session
        self.interval = interval
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeChain:
    def __init__(self) -> None:
        self._choices = [SimpleNamespace(name="dc", factory=object())]
        self.closed = False

    def start(self):
        return "dc", object()

    def close(self) -> None:
        self.closed = True


class _FakeRunner:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeController:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.started = False
        self.stopped = False

    def start(self, transport_name: str) -> None:  # noqa: ARG002
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def test_livekit_session_carrier_hosts_include_sfu_hostname() -> None:
    session = LiveKitSession(url="wss://sfu.example.org/room", token="token")

    assert session.carrier_hosts == {"next-ws.bale.ai", "sfu.example.org"}


def test_linux_tunnel_runtime_programs_carrier_bypass_hosts(monkeypatch, tmp_path) -> None:
    _FakeResolver.instances = []
    monkeypatch.setattr("baleobala.cli._resolve_livekit_credentials", lambda args: ("wss://sfu.example.org/room", "token"))
    monkeypatch.setattr("baleobala.bale.LiveKitSession", _FakeSession)
    monkeypatch.setattr("baleobala.control.resolver.LinuxResolver", _FakeResolver)
    monkeypatch.setattr("baleobala.vpn.tun.TunDevice.open", lambda name: _FakeTun())
    monkeypatch.setattr("baleobala.vpn.keepalive.LiveKitKeepalive", _FakeKeepalive)
    monkeypatch.setattr("baleobala.vpn.cli._build_transport_chain", lambda transport, session, args: _FakeChain())
    monkeypatch.setattr("baleobala.vpn.cli._safe_mtu", lambda factory, precomputed=None: 1400)
    monkeypatch.setattr("baleobala.vpn.runner.VpnRunner", _FakeRunner)
    monkeypatch.setattr("baleobala.vpn.router.FailoverController", _FakeController)

    runtime = linux_mod.LinuxTunnelRuntime(state_store=linux_mod.JsonStore(tmp_path / "runtime.json"))
    profile = SimpleNamespace(
        profile_id="profile-a",
        pair_id=None,
        pairing_id="pairing-a",
        peer_id=7,
        answer=False,
        role="client",
        name="client",
        peer_name="relay",
        protocol="fast",
        volume=50,
    )
    auth = SimpleNamespace(jwt="jwt-token")

    payload = runtime.start(profile, auth)
    runtime.stop()

    assert payload["call_established"] == "yes"
    assert _FakeResolver.instances, "expected the runtime to construct a resolver"
    assert _FakeResolver.instances[0].added == ["next-ws.bale.ai", "sfu.example.org"]
    assert _FakeResolver.instances[0].removed == ["next-ws.bale.ai", "sfu.example.org"]
