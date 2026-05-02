#!/usr/bin/env python3
"""Dump every plausible peer_id Bale pushes to a given JWT session.

Usage:
    python scripts/list-bale-peers.py --jwt-file ~/.baleobala/client_jwt.txt
    python scripts/list-bale-peers.py --jwt "$BALE_JWT" --seconds 12

Connects to the Bale WS gateway, fires `GhasedakService/GetDiff` to open
the push stream, then reads server pushes for `--seconds` and extracts
every varint in the user_id range (10_000 .. 5_000_000_000) found
anywhere in any frame. Prints them sorted with frequency, plus the
caller's own user_id parsed from the JWT for reference.

Useful for figuring out which `peer_id` to set in the Android app's
"Exit Peer ID" — try the most-frequent ones first, those are typically
your dialogs / recent contacts. The Bale server responds with
`CallNotApproved` for peers that don't accept calls from this account,
so you can iterate through this list to find one that picks up.
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import time
from collections import Counter

# Make the src/ layout importable when run from repo root.
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from baleobala.bale.rpc_envelope import Response  # noqa: E402
from baleobala.bale.ws_client import WsClient, WsTlsConfig  # noqa: E402


def _decode_jwt_user_id(jwt: str) -> int | None:
    try:
        payload = jwt.split(".")[1]
        body = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(body)
        return data.get("payload", {}).get("user_id")
    except Exception:
        return None


def _walk_varints(buf: bytes) -> list[int]:
    """Yield every varint we can parse from `buf`, treating it as a
    flat protobuf stream. Sub-messages (wire type 2) are recursed into.
    Best-effort — wire-format errors just stop the walk for that branch.
    """
    out: list[int] = []
    pos = 0
    while pos < len(buf):
        try:
            tag, pos = _dec_varint(buf, pos)
        except Exception:
            return out
        wt = tag & 7
        try:
            if wt == 0:
                v, pos = _dec_varint(buf, pos)
                out.append(v)
            elif wt == 2:
                length, pos = _dec_varint(buf, pos)
                inner = buf[pos:pos + length]
                pos += length
                out.extend(_walk_varints(inner))
            elif wt == 1:
                pos += 8
            elif wt == 5:
                pos += 4
            else:
                return out
        except Exception:
            return out
    return out


def _dec_varint(buf: bytes, pos: int) -> tuple[int, int]:
    n = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, pos
        shift += 7


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jwt", help="raw JWT string")
    src.add_argument("--jwt-file", help="path to a file containing the JWT")
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="how long to listen for pushes (default 10s)")
    ap.add_argument("--ws-ssl-no-verify", action="store_true",
                    help="skip TLS verification (debug only)")
    ap.add_argument("--show-frames", action="store_true",
                    help="also print raw push frame sizes as they arrive")
    args = ap.parse_args()

    jwt = args.jwt or pathlib.Path(args.jwt_file).expanduser().read_text().strip()
    self_id = _decode_jwt_user_id(jwt)
    print(f"# this JWT belongs to user_id={self_id}", file=sys.stderr)

    counts: Counter[int] = Counter()
    frame_count = 0

    def on_update(resp: Response) -> None:
        nonlocal frame_count
        frame_count += 1
        if args.show_frames:
            print(f"# rx frame #{frame_count} {len(resp.raw)}B", file=sys.stderr)
        for v in _walk_varints(resp.raw):
            if 10_000 <= v <= 5_000_000_000 and v != self_id:
                counts[v] += 1

    tls = WsTlsConfig.from_sources(
        ca_file=None, ca_path=None,
        insecure=args.ws_ssl_no_verify, allow_insecure_debug=True,
    )
    ws = WsClient(jwt=jwt, on_update=on_update, tls_config=tls)
    ws.start(timeout=15.0)
    try:
        ws.send_oneway(
            "bale.ghasedak.v1.GhasedakService", "GetDiff",
            bytes.fromhex("1203080a0c"),
        )
        print(f"# subscribed; listening {args.seconds}s for pushes…", file=sys.stderr)
        time.sleep(args.seconds)
    finally:
        ws.stop()

    print(f"# saw {frame_count} push frame(s); {len(counts)} unique candidate peer_ids",
          file=sys.stderr)
    if not counts:
        print("# nothing arrived. Either GetDiff was rejected, the JWT is invalid,",
              file=sys.stderr)
        print("# or this account simply has no contacts/dialogs.",
              file=sys.stderr)
        return 1

    print("# count\tpeer_id")
    for peer_id, c in counts.most_common():
        print(f"{c}\t{peer_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
