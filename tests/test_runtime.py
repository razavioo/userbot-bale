from __future__ import annotations

import argparse
import os
import time

import baleobala

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

    assert cli.cmd_bale_proxy_system(args) == 0
    assert events.index("serve") < events.index("proxy.start")
    assert events[-3:] == ["proxy.stop", "server.stop", "transport.close"]


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
