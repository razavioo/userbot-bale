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

from baleobala.codec import Protocol
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


def _resolve_livekit_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Return (url, token) for a LiveKit room, using whichever of three
    sources the caller provided:

    1. `--livekit-url` + `--livekit-token` (explicit; still supported).
    2. `--peer-id` + `--bale-jwt`        (we place the call ourselves).
    3. `--answer` + `--bale-jwt`         (we listen for a call pushed to us).
    """
    if args.livekit_url and args.livekit_token:
        return args.livekit_url, args.livekit_token

    jwt = args.bale_jwt
    if not jwt and args.bale_jwt_file:
        from pathlib import Path
        jwt = Path(args.bale_jwt_file).read_text().strip()
    if not jwt:
        raise SystemExit(
            "Need either --livekit-url/--livekit-token OR --bale-jwt(-file) "
            "with --peer-id/--answer."
        )

    from baleobala.bale import BaleApiClient

    client = BaleApiClient(jwt=jwt)
    client.start()
    try:
        peer_id = args.peer_id
        if peer_id is None and args.peer_name:
            matches = client.search_contacts(args.peer_name)
            if not matches:
                raise SystemExit(
                    f"no contacts match name {args.peer_name!r}"
                )
            peer_id = matches[0].user_id
            print(f"[bale-call] matched name {args.peer_name!r} -> "
                  f"user_id {peer_id} ({len(matches)} total matches)",
                  file=sys.stderr)
        if peer_id is None and args.peer:
            peer_id = client.resolve_peer(args.peer)
            print(f"[bale-call] resolved {args.peer} -> user_id {peer_id}",
                  file=sys.stderr)
        if peer_id is not None:
            creds = client.start_call(peer_id=peer_id, video=False)
        elif args.answer:
            import threading
            got = threading.Event()
            holder: list = []

            def on_creds(c):
                if not holder:
                    holder.append(c)
                    got.set()

            client.listen_incoming_calls(on_creds)
            print(
                "[bale-call] listening for incoming call. Ask the caller "
                "to ring you now. (Ctrl-C to abort.)",
                file=sys.stderr,
            )
            if not got.wait(timeout=args.answer_timeout):
                raise TimeoutError(
                    f"no incoming call within {args.answer_timeout}s"
                )
            creds = holder[0]
        else:
            raise SystemExit(
                "Need one of --peer-id, --peer, --peer-name, or --answer."
            )
    finally:
        client.stop()
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

    from baleobala.bale import LiveKitSession

    session = LiveKitSession(
        url=url, token=token, identity=args.identity,
    )
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

    ba = sub.add_parser(
        "bale-auth",
        help="phone/SMS login flow → prints a JWT. app_id+api_key must "
             "be captured from web.bale.ai (see docs/BALE_HEADLESS.md).",
    )
    ba.add_argument("--phone", required=True, help="phone in E.164, with or without '+'")
    ba.add_argument("--app-id", type=int, required=True)
    ba.add_argument("--api-key", required=True)
    ba.add_argument("--device-title", default="baleobala")
    ba.add_argument("--device-hash-hex", default=None,
                    help="hex-encoded device hash (default: random 16B)")
    ba.set_defaults(func=cmd_bale_auth)

    from baleobala.vpn.cli import add_vpn_subparser
    add_vpn_subparser(sub)

    return p


def cmd_bale_auth(args: argparse.Namespace) -> int:
    import os, secrets
    from baleobala.bale import BaleApiClient

    phone = str(args.phone).lstrip("+")
    if not phone.isdigit():
        raise SystemExit("invalid phone number (digits only, optional +)")
    device_hash = (
        bytes.fromhex(args.device_hash_hex)
        if args.device_hash_hex else secrets.token_bytes(16)
    )

    # Start WS without JWT — it will fail unless the transport
    # supports unauthenticated RPCs. Bale's auth endpoint does; others
    # don't. See BaleApiClient source.
    client = BaleApiClient(jwt="")  # type: ignore[arg-type]
    try:
        client.start()
    except Exception as e:
        raise SystemExit(
            f"could not establish unauthenticated WS: {e}. Bale may "
            f"require a bootstrap token; capture web.bale.ai's own."
        )
    try:
        tx = client.start_phone_auth(
            phone_number=int(phone),
            app_id=args.app_id,
            api_key=args.api_key,
            device_hash=device_hash,
            device_title=args.device_title,
        )
        print(f"SMS sent. transaction_hash = {tx}", file=sys.stderr)
        code = input("SMS code: ").strip()
        resp = client.validate_code(tx, code)
        print(resp.jwt)
    finally:
        client.stop()
    return 0


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
