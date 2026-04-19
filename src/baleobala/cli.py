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
