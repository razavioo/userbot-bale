from __future__ import annotations

import argparse
import os
import time
from types import SimpleNamespace

import baleobala
import pytest

from baleobala.carrier.bale import BaleCarrierController
from baleobala.runtime import MemoryByteChannel, NullSecurityProvider, TunnelRole, TunnelSession
from baleobala.runtime.frame import TunnelFrame, TunnelFrameType, decode_tunnel_frame, encode_tunnel_frame
from baleobala.signal import Frame as SignalFrame


def test_top_level_package_imports_without_codec() -> None:
    assert hasattr(baleobala, "AudioSink")
    assert hasattr(baleobala, "Frame")
    assert SignalFrame.__name__ == "Frame"
    assert BaleCarrierController.__name__ == "BaleCarrierController"


def test_tunnel_frame_roundtrip() -> None:
    frame = TunnelFrame(
        frame_type=TunnelFrameType.DATA,
        seq=7,
        ack=3,
        payload=b"hello",
    )
    encoded = encode_tunnel_frame(frame)
    decoded = decode_tunnel_frame(encoded)
    assert decoded == frame


def test_tunnel_session_full_duplex_memory_channel() -> None:
    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(
        left_ch,
        role=TunnelRole.CLIENT,
        security=NullSecurityProvider(session_id="left"),
    )
    right = TunnelSession(
        right_ch,
        role=TunnelRole.SERVER,
        security=NullSecurityProvider(session_id="right"),
    )

    left.open()
    right.open()

    left.send(b"alpha")
    right.send(b"beta")

    assert right.recv(timeout=0.1) == b"alpha"
    assert left.recv(timeout=0.1) == b"beta"

    left.close()
    right.close()


def test_cli_tunnel_loopback() -> None:
    from baleobala.cli import cmd_tunnel_loopback

    class Args:
        messages = ["one", "two"]

    assert cmd_tunnel_loopback(Args()) == 0


def test_cli_doctor_smoke(capsys) -> None:
    from baleobala.cli import cmd_doctor

    class Args:
        strict = False

    assert cmd_doctor(Args()) == 0
    out = capsys.readouterr().out
    assert "baleobala doctor" in out


def test_build_parser_exposes_bale_tunnel() -> None:
    from baleobala.cli import build_parser

    parser = build_parser()
    subcommands = {}
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            subcommands = choices
            break
    assert "bale-tunnel" in subcommands
    assert "bale-proxy" in subcommands
    proxy_parser = subcommands["bale-proxy"]
    proxy_subcommands = {}
    for action in proxy_parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            proxy_subcommands = choices
            break
    assert "browser" in proxy_subcommands
    assert "system" in proxy_subcommands
    assert "doctor" in subcommands
    assert parser.parse_args(["bale-proxy", "client"]).direct_first is True
    assert parser.parse_args(["bale-proxy", "browser"]).direct_first is True
    assert parser.parse_args(["bale-proxy", "system"]).direct_first is True
    assert parser.parse_args(["bale-proxy", "system", "--no-direct-first"]).direct_first is False
    parsed = parser.parse_args(
        [
            "bale-proxy",
            "system",
            "--direct-first",
            "--direct-only-domain",
            "digikala.com",
            "--tunnel-only-domain",
            "example.org",
        ]
    )
    assert parsed.direct_only_domain == ["digikala.com"]
    assert parsed.tunnel_only_domain == ["example.org"]


def test_vpn_proxy_profile_enables_policy_backed_direct_first(monkeypatch) -> None:
    from baleobala.cli import _vpn_namespace_from_profile
    from baleobala.control.vpn import VpnProfile

    monkeypatch.delenv("BALEOBALA_PROXY_DIRECT_FIRST", raising=False)
    monkeypatch.delenv("BALEOBALA_PROXY_POLICY_REFRESH", raising=False)
    proxy_args = _vpn_namespace_from_profile(
        VpnProfile(profile_id="p1", name="proxy", backend="proxy"),
        auth_record=None,
    )
    assert proxy_args.direct_first is True
    assert proxy_args.proxy_policy_refresh is False
    assert proxy_args.direct_only_domain == []
    assert proxy_args.tunnel_only_domain == []

    monkeypatch.setenv("BALEOBALA_PROXY_POLICY_REFRESH", "1")
    env_args = _vpn_namespace_from_profile(
        VpnProfile(profile_id="p2", name="proxy", backend="proxy"),
        auth_record=None,
    )

    assert env_args.direct_first is True
    assert env_args.proxy_policy_refresh is True


def test_proxy_runtime_uses_direct_first_by_default(monkeypatch) -> None:
    import baleobala.cli as cli

    class FakeTransport:
        mtu = 1400
        closed = False

        def send(self, data):  # noqa: ANN001
            pass

        def recv(self, timeout=None):  # noqa: ANN001
            return None

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "_open_proxy_transport", lambda args, role: ("dc", FakeTransport()))
    monkeypatch.setattr(cli, "_resolve_proxy_secret", lambda args: None)
    monkeypatch.setattr("baleobala.runtime.proxy_policy.load_proxy_domain_policy", lambda **kwargs: type("P", (), {"direct_only_domains": ("ir",), "tunnel_only_domains": ("youtube.com",)})())

    args = argparse.Namespace(
        listen_host="127.0.0.1",
        listen_port=0,
        direct_first=True,
        direct_only_domain=[],
        tunnel_only_domain=[],
        proxy_policy_refresh=False,
    )
    _name, _transport, server = cli._start_proxy_client_runtime(args)

    from baleobala.runtime.proxy import DirectFirstSocks5ProxyServer

    assert isinstance(server, DirectFirstSocks5ProxyServer)
    assert server._matches_direct_only("example.ir") is True  # noqa: SLF001
    assert server._matches_tunnel_only("www.youtube.com") is True  # noqa: SLF001


def test_bale_proxy_system_enables_proxy_after_listener(monkeypatch) -> None:
    import baleobala.cli as cli

    monkeypatch.setattr("sys.platform", "darwin")
    events: list[str] = []

    class FakeTransport:
        def close(self) -> None:
            events.append("transport.close")

    class FakeServer:
        def __init__(self, on_listen):  # noqa: ANN001
            self._on_listen = on_listen

        def serve_forever(self) -> None:
            events.append("serve")
            self._on_listen("127.0.0.1", 1080)
            time.sleep(0.01)

        def stop(self) -> None:
            events.append("server.stop")

    class FakeSystemProxy:
        def __init__(self, **kwargs):  # noqa: ANN001
            events.append(f"proxy.init:{kwargs['services']}")

        def start(self) -> None:
            events.append("proxy.start")

        def stop(self) -> None:
            events.append("proxy.stop")

        def status(self) -> dict[str, str]:
            return {"proxy": "127.0.0.1:1080"}

    def fake_start_proxy_client_runtime(args, *, on_listen=None):  # noqa: ANN001
        return "dc", FakeTransport(), FakeServer(on_listen)

    monkeypatch.setattr(cli, "_start_proxy_client_runtime", fake_start_proxy_client_runtime)
    monkeypatch.setattr("baleobala.control.macos.MacOSSystemProxySession", FakeSystemProxy)

    args = argparse.Namespace(
        listen_host="127.0.0.1",
        listen_port=1080,
        service=["Wi-Fi"],
        proxy_ready_timeout=1.0,
    )

    assert cli.cmd_bale_proxy_system(args) == 1
    assert events.index("serve") < events.index("proxy.start")
    assert events[-3:] == ["proxy.stop", "server.stop", "transport.close"]


def test_bale_proxy_system_surfaces_prelisten_server_error(monkeypatch) -> None:
    import baleobala.cli as cli

    monkeypatch.setattr("sys.platform", "darwin")
    events: list[str] = []

    class FakeTransport:
        def close(self) -> None:
            events.append("transport.close")

    class FakeServer:
        def serve_forever(self) -> None:
            raise TimeoutError("proxy handshake failed")

        def stop(self) -> None:
            events.append("server.stop")

    class FakeSystemProxy:
        def __init__(self, **kwargs):  # noqa: ANN001
            pass

        def start(self) -> None:
            events.append("proxy.start")

        def stop(self) -> None:
            events.append("proxy.stop")

    def fake_start_proxy_client_runtime(args, *, on_listen=None):  # noqa: ANN001
        return "dc", FakeTransport(), FakeServer()

    monkeypatch.setattr(cli, "_start_proxy_client_runtime", fake_start_proxy_client_runtime)
    monkeypatch.setattr("baleobala.control.macos.MacOSSystemProxySession", FakeSystemProxy)

    args = argparse.Namespace(
        listen_host="127.0.0.1",
        listen_port=1080,
        service=["Wi-Fi"],
        proxy_ready_timeout=1.0,
    )

    with pytest.raises(RuntimeError, match="proxy server exited before listening: TimeoutError: proxy handshake failed"):
        cli.cmd_bale_proxy_system(args)

    assert "proxy.start" not in events
    assert events[-2:] == ["server.stop", "transport.close"]


def test_bonded_proxy_transport_keeps_stable_single_call_topic(monkeypatch, tmp_path) -> None:
    import baleobala.bale as bale_pkg
    import baleobala.cli as cli
    import baleobala.bale.api as bale_api
    import baleobala.vpn.keepalive as keepalive_mod

    topics: list[str] = []
    jwts: list[str | None] = []
    jwt_file = tmp_path / "client_jwt.txt"
    jwt_file.write_text("jwt-from-home\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))

    class FakeClient:
        def __init__(self, **kwargs):  # noqa: ANN001
            jwts.append(kwargs.get("jwt"))

        def start(self) -> None:
            pass

        def fetch_livekit_credentials(self, peer_id, *, creds_timeout):  # noqa: ANN001
            return SimpleNamespace(url="wss://meet.example", token="token")

        def stop(self) -> None:
            pass

    class FakeChannel:
        mtu = 1400
        rate_hint = 100.0
        closed = False

        def send_bytes(self, data: bytes) -> None:
            pass

        def recv_bytes(self, timeout=None):  # noqa: ANN001
            time.sleep(min(timeout or 0, 0.001))
            return None

        def close(self) -> None:
            self.closed = True

    class FakeSession:
        def __init__(self, *, url: str, token: str, identity: str) -> None:
            pass

        def start(self) -> None:
            pass

        def data_channel(self, *, topic: str, reliable: bool):  # noqa: ANN001
            topics.append(topic)
            return FakeChannel()

        def stop(self) -> None:
            pass

    class FakeKeepalive:
        def __init__(self, session, *, interval: float) -> None:  # noqa: ANN001
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(bale_api, "BaleApiClient", FakeClient)
    monkeypatch.setattr(bale_pkg, "LiveKitSession", FakeSession)
    monkeypatch.setattr(keepalive_mod, "LiveKitKeepalive", FakeKeepalive)
    monkeypatch.setattr(cli, "_emit_marker", lambda marker: None)
    monkeypatch.setattr(cli, "LIVEKIT_SETTLE_DELAY", 0.0, raising=False)

    args = argparse.Namespace(
        bale_jwt=None,
        bale_jwt_file="~/client_jwt.txt",
        peer_id=123,
        answer=False,
        creds_timeout=1.0,
        identity="client",
        ws_ca_file=None,
        ws_ca_path=None,
        ws_ssl_no_verify=False,
    )

    _name, transport = cli._open_bonded_proxy_transport(args, object(), 4)
    try:
        assert topics == ["vpn"]
        assert jwts == ["jwt-from-home"]
    finally:
        transport.close()


def test_bonded_proxy_transport_reports_missing_jwt_file(tmp_path) -> None:
    import baleobala.cli as cli

    missing = tmp_path / "missing.jwt"
    args = argparse.Namespace(
        bale_jwt=None,
        bale_jwt_file=str(missing),
        peer_id=123,
        answer=False,
    )

    with pytest.raises(RuntimeError, match=f"Bale JWT file not found: {missing}"):
        cli._open_bonded_proxy_transport(args, object(), 4)


def test_clean_qt_environment_removes_sdk_overrides() -> None:
    from baleobala.cli import _clean_qt_environment, _qt_environment_is_contaminated

    env = {
        "PATH": "/usr/bin",
        "LD_LIBRARY_PATH": os.pathsep.join(
            ["/home/taheri/Qt/6.8.3/gcc_64/lib", "/usr/lib", "/opt/pyside/lib"]
        ),
        "QT_PLUGIN_PATH": "/home/taheri/Qt/6.8.3/gcc_64/plugins",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/home/taheri/Qt/6.8.3/gcc_64/plugins/platforms",
        "QT_DEBUG_PLUGINS": "1",
    }

    assert _qt_environment_is_contaminated(env)
    cleaned = _clean_qt_environment(env)
    assert "QT_PLUGIN_PATH" not in cleaned
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in cleaned
    assert "QT_DEBUG_PLUGINS" not in cleaned
    assert "/home/taheri/Qt/6.8.3/gcc_64/lib" not in cleaned.get("LD_LIBRARY_PATH", "")
    assert "/usr/lib" in cleaned.get("LD_LIBRARY_PATH", "")
    assert "/opt/pyside/lib" in cleaned.get("LD_LIBRARY_PATH", "")
