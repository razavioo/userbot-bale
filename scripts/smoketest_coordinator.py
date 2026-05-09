#!/usr/bin/env python3
"""Coordinator live smoke test.

Brings up a coordinator + 2 relays in-process using live Bale JWTs,
sends a HELLO from the client, asserts ASSIGN is received, then
verifies the relay callback arrives and transfers --payload-bytes
through the established DataChannel tunnel.

Exit 0 on success; non-zero on any failure or timeout.
Writes a JSON result to --out for CI artifact upload.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--coordinator-jwt", required=True)
    p.add_argument("--relay1-jwt", required=True)
    p.add_argument("--relay2-jwt", required=True)
    p.add_argument("--client-jwt", required=True)
    p.add_argument("--coordinator-peer-id", type=int, required=True)
    p.add_argument("--relay1-peer-id", type=int, required=True)
    p.add_argument("--relay2-peer-id", type=int, required=True)
    p.add_argument("--client-peer-id", type=int, required=True)
    p.add_argument("--payload-bytes", type=int, default=1_048_576)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--out", default="-")
    return p.parse_args()


def _write_jwt(path: Path, jwt: str) -> None:
    path.write_text(jwt.strip())


def _start_coordinator(args: argparse.Namespace, tmp: Path) -> subprocess.Popen:
    jwt_file = tmp / "coordinator.jwt"
    _write_jwt(jwt_file, args.coordinator_jwt)
    snap = tmp / "coordinator-state.json"
    return subprocess.Popen(
        [
            sys.executable, "-m", "baleobala.cli",
            "coordinator", "serve",
            "--jwt", str(jwt_file),
            "--snapshot", str(snap),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _start_relay(name: str, jwt: str, peer_id: int,
                 coordinator_peer_id: int, tmp: Path) -> subprocess.Popen:
    jwt_file = tmp / f"{name}.jwt"
    _write_jwt(jwt_file, jwt)
    psk_file = tmp / "smoke.psk"
    if not psk_file.exists():
        psk_file.write_bytes(os.urandom(32))
    return subprocess.Popen(
        [
            sys.executable, "-m", "baleobala.cli",
            "vpn", "mesh",
            "--jwt", str(jwt_file),
            "--relay-peer-id", str(peer_id),
            "--coordinator-peer-id", str(coordinator_peer_id),
            "--psk", str(psk_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _stream_output(proc: subprocess.Popen, label: str) -> None:
    for line in proc.stdout:
        print(f"[{label}] {line.decode(errors='replace').rstrip()}", flush=True)


def main() -> int:
    args = _parse_args()
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="baleobala-smoke-"))

    result: dict = {
        "ok": False,
        "error": None,
        "elapsed_secs": None,
        "payload_bytes": args.payload_bytes,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    t0 = time.monotonic()

    procs: list[subprocess.Popen] = []
    try:
        coord_proc = _start_coordinator(args, tmp)
        procs.append(coord_proc)
        threading.Thread(target=_stream_output, args=(coord_proc, "coordinator"), daemon=True).start()

        # Give coordinator time to start accepting calls.
        time.sleep(5)

        r1 = _start_relay("relay1", args.relay1_jwt, args.relay1_peer_id, args.coordinator_peer_id, tmp)
        procs.append(r1)
        threading.Thread(target=_stream_output, args=(r1, "relay1"), daemon=True).start()

        r2 = _start_relay("relay2", args.relay2_jwt, args.relay2_peer_id, args.coordinator_peer_id, tmp)
        procs.append(r2)
        threading.Thread(target=_stream_output, args=(r2, "relay2"), daemon=True).start()

        # Give relays time to send ONLINE to coordinator.
        time.sleep(8)

        # Run the client-side coordinator flow synchronously.
        jwt_file = tmp / "client.jwt"
        _write_jwt(jwt_file, args.client_jwt)
        psk_file = tmp / "smoke.psk"

        client_cmd = [
            sys.executable, "-m", "baleobala.cli",
            "vpn", "mesh-client",
            "--jwt", str(jwt_file),
            "--coordinator-peer-id", str(args.coordinator_peer_id),
            "--client-peer-id", str(args.client_peer_id),
            "--psk", str(psk_file),
            "--smoke-bytes", str(args.payload_bytes),
            "--smoke-only",
        ]
        deadline = args.timeout - (time.monotonic() - t0)
        if deadline <= 0:
            raise TimeoutError("timed out before client run")

        client_result = subprocess.run(
            client_cmd,
            timeout=deadline,
            capture_output=True,
            text=True,
        )
        print(client_result.stdout)
        print(client_result.stderr, file=sys.stderr)

        if client_result.returncode != 0:
            raise RuntimeError(f"client exited {client_result.returncode}")

        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()

    result["elapsed_secs"] = round(time.monotonic() - t0, 2)

    out_text = json.dumps(result, indent=2)
    if args.out == "-":
        print(out_text)
    else:
        Path(args.out).write_text(out_text)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
