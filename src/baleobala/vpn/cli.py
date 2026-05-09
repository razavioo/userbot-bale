"""
`baleobala tunnel …` subcommands.

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

from .constants import DEFAULT_TUNNEL_SESS_ID
from .relay_handler import RelayCallHandler
from .relay_state import DEFAULT_STALE_ACTIVE_FLAG_SECS, RelayState
from .session_reaper import SessionReaper

log = logging.getLogger(__name__)


# --- argparse wiring (called by baleobala.cli.build_parser) --------------

def add_tunnel_subparser(sub: "argparse._SubParsersAction") -> None:
    tunnel = sub.add_parser("tunnel", help="IP tunnel over a Bale call")
    vpn_sub = tunnel.add_subparsers(dest="vpn_cmd", required=True)

    up = vpn_sub.add_parser("up", help="client: place a call and tunnel IP")
    _add_bale_creds_opts(up)
    _add_tun_opts(up)
    up.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc", "mtproto_rpc"],
                    default="auto",
                    help="carrier inside the Bale call (default: auto — "
                         "tries dc, then qr, then audio, then rpc, then mtproto_rpc)")
    up.add_argument("--identity", default="baleobala-vpn")
    up.add_argument("--sess-id", type=lambda s: int(s, 0), default=DEFAULT_TUNNEL_SESS_ID)
    up.add_argument("--psk", default=None,
                    help="pre-shared passphrase. Enables ChaCha20-Poly1305 "
                         "AEAD over the transport (28 B overhead per frame). "
                         "Must match the exit node's --psk.")
    up.add_argument("--psk-file", default=None,
                    help="read --psk from this file (one line).")
    up.add_argument("--provision-timeout", type=float, default=10.0,
                    help="seconds to wait for mesh provisioning before "
                         "falling back to legacy tunnel startup")
    up.set_defaults(func=cmd_vpn_up)

    ex = vpn_sub.add_parser("exit-node",
                            help="VPS: answer a call and forward IP to WAN")
    _add_bale_creds_opts(ex, answer_default=True)
    _add_tun_opts(ex, default_addr="10.77.0.1/24")
    ex.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc", "mtproto_rpc"],
                    default="auto")
    ex.add_argument("--wan", default="eth0",
                    help="public interface to MASQUERADE onto (default: eth0)")
    ex.add_argument("--skip-nat-setup", action="store_true",
                    help="don't run scripts/vpn-exit-node.sh (already done)")
    ex.add_argument("--identity", default="baleobala-exit")
    ex.add_argument("--sess-id", type=lambda s: int(s, 0), default=DEFAULT_TUNNEL_SESS_ID)
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
    mx.add_argument("--max-peers-per-server-jwt", type=int, default=4,
                    help="maximum active peers allowed to share one server JWT")
    mx.add_argument("--psk", default=None)
    mx.add_argument("--psk-file", default=None)
    mx.add_argument("--provision-timeout", type=float, default=10.0)
    mx.add_argument("--coordinator-peer-id", type=int, default=None,
                    help="Bale user_id of the coordinator account. When set, the relay "
                         "registers itself and only accepts calls from approved clients.")
    mx.add_argument("--relay-peer-id", action="append", type=int, default=None,
                    dest="relay_peer_ids",
                    help="Bale user_id for each --bale-jwt-file in order "
                         "(required when --coordinator-peer-id is set). Repeat once per JWT.")
    mx.add_argument("--heartbeat-interval", type=float, default=60.0,
                    help="seconds between HEARTBEAT messages to coordinator (default: 60)")
    mx.add_argument("--standalone", action="store_true",
                    help="skip coordinator registration; accept any inbound call (legacy mode)")
    mx.add_argument("--metrics-port", type=int, default=9201,
                    help="bind a Prometheus /metrics + /healthz endpoint on "
                         "127.0.0.1:<port>; pass 0 to disable (default: 9201)")
    mx.set_defaults(func=cmd_vpn_exit_node_mesh)

    co = vpn_sub.add_parser(
        "coordinator",
        help="central rendezvous: route incoming clients to free relay slots",
    )
    co.add_argument("--listen-jwt", default=None,
                    help="Bale JWT clients call (the public coordinator number)")
    co.add_argument("--listen-jwt-file", default=None,
                    help="file containing the listen JWT")
    co.add_argument("--dispatch-jwt", default=None,
                    help="Bale JWT used for outbound calls to relays "
                         "(default: same as listen-jwt; warns about race)")
    co.add_argument("--dispatch-jwt-file", default=None,
                    help="file containing the dispatch JWT")
    co.add_argument("--snapshot-path", default=None,
                    help="path to JSON state snapshot (default: "
                         "<config_dir>/coordinator-state.json)")
    co.add_argument("--identity-prefix", default="coordinator")
    co.add_argument("--prune-interval", type=float, default=30.0,
                    help="seconds between stale-relay pruning passes")
    co.add_argument("--stale-timeout", type=float, default=90.0,
                    help="seconds since last heartbeat before a relay is dropped")
    co.add_argument("--metrics-port", type=int, default=9202,
                    help="bind a Prometheus /metrics + /healthz endpoint on "
                         "127.0.0.1:<port>; pass 0 to disable (default: 9202)")
    co.set_defaults(func=cmd_vpn_coordinator)

    lb = vpn_sub.add_parser("loopback",
                            help="in-process tunnel self-test (no TUN, no call)")
    lb.add_argument("--packets", type=int, default=20)
    lb.add_argument("--size", type=int, default=1200)
    lb.add_argument("--loss", type=float, default=0.0,
                    help="simulated per-frame drop probability 0..1")
    lb.set_defaults(func=cmd_vpn_loopback)


# Backwards compatibility for older imports.
add_vpn_subparser = add_tunnel_subparser


def _add_bale_creds_opts(sp: argparse.ArgumentParser, *, answer_default: bool = False) -> None:
    sp.add_argument("--livekit-url", default=None)
    sp.add_argument("--livekit-token", default=None)
    sp.add_argument("--bale-jwt", action="append", default=None,
                    help="Bale access_token JWT; repeat in mesh mode for an account pool")
    sp.add_argument("--bale-jwt-file", action="append", default=None,
                    help="file to read a Bale JWT from; repeat in mesh mode for an account pool")
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

    With --coordinator-peer-id the relay registers itself and only accepts
    calls from clients pre-approved by the coordinator (reverse-call flow).
    Use --standalone to skip coordinator and accept any inbound call."""
    import threading
    import time
    from baleobala.bale import BaleApiClient, LiveKitSession
    from baleobala.control.service import ControlService

    from .crypto import EncryptedTransport
    from .keepalive import LiveKitKeepalive
    from .mesh.capacity import ServerJwtAllocator
    from .mesh.exit_node import MeshExitNode
    from .provisioning import MeshProvisionMessage, ProvisioningError, recv_mesh_message
    from .tun import TunDevice
    from .transports.datachannel_transport import DataChannelTransport

    coordinator_peer_id: int | None = args.coordinator_peer_id
    relay_peer_ids: list[int] = list(args.relay_peer_ids or [])
    use_coordinator = coordinator_peer_id is not None and not args.standalone

    if not args.skip_nat_setup:
        _run_nat_setup(args.tun, args.wan)

    try:
        tun = TunDevice.open(args.tun)
    except (PermissionError, RuntimeError, OSError) as e:
        log.error("vpn-mesh: cannot open TUN %s: %s", args.tun, e)
        return 3

    psk_key = _resolve_psk(args)
    mesh = MeshExitNode(tun, pool_cidr=args.pool_cidr)
    mesh.start()
    control = ControlService()

    # Metrics server is bound here but the relay-specific /healthz hook
    # is registered later (after `relay_state` and `sessions` are
    # defined) so the closure has stable references rather than relying
    # on Python's late-binding of free variables.
    metrics_server = _maybe_start_metrics_server(
        getattr(args, "metrics_port", 0), tag="vpn-mesh",
    )

    jwts = _resolve_bale_jwts(args)
    if not jwts:
        raise SystemExit("--bale-jwt(-file) required in mesh mode")

    if use_coordinator and relay_peer_ids and len(relay_peer_ids) != len(jwts):
        raise SystemExit(
            f"--relay-peer-id count ({len(relay_peer_ids)}) must match "
            f"--bale-jwt-file count ({len(jwts)})"
        )

    from .jwt_util import warn_if_near_expiry
    for jwt in jwts:
        warn_if_near_expiry(jwt)

    bale_clients: list[BaleApiClient] = []
    sessions: list = []  # keep refs so GC doesn't tear them down
    allocator = ServerJwtAllocator(
        server_count=len(jwts),
        max_peers_per_server=args.max_peers_per_server_jwt,
    )

    # Coordinator integration state (populated below if use_coordinator)
    reporters: list = []
    from baleobala.coordinator.relay_client import ExpectedClientSet, handle_coordinator_instruction
    expected_clients = ExpectedClientSet()
    # Maps session_id → peer_id for RELEASED reporting
    session_map: dict[str, int] = {}
    session_map_lock = threading.Lock()
    # The probe lock used to be global, then per-account, but both
    # caused legitimate dispatch calls to time out behind slow probes.
    # We now rely on the audio-skip / disconnect-grace fixes plus the
    # _account_active flag to keep the Rust runtime stable; concurrent
    # probes are tolerated. Keep a no-op context manager so existing
    # `with _probe_locks[idx]:` syntax still works.
    class _NoLock:
        def __enter__(self): return None
        def __exit__(self, *a): return None
    _probe_locks: dict[int, "_NoLock"] = {i: _NoLock() for i in range(len(jwts))}
    # Track per-account active VPN sessions so a duplicate Bale push (the
    # SFU sometimes redelivers a call notification after AcceptCall) does
    # not start a second probe that overwrites the live tunnel via
    # mesh.issue_client. See `RelayState` in relay_state.py for the full
    # rationale, including the stale-flag auto-recovery.
    relay_state = RelayState(num_accounts=len(jwts))
    STALE_ACTIVE_FLAG_SECS = DEFAULT_STALE_ACTIVE_FLAG_SECS

    if metrics_server is not None:
        from baleobala.runtime.metrics import add_health_hook

        def _relay_health() -> dict:
            return {
                "active_sessions": len(sessions),
                "stale_active_flags": [
                    idx for idx in range(len(jwts))
                    if relay_state.is_stale(idx, threshold=STALE_ACTIVE_FLAG_SECS)
                ],
                "active_flag_ages": {
                    str(idx): round(relay_state.age(idx), 2)
                    for idx in range(len(jwts))
                    if relay_state.is_active(idx)
                },
            }
        add_health_hook(_relay_health)

    relay_handler = RelayCallHandler(
        use_coordinator=use_coordinator,
        identity_prefix=args.identity_prefix,
        pool_cidr=args.pool_cidr,
        tun_mtu=args.tun_mtu,
        provision_timeout=args.provision_timeout,
        psk_key=psk_key,
        mesh=mesh,
        allocator=allocator,
        control=control,
        expected_clients=expected_clients,
        session_map=session_map,
        session_map_lock=session_map_lock,
        probe_locks=_probe_locks,
        relay_state=relay_state,
        stale_active_flag_secs=STALE_ACTIVE_FLAG_SECS,
        sessions=sessions,
        reporters=reporters,
        livekit_session_factory=LiveKitSession,
        keepalive_factory=LiveKitKeepalive,
        datachannel_transport_factory=DataChannelTransport,
        encrypted_transport_factory=EncryptedTransport,
        provision_message_factory=MeshProvisionMessage,
        recv_provision=recv_mesh_message,
        provisioning_error_cls=ProvisioningError,
    )

    def on_incoming_call(event, account_index: int = 0):  # type: ignore[no-untyped-def]
        relay_handler.handle_call(event, account_index)


    for account_index, jwt in enumerate(jwts):
        bale = BaleApiClient(jwt=jwt)
        bale.start()
        bale.listen_incoming_calls(
            lambda event, idx=account_index: on_incoming_call(event, idx)
        )
        bale_clients.append(bale)

        if use_coordinator and relay_peer_ids:
            from baleobala.coordinator.relay_client import CoordinatorReporter
            import socket as _socket
            hostname = _socket.gethostname()
            relay_id = f"{hostname}-{account_index}"
            reporter = CoordinatorReporter(
                coordinator_peer_id=coordinator_peer_id,
                relay_id=relay_id,
                relay_peer_id=relay_peer_ids[account_index],
                bale_client=bale,
                identity_prefix=f"{args.identity_prefix}-reporter",
            )
            reporter.start()
            reporter.report_online(capacity=args.max_peers_per_server_jwt)
            reporter.start_heartbeat(
                interval=args.heartbeat_interval,
                get_in_use=lambda idx=account_index: [
                    p for s in allocator.slots() if s.index == idx
                    for p in s.peer_ids
                ],
                capacity=args.max_peers_per_server_jwt,
            )
            reporters.append(reporter)

    mode_tag = "coordinator" if use_coordinator else "standalone"
    log.info(
        "vpn-mesh: up mode=%s tun=%s pool=%s accounts=%d max_peers=%d capacity=%d",
        mode_tag, args.tun, args.pool_cidr, len(bale_clients),
        args.max_peers_per_server_jwt, allocator.total_capacity,
    )

    # Reaper: when a VPN session's underlying LiveKit room ends
    # (client disconnects, network gone, room.disconnected fires), the
    # main loop never noticed and left the session in `sessions`, the
    # allocator slot reserved, and `_account_active[idx]` set. See
    # `SessionReaper` in session_reaper.py for the full rationale,
    # including the one-per-cycle invariant (commit 9fea7eb) and the
    # FAILED-state skip (also 9fea7eb).
    _reaper = SessionReaper(
        sessions=sessions,
        mesh=mesh,
        control=control,
        allocator=allocator,
    )
    _reaper.start()

    from .runner import wait_for_signal
    try:
        wait_for_signal()
    finally:
        _reaper.stop()
        log.info("vpn-mesh: shutting down active_clients=%d", len(sessions))
        for reporter in reporters:
            try:
                reporter.report_offline()
                reporter.stop()
            except Exception:  # noqa: BLE001
                pass
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
        for bale in bale_clients:
            bale.stop()
        tun.close()
        if metrics_server is not None:
            try:
                metrics_server.shutdown()
            except Exception:  # noqa: BLE001
                pass
    return 0


def _maybe_start_metrics_server(port: int, *, tag: str):
    """Bring up the Prometheus /metrics + /healthz HTTP server on
    127.0.0.1:<port>. Returns the server (so the caller can shutdown())
    or None if the port is 0 / disabled / fails to bind. A bind failure
    is logged but never fatal — the relay must keep running."""
    if not port:
        return None
    try:
        from baleobala.runtime.metrics import start_http_server
        server = start_http_server(port)
        log.info("%s: metrics on http://127.0.0.1:%d/metrics (healthz at /healthz)", tag, port)
        return server
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: failed to bind metrics on port %d: %s — continuing without metrics", tag, port, exc)
        return None


def _resolve_bale_jwts(args: argparse.Namespace) -> list[str]:
    """Resolve one or more Bale JWTs from repeated CLI args.

    A single account can still only own one active Bale call reliably, so
    server-side capacity should be represented as a pool of account JWTs.
    """
    values: list[str] = []
    for jwt in _as_list(getattr(args, "bale_jwt", None)):
        jwt = jwt.strip()
        if jwt:
            values.append(jwt)
    for jwt_file in _as_list(getattr(args, "bale_jwt_file", None)):
        path = Path(jwt_file).expanduser()
        jwt = path.read_text().strip()
        if jwt:
            values.append(jwt)
    return list(dict.fromkeys(values))


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _first_bale_jwt_arg(args: argparse.Namespace) -> str | None:
    values = _as_list(getattr(args, "bale_jwt", None))
    return values[0] if values else None


def _first_bale_jwt_file_arg(args: argparse.Namespace) -> str | None:
    values = _as_list(getattr(args, "bale_jwt_file", None))
    return values[0] if values else None


def cmd_vpn_coordinator(args: argparse.Namespace) -> int:
    """Run the central rendezvous service.

    The coordinator listens on `--listen-jwt` for incoming Bale calls from
    clients, picks a free relay slot, hands the assignment back to the client,
    then instructs the chosen relay (via `--dispatch-jwt`) to expect the call.
    """
    import threading
    import time as _time

    from baleobala.coordinator import CoordinatorService, RelayRegistry, ServiceConfig
    from baleobala.coordinator.bale_transport import BaleCoordinatorTransport
    from baleobala.coordinator.registry import default_snapshot_path
    from baleobala.bale.ws_client import WsTlsConfig

    listen_jwt = _read_jwt_arg(args.listen_jwt, args.listen_jwt_file)
    if not listen_jwt:
        raise SystemExit("--listen-jwt or --listen-jwt-file required")
    dispatch_jwt = _read_jwt_arg(args.dispatch_jwt, args.dispatch_jwt_file) or listen_jwt

    snapshot_path = (
        Path(args.snapshot_path).expanduser() if args.snapshot_path else default_snapshot_path()
    )
    log.info("coordinator: state snapshot at %s", snapshot_path)

    ws_tls_config = WsTlsConfig.from_sources(
        ca_file=getattr(args, "ws_ca_file", None),
        ca_path=getattr(args, "ws_ca_path", None),
        insecure=bool(getattr(args, "ws_ssl_no_verify", False)),
        allow_insecure_debug=True,
    )

    registry = RelayRegistry(snapshot_path=snapshot_path)
    transport = BaleCoordinatorTransport(
        listen_jwt=listen_jwt,
        dispatch_jwt=dispatch_jwt,
        identity_prefix=args.identity_prefix,
        ws_tls_config=ws_tls_config,
    )
    service = CoordinatorService(
        transport=transport,
        registry=registry,
        config=ServiceConfig(stale_relay_timeout_secs=args.stale_timeout),
    )
    service.start()
    log.info(
        "coordinator: listening total_relays=%d capacity=%d in_use=%d",
        len(registry.list_relays()), registry.total_capacity(), registry.total_in_use(),
    )

    metrics_server = _maybe_start_metrics_server(
        getattr(args, "metrics_port", 0), tag="coordinator",
    )

    stop_event = threading.Event()

    def prune_loop() -> None:
        while not stop_event.wait(args.prune_interval):
            try:
                service.prune_stale()
            except Exception:  # noqa: BLE001
                log.exception("coordinator: prune pass failed")

    pruner = threading.Thread(target=prune_loop, name="coord-prune", daemon=True)
    pruner.start()

    from .runner import wait_for_signal
    try:
        wait_for_signal()
    finally:
        stop_event.set()
        try:
            service.stop()
        except Exception:  # noqa: BLE001
            log.exception("coordinator: service.stop failed")
        if metrics_server is not None:
            try:
                metrics_server.shutdown()
            except Exception:  # noqa: BLE001
                pass
    return 0


def _read_jwt_arg(value: str | None, file_path: str | None) -> str | None:
    if value:
        return value.strip()
    if file_path:
        return Path(file_path).expanduser().read_text().strip()
    return None


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
        print(f"[tunnel loopback] OK: {args.packets} pkts × {args.size} B "
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
        log.warning("tunnel: NAT setup script missing at %s; skipping", script)
        return
    sudo = shutil.which("sudo")
    cmd = [sudo, str(script), tun, wan] if (sudo and os.geteuid() != 0) else [str(script), tun, wan]
    log.info("tunnel: running NAT setup: %s", " ".join(cmd))
    subprocess.check_call(cmd)


def _run_tunnel_session(args: argparse.Namespace, *, is_exit_node: bool) -> int:
    from baleobala.cli import _resolve_livekit_credentials  # reuse existing

    from .runner import RunnerConfig, VpnRunner, prompt_tun_setup_hint, wait_for_signal
    from .tun import TunDevice
    import errno

    def _open_tun_with_fallback(preferred: str):
        names = [preferred]
        if preferred == "vpn0":
            names.extend(f"vpn{i}" for i in range(1, 6))
        last_exc: Exception | None = None
        for name in dict.fromkeys(names):
            try:
                return TunDevice.open(name)
            except OSError as exc:
                last_exc = exc
                if exc.errno != errno.EBUSY:
                    continue
            except PermissionError as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("no usable Linux TUN interface name was available")

    # 1. Open the TUN device (fail fast with a setup hint).
    try:
        tun = _open_tun_with_fallback(args.tun)
    except (PermissionError, RuntimeError, OSError) as e:
        log.error("tunnel: cannot open TUN %s: %s", args.tun, e)
        prompt_tun_setup_hint(args.tun, args.tun_addr, args.tun_mtu)
        return 3
    args.tun = tun.name

    # 2. JWT expiry warning (best-effort; no-op if creds were passed
    #    via --livekit-url/--livekit-token instead of a Bale JWT).
    from .jwt_util import warn_if_near_expiry
    jwt = _first_bale_jwt_arg(args)
    if not jwt and _first_bale_jwt_file_arg(args):
        try:
            jwt = Path(_first_bale_jwt_file_arg(args)).expanduser().read_text().strip()
        except OSError:
            jwt = None
    if jwt:
        warn_if_near_expiry(jwt)

    # 3. Resolve LiveKit creds via Bale (call or answer).
    url, token = _resolve_livekit_credentials(args)

    # 4. Build session + transport + keepalive + health monitor.
    from baleobala.bale import LiveKitSession

    from .keepalive import LiveKitKeepalive
    from .router import FailoverController

    session = LiveKitSession(url=url, token=token, identity=args.identity)
    session.start()
    peer_timeout = float(getattr(args, "peer_ready_timeout", 30.0))
    session.wait_for_remote_participant(timeout=peer_timeout)

    keepalive = LiveKitKeepalive(session, interval=20.0)
    keepalive.start()

    try:
        chain = _build_transport_chain(args.transport, session, args)
        transport_name, transport = chain.start()
        provision = None
        if not is_exit_node:
            provision = _maybe_receive_mesh_provisioning(
                args, tun, transport, transport_name,
            )
        # MTU floor = min across all candidates so pending frames
        # survive a hot-swap. Audio's 128 B dominates when it's in the
        # chain; DC-only runs use ~14 KiB.
        mtu_floor = min(_safe_mtu(c.factory, precomputed=transport if c.name == transport_name else None)
                        for c in chain._choices)  # type: ignore[attr-defined]

        cfg = RunnerConfig(
            sess_id=provision.session_id if provision is not None else args.sess_id,
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

        # When the tunnel emits `tunnel_dead` (8 consecutive max-retry
        # drops with no ACK), tear the whole session down and exit the
        # process so systemd brings up a fresh listener with a fresh WS
        # session — staying alive on a dead carrier is what produced the
        # "VPS thinks tunnel_up but Android sees no traffic" symptom.
        import threading as _threading
        carrier_dead = _threading.Event()

        def _on_runner_event(event: str, payload: dict) -> None:  # type: ignore[no-untyped-def]
            if event == "tunnel_dead" and not carrier_dead.is_set():
                carrier_dead.set()
                log.warning(
                    "tunnel: carrier dead consecutive_drops=%s; exiting for restart",
                    payload.get("consecutive_drops", "?"),
                )

        runner.add_event_handler(_on_runner_event)

        controller = FailoverController(
            runner,
            chain,
            carrier_session=session,
            carrier_factory=lambda: _rebuild_carrier_and_chain(args),
        )
        controller.start(transport_name)

        role = "exit-node" if is_exit_node else "client"
        sess_id = provision.session_id if provision is not None else args.sess_id
        log.info(
            "tunnel: up role=%s transport=%s tun=%s sess=0x%x mtu_floor=%d",
            role, transport_name, args.tun, sess_id, mtu_floor,
        )
        try:
            _wait_for_signal_or_carrier_dead(session, extra_event=carrier_dead)
        finally:
            controller.stop()
            runner.stop()
    finally:
        log.info("tunnel: teardown_done")
        keepalive.stop()
        try:
            chain.close()
        except Exception:  # noqa: BLE001
            pass
        session.stop()
        tun.close()
    return 0


def _wait_for_signal_or_carrier_dead(session, *, extra_event=None) -> None:  # type: ignore[no-untyped-def]
    """Block until SIGINT/SIGTERM, the LiveKit session terminates, the
    call goes silent, or `extra_event` is set. The dead-carrier exit
    lets systemd restart the process so a stale call doesn't hold the
    Bale account hostage and block subsequent inbound calls.

    `extra_event` is a `threading.Event` set by the caller when its own
    death-detection logic fires (e.g. the tunnel-level `tunnel_dead`
    emitter). LiveKit's session.is_terminal() lags badly on silent
    DataChannel failures, so this gives us a faster path out.
    """
    import signal
    import threading
    import time

    ev = threading.Event()
    if extra_event is not None:
        # Bridge the tunnel's event into our wait Event.
        def _watch_extra() -> None:
            extra_event.wait()
            ev.set()
        threading.Thread(target=_watch_extra, daemon=True, name="tunnel-dead-watch").start()

    def _signal_handler(signum, frame):  # type: ignore[no-untyped-def]
        ev.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except ValueError:
            pass

    def _terminal_observer(_exc) -> None:  # type: ignore[no-untyped-def]
        log.warning("tunnel: carrier session terminated; exiting for restart")
        ev.set()

    add_observer = getattr(session, "add_terminal_observer", None)
    if callable(add_observer):
        add_observer(_terminal_observer)

    is_terminal = getattr(session, "is_terminal", None)
    poll_interval = 5.0
    while not ev.wait(poll_interval):
        if callable(is_terminal):
            try:
                if is_terminal():
                    log.warning("tunnel: carrier session no longer running; exiting for restart")
                    return
            except Exception:
                pass


def _maybe_receive_mesh_provisioning(args, tun, transport, transport_name):  # type: ignore[no-untyped-def]
    from baleobala.control.mesh import mesh_assignment_to_cidr

    from .provisioning import (
        MeshProvisionMessage,
        ProvisioningError,
        configure_tun_interface,
        recv_mesh_message,
    )

    log.info("tunnel: waiting for mesh assignment from relay")
    msg = recv_mesh_message(transport, timeout=args.provision_timeout)
    if msg is None:
        return None
    if msg.kind == "error":
        raise ProvisioningError(msg.error or "mesh provisioning failed")
    if msg.kind != "assign":
        raise ProvisioningError(f"unexpected provisioning message: {msg.kind}")
    client_cidr = mesh_assignment_to_cidr(msg.client_ip, msg.prefix)
    configure_tun_interface(args.tun, client_cidr, msg.tun_mtu or args.tun_mtu)
    transport.send_bytes(MeshProvisionMessage(
        version=msg.version,
        kind="ack",
        peer_id=msg.peer_id,
        session_id=msg.session_id,
        pool_cidr=msg.pool_cidr,
        prefix=msg.prefix,
        gateway_ip=msg.gateway_ip,
        client_ip=msg.client_ip,
        tun_mtu=msg.tun_mtu,
        transport=transport_name,
    ).encode())
    args.tun_addr = client_cidr
    log.info(
        "tunnel: mesh assignment acknowledged client=%s gateway=%s prefix=%d",
        msg.client_ip, msg.gateway_ip, msg.prefix,
    )
    return msg


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
            log.warning("tunnel: transport swap failed: %s", e)
            return
        old = runner._tunnel.swap_transport(new_tx)  # type: ignore[attr-defined]
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("tunnel: swapped to transport %s", name)

    try:
        signal.signal(signal.SIGUSR1, handler)
    except (ValueError, OSError):
        pass  # e.g. on Windows or non-main thread


def _rebuild_carrier_and_chain(args):  # type: ignore[no-untyped-def]
    from baleobala.cli import _resolve_livekit_credentials
    from baleobala.bale import LiveKitSession
    from .keepalive import LiveKitKeepalive

    url, token = _resolve_livekit_credentials(args)
    session = LiveKitSession(url=url, token=token, identity=args.identity)
    session.start()
    keepalive = LiveKitKeepalive(session, interval=20.0)
    keepalive.start()
    chain = _build_transport_chain(args.transport, session, args)
    original_close = chain.close

    def close_with_session() -> None:
        try:
            original_close()
        finally:
            keepalive.stop()
            session.stop()

    chain.close = close_with_session  # type: ignore[method-assign]
    return session, chain


def _build_transport_chain(name: str, session, args):  # type: ignore[no-untyped-def]
    """Build a TransportChain ordered per user selection.

    Single-name selections produce a one-element chain (still hot-swap
    capable in the degenerate sense). `auto` produces
    dc → qr → audio → rpc → mtproto_rpc.
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
        return maybe_encrypt(DataChannelTransport(session, topic="vpn", reliable=False))
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
        jwt = _first_bale_jwt_arg(args)
        jwt_file = _first_bale_jwt_file_arg(args)
        if not jwt and jwt_file:
            jwt = Path(jwt_file).expanduser().read_text().strip()
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

    def mtproto_rpc():
        from baleobala.bale import MtprotoMessagingBackend
        from .transports.rpc_transport import RpcTransport
        if args.peer_id is None:
            raise RuntimeError("mtproto_rpc transport requires --peer-id")
        jwt = _first_bale_jwt_arg(args)
        jwt_file = _first_bale_jwt_file_arg(args)
        if not jwt and jwt_file:
            jwt = Path(jwt_file).expanduser().read_text().strip()
        client = MtprotoMessagingBackend(jwt=jwt)
        client.start()
        t = RpcTransport(client, peer_id=args.peer_id)
        _orig_close = t.close

        def close_with_client():  # type: ignore[no-untyped-def]
            try:
                _orig_close()
            finally:
                client.stop()

        t.close = close_with_client  # type: ignore[method-assign]
        return maybe_encrypt(t)
    mtproto_rpc._mtu_hint = 3 * 1024 - (OVERHEAD if psk_key else 0)  # type: ignore[attr-defined]

    from .router import RouterChoice, TransportChain
    named = {"dc": dc, "qr": qr, "audio": audio, "rpc": rpc, "mtproto_rpc": mtproto_rpc}
    if name in named:
        return TransportChain([RouterChoice(name, named[name])])
    if name == "auto":
        return TransportChain([
            RouterChoice("dc", dc),
            RouterChoice("qr", qr),
            RouterChoice("audio", audio),
            RouterChoice("rpc", rpc),
            RouterChoice("mtproto_rpc", mtproto_rpc),
        ])
    raise ValueError(f"unknown transport: {name}")


def _repo_root() -> Path:
    # src/baleobala/vpn/cli.py → repo root is 4 parents up
    return Path(__file__).resolve().parents[3]
