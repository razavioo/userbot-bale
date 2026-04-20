"""
`baleobala vpn …` subcommands.

Subcommands:
    up          Client side: bring up a TUN, place a Bale call, tunnel IP.
    exit-node   Server side: answer a Bale call, tunnel IP, (optionally)
                run the forwarding setup script.
    loopback    In-process self-test (no TUN, no call): pairs two tunnels
                over an InMemoryTransport and pings one end from the other.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


# --- argparse wiring (called by baleobala.cli.build_parser) --------------

def add_vpn_subparser(sub: "argparse._SubParsersAction") -> None:
    vpn = sub.add_parser("vpn", help="IP tunnel over a Bale call")
    vpn_sub = vpn.add_subparsers(dest="vpn_cmd", required=True)

    up = vpn_sub.add_parser("up", help="client: place a call and tunnel IP")
    _add_bale_creds_opts(up)
    _add_tun_opts(up)
    up.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc"],
                    default="auto",
                    help="carrier inside the Bale call (default: auto — "
                         "tries dc, then qr, then audio, then rpc)")
    up.add_argument("--identity", default="baleobala-vpn")
    up.add_argument("--sess-id", type=lambda s: int(s, 0), default=0x1111)
    up.add_argument("--psk", default=None,
                    help="pre-shared passphrase. Enables ChaCha20-Poly1305 "
                         "AEAD over the transport (28 B overhead per frame). "
                         "Must match the exit node's --psk.")
    up.add_argument("--psk-file", default=None,
                    help="read --psk from this file (one line).")
    up.set_defaults(func=cmd_vpn_up)

    ex = vpn_sub.add_parser("exit-node",
                            help="VPS: answer a call and forward IP to WAN")
    _add_bale_creds_opts(ex, answer_default=True)
    _add_tun_opts(ex, default_addr="10.77.0.1/24")
    ex.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc"],
                    default="auto")
    ex.add_argument("--wan", default="eth0",
                    help="public interface to MASQUERADE onto (default: eth0)")
    ex.add_argument("--skip-nat-setup", action="store_true",
                    help="don't run scripts/vpn-exit-node.sh (already done)")
    ex.add_argument("--identity", default="baleobala-exit")
    ex.add_argument("--sess-id", type=lambda s: int(s, 0), default=0x1111)
    ex.add_argument("--psk", default=None)
    ex.add_argument("--psk-file", default=None)
    ex.set_defaults(func=cmd_vpn_exit_node)

    mx = vpn_sub.add_parser(
        "exit-node-mesh",
        help="VPS: serve many clients concurrently, each on its own /30 tunnel",
    )
    _add_bale_creds_opts(mx, answer_default=True)
    _add_tun_opts(mx, default_addr="10.77.0.1/16")
    mx.add_argument("--wan", default="eth0")
    mx.add_argument("--skip-nat-setup", action="store_true")
    mx.add_argument("--identity-prefix", default="baleobala-mesh")
    mx.add_argument("--pool-cidr", default="10.77.0.0/16",
                    help="IP pool for per-client /30 allocations")
    mx.add_argument("--psk", default=None)
    mx.add_argument("--psk-file", default=None)
    mx.set_defaults(func=cmd_vpn_exit_node_mesh)

    lb = vpn_sub.add_parser("loopback",
                            help="in-process tunnel self-test (no TUN, no call)")
    lb.add_argument("--packets", type=int, default=20)
    lb.add_argument("--size", type=int, default=1200)
    lb.add_argument("--loss", type=float, default=0.0,
                    help="simulated per-frame drop probability 0..1")
    lb.set_defaults(func=cmd_vpn_loopback)


def _add_bale_creds_opts(sp: argparse.ArgumentParser, *, answer_default: bool = False) -> None:
    sp.add_argument("--livekit-url", default=None)
    sp.add_argument("--livekit-token", default=None)
    sp.add_argument("--bale-jwt", default=None)
    sp.add_argument("--bale-jwt-file", default=None)
    sp.add_argument("--peer-id", type=int, default=None)
    sp.add_argument("--peer", default=None,
                    help="phone number in E.164")
    sp.add_argument("--peer-name", default=None,
                    help="contact display name / username")
    sp.add_argument("--answer", action="store_true", default=answer_default,
                    help="wait for an incoming call and join it")
    sp.add_argument("--answer-timeout", type=float, default=300.0)


def _add_tun_opts(sp: argparse.ArgumentParser, *, default_addr: str = "10.77.0.2/24") -> None:
    sp.add_argument("--tun", default="vpn0",
                    help="TUN interface name (must already exist; see "
                         "scripts/vpn-setup-tun.sh)")
    sp.add_argument("--tun-addr", default=default_addr,
                    help="for diagnostics / setup hint only")
    sp.add_argument("--tun-mtu", type=int, default=1400,
                    help="for diagnostics / setup hint only")


# --- commands -------------------------------------------------------------

def cmd_vpn_up(args: argparse.Namespace) -> int:
    return _run_tunnel_session(args, is_exit_node=False)


def cmd_vpn_exit_node(args: argparse.Namespace) -> int:
    if not args.skip_nat_setup:
        _run_nat_setup(args.tun, args.wan)
    return _run_tunnel_session(args, is_exit_node=True)


def cmd_vpn_exit_node_mesh(args: argparse.Namespace) -> int:
    """Multi-client exit node. Accepts N concurrent Bale calls, each
    mapped to its own /30 slot inside --pool-cidr. Requires a reachable
    TUN device (see scripts/vpn-setup-tun.sh) and NAT setup (vpn-exit-node.sh).

    IP negotiation: clients must self-assign a matching /30 address on
    their side. A future control-channel will push the assignment down
    automatically; today it's manual (CIDR is deterministic per peer_id,
    so a client can compute its own address from the pool formula)."""
    import threading
    from baleobala.bale import BaleApiClient, LiveKitSession

    from .crypto import EncryptedTransport
    from .keepalive import LiveKitKeepalive
    from .mesh.exit_node import MeshExitNode
    from .tun import TunDevice
    from .transports.datachannel_transport import DataChannelTransport

    if not args.skip_nat_setup:
        _run_nat_setup(args.tun, args.wan)

    try:
        tun = TunDevice.open(args.tun)
    except (PermissionError, RuntimeError, OSError) as e:
        print(f"[vpn-mesh] cannot open TUN {args.tun}: {e}", file=sys.stderr)
        return 3

    psk_key = _resolve_psk(args)
    mesh = MeshExitNode(tun, pool_cidr=args.pool_cidr)
    mesh.start()

    jwt = args.bale_jwt
    if not jwt and args.bale_jwt_file:
        jwt = Path(args.bale_jwt_file).read_text().strip()
    if not jwt:
        raise SystemExit("--bale-jwt(-file) required in mesh mode")

    from .jwt_util import warn_if_near_expiry
    warn_if_near_expiry(jwt)

    bale = BaleApiClient(jwt=jwt)
    bale.start()
    sessions: list = []  # keep refs so GC doesn't tear them down

    def on_incoming_call(creds):  # type: ignore[no-untyped-def]
        # We don't yet know the peer_id from the LiveKit creds alone;
        # Bale's push includes the room name + caller identity. Extract
        # whatever we can; for now use a deterministic slot from the
        # room name's hash until full creds parsing lands.
        import hashlib
        peer_id = int.from_bytes(
            hashlib.blake2b((creds.room or creds.url).encode(),
                            digest_size=4).digest(),
            "big",
        )
        print(f"[vpn-mesh] incoming call → peer_slot={peer_id}", file=sys.stderr)

        try:
            session = LiveKitSession(
                url=creds.url, token=creds.token,
                identity=f"{args.identity_prefix}-{peer_id}",
            )
            session.start()
            LiveKitKeepalive(session, interval=20.0).start()
            dc = DataChannelTransport(session, topic="vpn", reliable=True)
            transport = EncryptedTransport(dc, psk_key) if psk_key else dc
            assignment = mesh.accept_client(
                peer_id, transport, mtu_override=transport.mtu,
            )
            print(
                f"[vpn-mesh] peer={peer_id} → assigned "
                f"{assignment.client} (gateway={assignment.gateway})",
                file=sys.stderr,
            )
            sessions.append((peer_id, session, transport))
        except Exception:  # noqa: BLE001
            log.exception("failed to bring up client tunnel")

    bale.listen_incoming_calls(on_incoming_call)
    print(f"[vpn-mesh] up — tun={args.tun} pool={args.pool_cidr}. "
          f"Waiting for incoming Bale calls. Ctrl-C to stop.",
          file=sys.stderr)

    from .runner import wait_for_signal
    try:
        wait_for_signal()
    finally:
        print(f"[vpn-mesh] shutting down ({len(sessions)} active clients)",
              file=sys.stderr)
        for peer_id, sess, _tx in sessions:
            try:
                mesh.drop_client(peer_id)
            except Exception:
                pass
            try:
                sess.stop()
            except Exception:
                pass
        mesh.stop()
        bale.stop()
        tun.close()
    return 0


def cmd_vpn_loopback(args: argparse.Namespace) -> int:
    """Drive two tunnels paired over an InMemoryTransport, no TUN, no call."""
    import queue
    import time

    from .transports import InMemoryTransport
    from .tunnel import Tunnel

    a_tx, b_tx = InMemoryTransport.pair(mtu=1500, loss=args.loss, rng_seed=1)
    a = Tunnel(a_tx, sess_id=0xBEEF, ack_timeout=0.1,
               max_retries=40 if args.loss > 0 else 8, window=16)
    b = Tunnel(b_tx, sess_id=0xBEEF, ack_timeout=0.1,
               max_retries=40 if args.loss > 0 else 8, window=16)
    rx: "queue.Queue[bytes]" = queue.Queue()
    try:
        b.start(on_packet=rx.put)
        a.start(on_packet=lambda _: None)

        payload = bytes((i & 0xFF) for i in range(args.size))
        t0 = time.monotonic()
        for _ in range(args.packets):
            a.send_packet(payload)
        got = [rx.get(timeout=10) for _ in range(args.packets)]
        dt = time.monotonic() - t0

        assert all(g == payload for g in got), "payload mismatch"
        mb = args.packets * args.size / (1024 * 1024)
        print(f"[vpn loopback] OK: {args.packets} pkts × {args.size} B "
              f"= {mb:.2f} MiB in {dt*1000:.1f} ms "
              f"({mb/dt:.2f} MiB/s, loss={args.loss})")
        return 0
    finally:
        a.stop()
        b.stop()
        a_tx.close()
        b_tx.close()


# --- shared session plumbing ---------------------------------------------

def _run_nat_setup(tun: str, wan: str) -> None:
    script = _repo_root() / "scripts" / "vpn-exit-node.sh"
    if not script.exists():
        print(f"[vpn] missing {script}; skipping NAT setup", file=sys.stderr)
        return
    sudo = shutil.which("sudo")
    cmd = [sudo, str(script), tun, wan] if (sudo and os.geteuid() != 0) else [str(script), tun, wan]
    print(f"[vpn] running NAT setup: {' '.join(cmd)}", file=sys.stderr)
    subprocess.check_call(cmd)


def _run_tunnel_session(args: argparse.Namespace, *, is_exit_node: bool) -> int:
    from baleobala.cli import _resolve_livekit_credentials  # reuse existing

    from .runner import RunnerConfig, VpnRunner, prompt_tun_setup_hint, wait_for_signal
    from .tun import TunDevice

    # 1. Open the TUN device (fail fast with a setup hint).
    try:
        tun = TunDevice.open(args.tun)
    except (PermissionError, RuntimeError, OSError) as e:
        print(f"[vpn] cannot open TUN {args.tun}: {e}", file=sys.stderr)
        prompt_tun_setup_hint(args.tun, args.tun_addr, args.tun_mtu)
        return 3

    # 2. JWT expiry warning (best-effort; no-op if creds were passed
    #    via --livekit-url/--livekit-token instead of a Bale JWT).
    from .jwt_util import warn_if_near_expiry
    jwt = args.bale_jwt
    if not jwt and args.bale_jwt_file:
        try:
            jwt = Path(args.bale_jwt_file).read_text().strip()
        except OSError:
            jwt = None
    if jwt:
        warn_if_near_expiry(jwt)

    # 3. Resolve LiveKit creds via Bale (call or answer).
    url, token = _resolve_livekit_credentials(args)

    # 4. Build session + transport + keepalive + health monitor.
    from baleobala.bale import LiveKitSession

    from .keepalive import LiveKitKeepalive

    session = LiveKitSession(url=url, token=token, identity=args.identity)
    session.start()

    keepalive = LiveKitKeepalive(session, interval=20.0)
    keepalive.start()

    try:
        chain = _build_transport_chain(args.transport, session, args)
        transport_name, transport = chain.start()
        # MTU floor = min across all candidates so pending frames
        # survive a hot-swap. Audio's 128 B dominates when it's in the
        # chain; DC-only runs use ~14 KiB.
        mtu_floor = min(_safe_mtu(c.factory, precomputed=transport if c.name == transport_name else None)
                        for c in chain._choices)  # type: ignore[attr-defined]

        cfg = RunnerConfig(
            sess_id=args.sess_id,
            # DC is reliable + ordered; we mostly don't need ARQ, but keep a
            # safety-net retransmit with a long timeout. Audio path needs
            # aggressive ARQ because GGWave loses whole frames under Opus NS.
            ack_timeout=2.0 if transport_name == "dc" else 0.5,
            window=64 if transport_name == "dc" else 1,
            max_retries=3 if transport_name == "dc" else 16,
            mtu_override=mtu_floor,
        )
        runner = VpnRunner(tun, transport, cfg)
        runner.start()

        from .router import HealthMonitor
        health = HealthMonitor(runner._tunnel, interval=5.0, stuck_threshold=8)
        health.start()

        # SIGUSR1 = manual swap to next transport in chain.
        _install_swap_handler(runner, chain)

        role = "exit-node" if is_exit_node else "client"
        print(f"[vpn] up ({role}, transport={transport_name}, "
              f"tun={args.tun}, sess=0x{args.sess_id:x}, "
              f"mtu_floor={mtu_floor}). "
              f"Ctrl-C to stop; kill -USR1 {os.getpid()} to swap transport.",
              file=sys.stderr)
        try:
            wait_for_signal()
        finally:
            health.stop()
            runner.stop()
    finally:
        keepalive.stop()
        try:
            chain.close()
        except Exception:  # noqa: BLE001
            pass
        session.stop()
        tun.close()
    return 0


def _resolve_psk(args) -> bytes | None:  # type: ignore[no-untyped-def]
    """Return a 32-byte AEAD key derived from --psk / --psk-file, or None."""
    from .crypto import derive_key
    psk = args.psk
    if not psk and args.psk_file:
        psk = Path(args.psk_file).read_text().strip()
    if not psk:
        return None
    return derive_key(psk)


def _safe_mtu(factory, *, precomputed=None) -> int:  # type: ignore[no-untyped-def]
    """Return transport.mtu without side effects if possible.

    If the factory has already been called (precomputed), use that. We
    can't probe build-cost factories (like rpc which opens a WS) just
    to read MTU; for those we fall back to known-safe defaults by
    factory name, assuming caller tagged them."""
    if precomputed is not None:
        return getattr(precomputed, "mtu", 128)
    name = getattr(factory, "_mtu_hint", None)
    if isinstance(name, int):
        return name
    # Last resort: assume the smallest (audio). Better to under-fragment
    # than to produce oversized frames we can't retransmit.
    return 128


def _install_swap_handler(runner, chain) -> None:  # type: ignore[no-untyped-def]
    import signal

    def handler(signum, frame):  # type: ignore[no-untyped-def]
        try:
            name, new_tx = chain.advance()
        except RuntimeError as e:
            print(f"[vpn] swap failed: {e}", file=sys.stderr)
            return
        old = runner._tunnel.swap_transport(new_tx)  # type: ignore[attr-defined]
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        print(f"[vpn] swapped to transport {name}", file=sys.stderr)

    try:
        signal.signal(signal.SIGUSR1, handler)
    except (ValueError, OSError):
        pass  # e.g. on Windows or non-main thread


def _build_transport_chain(name: str, session, args):  # type: ignore[no-untyped-def]
    """Build a TransportChain ordered per user selection.

    Single-name selections produce a one-element chain (still hot-swap
    capable in the degenerate sense). `auto` produces dc → qr → audio → rpc.
    When --psk / --psk-file is set, each transport is wrapped with
    EncryptedTransport (ChaCha20-Poly1305)."""

    from .crypto import EncryptedTransport, OVERHEAD, derive_key

    psk_key = _resolve_psk(args)
    def maybe_encrypt(inner):  # type: ignore[no-untyped-def]
        if psk_key is None:
            return inner
        return EncryptedTransport(inner, psk_key)

    def dc():
        from .transports.datachannel_transport import DataChannelTransport
        return maybe_encrypt(DataChannelTransport(session, topic="vpn", reliable=True))
    dc._mtu_hint = 14 * 1024 - (OVERHEAD if psk_key else 0)  # type: ignore[attr-defined]

    def audio():
        from .transports.audio_transport import AudioTransport
        return maybe_encrypt(AudioTransport(session))
    audio._mtu_hint = 128 - (OVERHEAD if psk_key else 0)  # type: ignore[attr-defined]

    def qr():
        from .transports.video_qr_transport import VideoQrTransport
        return maybe_encrypt(VideoQrTransport(session))
    qr._mtu_hint = 240 - (OVERHEAD if psk_key else 0)  # type: ignore[attr-defined]

    def rpc():
        from baleobala.bale import BaleApiClient
        from .transports.rpc_transport import RpcTransport
        if args.peer_id is None:
            raise RuntimeError("rpc transport requires --peer-id")
        jwt = args.bale_jwt
        if not jwt and args.bale_jwt_file:
            jwt = Path(args.bale_jwt_file).read_text().strip()
        if not jwt:
            raise RuntimeError("rpc transport requires --bale-jwt(-file)")
        client = BaleApiClient(jwt=jwt)
        client.start()
        # The client lifetime is now tied to the transport; close() will
        # stop it. Worth noting: the same account can't run SearchContacts
        # + SendMessage + LiveKit simultaneously on multiple WS connections
        # in some Bale accounts, so we open a dedicated WS here.
        t = RpcTransport(client, peer_id=args.peer_id)
        _orig_close = t.close

        def close_with_client():  # type: ignore[no-untyped-def]
            try:
                _orig_close()
            finally:
                client.stop()

        t.close = close_with_client  # type: ignore[method-assign]
        return maybe_encrypt(t)
    rpc._mtu_hint = 3 * 1024 - (OVERHEAD if psk_key else 0)  # type: ignore[attr-defined]

    from .router import RouterChoice, TransportChain
    named = {"dc": dc, "qr": qr, "audio": audio, "rpc": rpc}
    if name in named:
        return TransportChain([RouterChoice(name, named[name])])
    if name == "auto":
        return TransportChain([
            RouterChoice("dc", dc),
            RouterChoice("qr", qr),
            RouterChoice("audio", audio),
            RouterChoice("rpc", rpc),
        ])
    raise ValueError(f"unknown transport: {name}")


def _repo_root() -> Path:
    # src/baleobala/vpn/cli.py → repo root is 4 parents up
    return Path(__file__).resolve().parents[3]
