"""
Command-line interface.

Subcommands:
    send     read messages from stdin (one per line) and transmit
    recv     listen, print each completed message on its own line
    devices  list audio devices sounddevice can see
    virtmic  create a virtual microphone and wait (Ctrl-C to tear down)
    loopback self-test: send → decode → compare, no virtual mic needed
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Iterator, Literal

from baleobala.virtmic import VirtualMic

log = logging.getLogger("baleobala")


def _pactl_has(kind: Literal["sinks", "sources"], name: str) -> bool:
    """Check if a PulseAudio sink/source with this exact name exists."""
    if shutil.which("pactl") is None:
        return False
    try:
        out = subprocess.run(
            ["pactl", "list", "short", kind],
            check=True, capture_output=True, text=True, timeout=3,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def _sounddevice_has(name: str, kind: Literal["input", "output"]) -> bool:
    import sounddevice as sd
    for d in sd.query_devices():
        if d["name"] == name and d[f"max_{kind}_channels"] > 0:
            return True
    return False


def _resolve_device(
    arg: str | None,
    kind: Literal["input", "output"],
) -> str | None:
    """
    Accept a device string that may be:
      * None                                 → default
      * a sounddevice device name or index   → pass through
      * a PulseAudio sink/source name        → route via "pulse" device + env var

    PortAudio on Linux exposes PulseAudio as a single "pulse" device; the
    way to target a specific sink/source is via PULSE_SINK / PULSE_SOURCE.
    This makes `--device baleobala_sink` work even though sounddevice never
    sees that name directly.
    """
    if arg is None:
        return None
    if arg.isdigit():
        return arg  # sounddevice accepts numeric indices via str
    if _sounddevice_has(arg, kind):
        return arg
    pulse_kind: Literal["sinks", "sources"] = "sinks" if kind == "output" else "sources"
    if _pactl_has(pulse_kind, arg):
        env_var = "PULSE_SINK" if kind == "output" else "PULSE_SOURCE"
        os.environ[env_var] = arg
        log.info("routing via pulse: %s=%s", env_var, arg)
        return "pulse"
    return arg  # let sounddevice raise its own error


def _proto(name: str) -> Protocol:
    from baleobala.codec import Protocol

    return {
        "normal": Protocol.AUDIBLE_NORMAL,
        "fast": Protocol.AUDIBLE_FAST,
        "fastest": Protocol.AUDIBLE_FASTEST,
    }[name]


def _stdin_lines() -> Iterator[str]:
    for line in sys.stdin:
        line = line.rstrip("\n")
        if line:
            yield line


def cmd_send(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device, "output")
    from baleobala.transmitter import Transmitter
    with Transmitter(
        device=device,
        protocol=_proto(args.protocol),
        volume=args.volume,
    ) as tx:
        source = [args.text] if args.text else _stdin_lines()
        for text in source:
            msg_id = tx.send(text)
            print(f"[tx] id={msg_id} bytes={len(text.encode('utf-8'))}", file=sys.stderr)
    return 0


def cmd_recv(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device, "input")
    from baleobala.receiver import Receiver
    with Receiver(device=device, protocol=_proto(args.protocol)) as rx:
        try:
            for msg in rx.iter_messages():
                print(msg.text(), flush=True)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_devices(_args: argparse.Namespace) -> int:
    import sounddevice as sd
    print(sd.query_devices())
    return 0


def cmd_virtmic(args: argparse.Namespace) -> int:
    with VirtualMic(name=args.name) as vm:
        print(f"Virtual mic ready.")
        print(f"  sink   (play here):      {vm.sink}")
        print(f"  source (mic for apps):   {vm.source}")
        print("Ctrl-C to tear down.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nTearing down virtual mic.")
    return 0


def cmd_loopback(args: argparse.Namespace) -> int:
    """Encode & decode in-process, no audio device — verifies the pipeline."""
    from baleobala.codec import Codec
    from baleobala.framing import Frame, Reassembler, fragment

    messages = args.messages or [f"hello-{i}" for i in range(5)]
    reasm = Reassembler()
    ok = 0
    with Codec(protocol=_proto(args.protocol)) as codec:
        for i, text in enumerate(messages):
            data = text.encode("utf-8")
            for frame in fragment(data, msg_id=i):
                wave = codec.encode(frame.encode())
                # Feed back in chunks to exercise the streaming decoder.
                chunk_size = 1024
                recovered: bytes | None = None
                for start in range(0, len(wave), chunk_size):
                    result = codec.decode_chunk(wave[start : start + chunk_size])
                    if result is not None:
                        recovered = result
                        break
                if recovered is None:
                    print(f"FAIL decode: {text!r}", file=sys.stderr)
                    continue
                decoded_frame = Frame.decode(recovered)
                if decoded_frame is None:
                    print(f"FAIL frame parse: {text!r}", file=sys.stderr)
                    continue
                assembled = reasm.push(decoded_frame)
                if assembled == data:
                    ok += 1
                    print(f"OK  [{i}] {text!r}")
                else:
                    print(f"FAIL mismatch: {text!r} vs {assembled!r}", file=sys.stderr)
    print(f"\n{ok}/{len(messages)} messages round-tripped.")
    return 0 if ok == len(messages) else 1


def cmd_tunnel_loopback(args: argparse.Namespace) -> int:
    """Exercise the byte-stream tunnel over an in-memory carrier."""
    from baleobala.runtime import MemoryByteChannel, TunnelRole, TunnelSession

    left_ch, right_ch = MemoryByteChannel.pair()
    left = TunnelSession(left_ch, role=TunnelRole.CLIENT)
    right = TunnelSession(right_ch, role=TunnelRole.SERVER)
    left.open()
    right.open()

    messages = [m.encode("utf-8") for m in (args.messages or ["alpha", "beta", "gamma"])]
    got: list[bytes] = []
    for msg in messages:
        left.send(msg)
        deadline = time.time() + 1.0
        item = None
        while time.time() < deadline and item is None:
            item = right.recv(timeout=0.05)
        got.append(item or b"")

    left.close()
    right.close()

    if got != messages:
        raise SystemExit(f"tunnel loopback mismatch: got={got!r}")
    for i, msg in enumerate(messages):
        print(f"OK  [{i}] {msg!r}")
    return 0


def _resolve_carrier_credentials(args: argparse.Namespace):
    from baleobala.carrier.bale import CarrierCredentials

    if args.livekit_url and args.livekit_token:
        return CarrierCredentials(
            url=args.livekit_url,
            token=args.livekit_token,
            room=args.livekit_room or "",
            identity=args.identity,
        )

    jwt = args.bale_jwt or os.environ.get("BALE_JWT")
    jwt_file = args.bale_jwt_file or "/tmp/bale_jwt.txt"
    if not jwt:
        from pathlib import Path
        jwt_path = Path(jwt_file)
        if jwt_path.exists():
            jwt = jwt_path.read_text().strip()
    if not jwt:
        raise SystemExit(
            "Need either --livekit-url/--livekit-token OR --bale-jwt (or BALE_JWT env var / /tmp/bale_jwt.txt)."
        )

    from baleobala.bale.api import BaleApiClient
    from baleobala.carrier.bale import BaleCarrierController

    controller = BaleCarrierController(client=BaleApiClient(jwt=jwt))
    try:
        peer_id = args.peer_id
        if peer_id is None and args.peer_name:
            matches = controller.search_contacts(args.peer_name)
            if not matches:
                raise SystemExit(f"no contacts match name {args.peer_name!r}")
            peer_id = matches[0].user_id
            print(
                f"[bale-call] matched name {args.peer_name!r} -> user_id {peer_id} ({len(matches)} total matches)",
                file=sys.stderr,
            )
        if peer_id is None and args.peer:
            peer_id = controller.resolve_peer(args.peer)
            print(f"[bale-call] resolved {args.peer} -> user_id {peer_id}", file=sys.stderr)
        if peer_id is not None:
            return controller.dial(peer_id=peer_id)
        if args.answer:
            print(
                "[bale-call] listening for incoming call. Ask the caller to ring you now. (Ctrl-C to abort.)",
                file=sys.stderr,
            )
            return controller.answer(timeout=args.answer_timeout)
        raise SystemExit("Need one of --peer-id, --peer, --peer-name, or --answer.")
    finally:
        controller._client.stop()


def _resolve_livekit_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Return (url, token) for a LiveKit room, using whichever of three
    sources the caller provided:

    1. `--livekit-url` + `--livekit-token` (explicit; still supported).
    2. `--peer-id` + `--bale-jwt`        (we place the call ourselves).
    3. `--answer` + `--bale-jwt`         (we listen for a call pushed to us).
    """
    creds = _resolve_carrier_credentials(args)
    return creds.url, creds.token


def cmd_bale_call(args: argparse.Namespace) -> int:
    """
    Run baleobala over a Bale LiveKit room.

    Three modes of obtaining the LiveKit url+token:

    1. Explicit tokens (`--livekit-url`/`--livekit-token`): for debugging
       or when you captured the creds out-of-band.
    2. Place a call (`--peer-id <user_id>` + `--bale-jwt ...`): we call
       the peer via Bale's API; server pushes us the creds.
    3. Answer a call (`--answer` + `--bale-jwt ...`): we wait for an
       incoming call; server pushes creds when a caller rings us.
    """
    url, token = _resolve_livekit_credentials(args)
    from baleobala.bale.livekit_backend import LiveKitSession
    session = LiveKitSession(url=url, token=token, identity=args.identity)
    session.start()
    try:
        if args.mode == "send":
            from baleobala.transmitter import Transmitter
            with Transmitter(
                sink=session.sink(),
                protocol=_proto(args.protocol),
                volume=args.volume,
            ) as tx:
                source = [args.text] if args.text else _stdin_lines()
                for text in source:
                    msg_id = tx.send(text)
                    print(f"[tx] id={msg_id} bytes={len(text.encode('utf-8'))}",
                          file=sys.stderr)
        else:  # recv
            from baleobala.receiver import Receiver
            with Receiver(
                source=session.source(), protocol=_proto(args.protocol),
            ) as rx:
                try:
                    for msg in rx.iter_messages():
                        print(msg.text(), flush=True)
                except KeyboardInterrupt:
                    pass
    finally:
        session.stop()
    return 0


def cmd_bale_tunnel(args: argparse.Namespace) -> int:
    """Run the full byte tunnel over Bale/LiveKit."""
    creds = _resolve_carrier_credentials(args)
    from baleobala.carrier.bale import BaleCarrierController
    from baleobala.runtime.bridge import AudioTunnelBridge
    from baleobala.runtime.frame import TunnelRole

    controller = BaleCarrierController()
    carrier = controller.open_session(creds)
    role = TunnelRole.CLIENT if args.mode == "send" else TunnelRole.SERVER
    bridge = AudioTunnelBridge(
        carrier=carrier,
        role=role,
        protocol=args.protocol,
        volume=args.volume,
    )
    with bridge:
        if args.mode == "send":
            source = [args.text] if args.text else _stdin_lines()
            for text in source:
                msg_id = bridge.send(text.encode("utf-8"))
                print(f"[tx] id={msg_id} bytes={len(text.encode('utf-8'))}", file=sys.stderr)
        else:
            try:
                while True:
                    payload = bridge.recv(timeout=0.25)
                    if payload is None:
                        continue
                    print(payload.decode("utf-8", errors="replace"), flush=True)
            except KeyboardInterrupt:
                pass
    return 0


def _open_audio_tunnel(args: argparse.Namespace, role):
    creds = _resolve_carrier_credentials(args)
    from baleobala.carrier.bale import BaleCarrierController
    from baleobala.runtime.bridge import AudioTunnelBridge

    controller = BaleCarrierController()
    carrier = controller.open_session(creds)
    bridge = AudioTunnelBridge(
        carrier=carrier,
        role=role,
        protocol=args.protocol,
        volume=args.volume,
    )
    bridge.start()
    return bridge


def _resolve_proxy_secret(args: argparse.Namespace) -> bytes | None:
    secret = getattr(args, "proxy_secret", None) or os.environ.get("BALE_PROXY_SECRET")
    if not secret:
        return None
    return secret.encode("utf-8")


def cmd_bale_proxy_client(args: argparse.Namespace) -> int:
    """Run a local SOCKS5/HTTP CONNECT server over Bale-backed transport."""
    from baleobala.runtime import QueuedTunnelTransport, Socks5ProxyServer
    from baleobala.runtime.frame import TunnelRole

    bridge = _open_audio_tunnel(args, TunnelRole.CLIENT)
    transport = QueuedTunnelTransport(bridge)
    server = Socks5ProxyServer(
        transport,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        secret=_resolve_proxy_secret(args),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()
    finally:
        transport.close()
        bridge.close()
    return 0


def cmd_bale_proxy_relay(args: argparse.Namespace) -> int:
    """Run the remote TCP relay over Bale-backed transport."""
    from baleobala.runtime import QueuedTunnelTransport, TunnelTcpRelay
    from baleobala.runtime.frame import TunnelRole

    bridge = _open_audio_tunnel(args, TunnelRole.SERVER)
    transport = QueuedTunnelTransport(bridge)
    relay = TunnelTcpRelay(transport, secret=_resolve_proxy_secret(args))
    try:
        relay.serve_forever()
    except KeyboardInterrupt:
        relay.stop()
    finally:
        transport.close()
        bridge.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="baleobala",
        description="Acoustic data bridge over voice/video calls.",
    )
    p.add_argument("-v", "--verbose", action="count", default=0)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_audio_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--device", default=None,
                        help="sounddevice name or index (default: system default)")
        sp.add_argument("--protocol", choices=["normal", "fast", "fastest"],
                        default="fast", help="GGWave audible protocol")

    s = sub.add_parser("send", help="transmit messages")
    add_audio_opts(s)
    s.add_argument("--volume", type=int, default=50, help="0-100")
    s.add_argument("--text", default=None,
                   help="single message; omit to read lines from stdin")
    s.set_defaults(func=cmd_send)

    r = sub.add_parser("recv", help="receive and print messages")
    add_audio_opts(r)
    r.set_defaults(func=cmd_recv)

    d = sub.add_parser("devices", help="list audio devices")
    d.set_defaults(func=cmd_devices)

    v = sub.add_parser("virtmic", help="create a virtual microphone and hold it open")
    v.add_argument("--name", default="baleobala")
    v.set_defaults(func=cmd_virtmic)

    lb = sub.add_parser("loopback", help="in-process self-test (no audio device)")
    lb.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    lb.add_argument("messages", nargs="*")
    lb.set_defaults(func=cmd_loopback)

    tl = sub.add_parser("tunnel-loopback", help="in-process byte-tunnel self-test")
    tl.add_argument("messages", nargs="*")
    tl.set_defaults(func=cmd_tunnel_loopback)

    bt = sub.add_parser(
        "bale-tunnel",
        help="run the byte tunnel over a Bale LiveKit room",
    )
    bt.add_argument("mode", choices=["send", "recv"])
    bt.add_argument("--livekit-url", default=None)
    bt.add_argument("--livekit-token", default=None)
    bt.add_argument("--livekit-room", default=None)
    bt.add_argument("--bale-jwt", default=None)
    bt.add_argument("--bale-jwt-file", default=None)
    bt.add_argument("--peer-id", type=int, default=None)
    bt.add_argument("--peer", default=None)
    bt.add_argument("--peer-name", default=None)
    bt.add_argument("--answer", action="store_true")
    bt.add_argument("--answer-timeout", type=float, default=120.0)
    bt.add_argument("--identity", default="baleobala")
    bt.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    bt.add_argument("--volume", type=int, default=50)
    bt.add_argument("--text", default=None)
    bt.set_defaults(func=cmd_bale_tunnel)

    bp = sub.add_parser(
        "bale-proxy",
        help="run a SOCKS5/HTTP CONNECT proxy over Bale/LiveKit",
    )
    bp_sub = bp.add_subparsers(dest="proxy_mode", required=True)

    bp_client = bp_sub.add_parser("client", help="listen locally as SOCKS5 or HTTP CONNECT")
    bp_client.add_argument("--listen-host", default="127.0.0.1")
    bp_client.add_argument("--listen-port", type=int, default=1080)
    bp_client.add_argument("--livekit-url", default=None)
    bp_client.add_argument("--livekit-token", default=None)
    bp_client.add_argument("--livekit-room", default=None)
    bp_client.add_argument("--bale-jwt", default=None)
    bp_client.add_argument("--bale-jwt-file", default=None)
    bp_client.add_argument("--peer-id", type=int, default=None)
    bp_client.add_argument("--peer", default=None)
    bp_client.add_argument("--peer-name", default=None)
    bp_client.add_argument("--answer", action="store_true")
    bp_client.add_argument("--answer-timeout", type=float, default=120.0)
    bp_client.add_argument("--identity", default="baleobala")
    bp_client.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    bp_client.add_argument("--volume", type=int, default=50)
    bp_client.add_argument("--proxy-secret", default=None, help="shared secret for packet auth/encryption")
    bp_client.set_defaults(func=cmd_bale_proxy_client)

    bp_relay = bp_sub.add_parser("relay", help="relay proxy traffic to TCP targets")
    bp_relay.add_argument("--livekit-url", default=None)
    bp_relay.add_argument("--livekit-token", default=None)
    bp_relay.add_argument("--livekit-room", default=None)
    bp_relay.add_argument("--bale-jwt", default=None)
    bp_relay.add_argument("--bale-jwt-file", default=None)
    bp_relay.add_argument("--peer-id", type=int, default=None)
    bp_relay.add_argument("--peer", default=None)
    bp_relay.add_argument("--peer-name", default=None)
    bp_relay.add_argument("--answer", action="store_true")
    bp_relay.add_argument("--answer-timeout", type=float, default=120.0)
    bp_relay.add_argument("--identity", default="baleobala")
    bp_relay.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    bp_relay.add_argument("--volume", type=int, default=50)
    bp_relay.add_argument("--proxy-secret", default=None, help="shared secret for packet auth/encryption")
    bp_relay.set_defaults(func=cmd_bale_proxy_relay)

    bc = sub.add_parser(
        "bale-call",
        help="connect to a Bale LiveKit room and send/recv baleobala frames",
    )
    bc.add_argument("mode", choices=["send", "recv"],
                    help="transmit from stdin, or receive and print")
    bc.add_argument("--livekit-url", default=None,
                    help="LiveKit WSS URL (debug/explicit mode)")
    bc.add_argument("--livekit-token", default=None,
                    help="LiveKit access token (debug/explicit mode)")
    bc.add_argument("--bale-jwt", default=None,
                    help="Bale access_token JWT for WS auth")
    bc.add_argument("--bale-jwt-file", default=None,
                    help="file to read the JWT from (default: /tmp/bale_jwt.txt)")
    bc.add_argument("--peer-id", type=int, default=None,
                    help="Bale user_id to call via StartCall RPC")
    bc.add_argument("--peer", default=None,
                    help="phone number (E.164, e.g. +989...) — "
                         "tries SearchContacts then ImportContacts. "
                         "Note: phone lookup often returns empty; "
                         "--peer-name works for contacts you know by name.")
    bc.add_argument("--peer-name", default=None,
                    help="search by name/username (SearchContacts). "
                         "Takes the first match.")
    bc.add_argument("--answer", action="store_true",
                    help="wait for an incoming call; join the pushed room")
    bc.add_argument("--answer-timeout", type=float, default=120.0,
                    help="seconds to wait in --answer mode")
    bc.add_argument("--identity", default="baleobala",
                    help="participant identity in the LiveKit room")
    bc.add_argument("--protocol", choices=["normal", "fast", "fastest"],
                    default="fast", help="GGWave audible protocol")
    bc.add_argument("--volume", type=int, default=50, help="send: 0-100")
    bc.add_argument("--text", default=None,
                    help="send: single message; omit to read lines from stdin")
    bc.set_defaults(func=cmd_bale_call)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args) or 0)
    except Exception as e:  # noqa: BLE001
        log.error("%s", e)
        if args.verbose:
            raise
        return 2


if __name__ == "__main__":
    sys.exit(main())
