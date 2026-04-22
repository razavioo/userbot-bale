"""Real two-account Bale smoke test helpers."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import threading
import time


def _read_jwt(path: str) -> str:
    return open(path, "r", encoding="utf-8").read().strip()


def _wait_recv(name: str, ch, timeout_s: float) -> bytes:
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{name}: receive timeout after {timeout_s:.1f}s")
        got = ch.recv_bytes(timeout=min(0.5, remaining))
        if got is not None:
            return got


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Real Bale two-account DataChannel smoke test"
    )
    ap.add_argument("--caller-jwt-file", required=True)
    ap.add_argument("--callee-jwt-file", required=True)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--callee-peer-id",
        type=int,
        help="Bale user_id of callee account (target peer for caller).",
    )
    target.add_argument(
        "--callee-peer",
        default=None,
        help="callee phone in E.164 (e.g. +98912...) to resolve via Bale.",
    )
    target.add_argument(
        "--callee-peer-name",
        default=None,
        help="callee contact display name/username to resolve via Bale.",
    )
    ap.add_argument("--topic", default="vpn")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument(
        "--ws-ssl-no-verify",
        action="store_true",
        help="disable TLS verification for Bale WS during this smoke run",
    )
    return ap


def run_live_smoke(args: argparse.Namespace) -> int:
    from baleobala.bale.api import BaleApiClient
    from baleobala.bale.livekit_backend import LiveKitSession
    from baleobala.carrier.bale import BaleCarrierController

    if getattr(args, "ws_ssl_no_verify", False):
        os.environ["BALE_WS_SSL_NO_VERIFY"] = "1"

    caller_jwt = _read_jwt(args.caller_jwt_file)
    callee_jwt = _read_jwt(args.callee_jwt_file)

    caller = BaleCarrierController(client=BaleApiClient(jwt=caller_jwt))
    callee = BaleCarrierController(client=BaleApiClient(jwt=callee_jwt))

    holder: dict[str, object] = {}
    err_holder: list[BaseException] = []
    ready = threading.Event()

    def _answer_thread() -> None:
        try:
            holder["callee_creds"] = callee.answer(timeout=args.timeout, ready_event=ready)
        except BaseException as e:  # noqa: BLE001
            err_holder.append(e)

    t = threading.Thread(target=_answer_thread, name="bale-answer-thread", daemon=True)
    t.start()
    if not ready.wait(timeout=3.0):
        print("callee listener did not become ready", file=sys.stderr)
        return 2

    callee_peer_id = args.callee_peer_id
    if callee_peer_id is None and args.callee_peer_name:
        matches = caller.search_contacts(args.callee_peer_name)
        if not matches:
            print(f"[smoke] no contacts match {args.callee_peer_name!r}", file=sys.stderr)
            return 8
        callee_peer_id = matches[0].user_id
        print(
            f"[smoke] resolved name {args.callee_peer_name!r} -> user_id {callee_peer_id}",
            flush=True,
        )
    if callee_peer_id is None and args.callee_peer:
        try:
            callee_peer_id = caller.resolve_peer(args.callee_peer)
        except Exception as e:  # noqa: BLE001
            print(f"[smoke] resolve_peer failed for {args.callee_peer}: {e}", file=sys.stderr)
            return 9
        print(
            f"[smoke] resolved phone {args.callee_peer} -> user_id {callee_peer_id}",
            flush=True,
        )
    assert callee_peer_id is not None

    print("[smoke] dialing callee via Bale...", flush=True)
    try:
        caller_creds = caller.dial(
            peer_id=callee_peer_id,
            creds_timeout=args.timeout,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] dial failed: {e}", file=sys.stderr)
        return 3

    t.join(timeout=args.timeout)
    if err_holder:
        print(f"[smoke] callee answer failed: {err_holder[0]}", file=sys.stderr)
        return 4
    if "callee_creds" not in holder:
        print("[smoke] callee did not receive credentials in time", file=sys.stderr)
        return 5

    callee_creds = holder["callee_creds"]
    print("[smoke] both sides received Bale call credentials", flush=True)

    caller_sess = LiveKitSession(
        url=caller_creds.url,
        token=caller_creds.token,
        identity="smoke-caller",
    )
    callee_sess = LiveKitSession(
        url=callee_creds.url,
        token=callee_creds.token,
        identity="smoke-callee",
    )

    caller_sess.start()
    callee_sess.start()

    try:
        ch_a = caller_sess.data_channel(topic=args.topic, reliable=True)
        ch_b = callee_sess.data_channel(topic=args.topic, reliable=True)

        time.sleep(1.0)

        nonce = os.urandom(48)
        msg_a = b"A->B:" + nonce
        msg_b = b"B->A:" + hashlib.sha256(nonce).digest()

        print(f"[smoke] send caller->callee ({len(msg_a)} bytes)", flush=True)
        ch_a.send_bytes(msg_a)
        got_b = _wait_recv("callee", ch_b, timeout_s=args.timeout)
        if got_b != msg_a:
            print("[smoke] payload mismatch on caller->callee", file=sys.stderr)
            return 6

        print(f"[smoke] send callee->caller ({len(msg_b)} bytes)", flush=True)
        ch_b.send_bytes(msg_b)
        got_a = _wait_recv("caller", ch_a, timeout_s=args.timeout)
        if got_a != msg_b:
            print("[smoke] payload mismatch on callee->caller", file=sys.stderr)
            return 7

        print("[smoke] PASS: real Bale call + real bidirectional DataChannel bytes")
        return 0
    finally:
        try:
            caller_sess.stop()
        except Exception:
            pass
        try:
            callee_sess.stop()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run_live_smoke(args)
