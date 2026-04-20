from __future__ import annotations

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
    assert "doctor" in subcommands
