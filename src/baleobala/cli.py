"""
Command-line interface.

Subcommands:
    send     read messages from stdin (one per line) and transmit
    recv     listen, print each completed message on its own line
    devices  list audio devices sounddevice can see
    virtmic  create a virtual microphone and wait (Ctrl-C to tear down)
    loopback self-test: send → decode → compare, no virtual mic needed
    doctor   print a local readiness report
    auth     manage local auth/session state
    pair     manage relay pairing records
    relay    manage relay runtime settings
    vpn      run or inspect the product control plane
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Literal

from baleobala.virtmic import VirtualMic

log = logging.getLogger("baleobala")


def _clean_qt_environment(env: dict[str, str]) -> dict[str, str]:
    """Return an environment that prefers the bundled Qt runtime."""

    cleaned = dict(env)
    for key in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_QPA_PLATFORMTHEME",
        "QT_STYLE_OVERRIDE",
        "QT_DEBUG_PLUGINS",
    ):
        cleaned.pop(key, None)

    ld_library_path = cleaned.get("LD_LIBRARY_PATH")
    if ld_library_path:
        parts = []
        for item in ld_library_path.split(os.pathsep):
            if not item:
                continue
            lowered = item.lower()
            if "qt" in lowered and "pyside" not in lowered:
                continue
            parts.append(item)
        if parts:
            cleaned["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
        else:
            cleaned.pop("LD_LIBRARY_PATH", None)
    return cleaned


def _qt_environment_is_contaminated(env: dict[str, str]) -> bool:
    if any(env.get(key) for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")):
        return True
    ld_library_path = env.get("LD_LIBRARY_PATH", "")
    return any("qt" in item.lower() for item in ld_library_path.split(os.pathsep) if item)


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


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print a readiness report for the current machine."""
    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", sys.version_info >= (3, 9), f"{sys.version_info.major}.{sys.version_info.minor}"))
    checks.append(("pactl", shutil.which("pactl") is not None, shutil.which("pactl") or "missing"))
    checks.append(("sounddevice", _module_available("sounddevice"), "available" if _module_available("sounddevice") else "missing"))
    checks.append(("numpy", _module_available("numpy"), "available" if _module_available("numpy") else "missing"))
    checks.append(("ggwave", _module_available("ggwave"), "available" if _module_available("ggwave") else "missing"))
    checks.append(("PySide6", _module_available("PySide6"), "available" if _module_available("PySide6") else "missing"))

    print("baleobala doctor")
    print(f"platform: {sys.platform}")
    print("")
    missing_critical = []
    for name, ok, detail in checks:
        status = "OK" if ok else "MISSING"
        print(f"{name:10} {status:8} {detail}")
        if not ok and name in {"python", "pactl", "sounddevice"}:
            missing_critical.append(name)

    if sys.platform == "darwin":
        print("")
        print("macOS note: the current virtmic helper is Linux-only; use the desktop/VPN release path for the final product.")
    else:
        print("")
        print("Linux note: the current virtual mic helper is available now; the full VPN path is tracked in docs/VPN_PLAN.md.")

    if args.strict and missing_critical:
        return 1
    return 0


def _read_secret_text(value: str | None, fallback_env: str, fallback_file: str | None = None) -> str | None:
    if value:
        return value
    env_value = os.environ.get(fallback_env)
    if env_value:
        return env_value
    if fallback_file is not None:
        from pathlib import Path

        path = Path(fallback_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def cmd_auth(args: argparse.Namespace) -> int:
    from baleobala.control import AuthStore

    store = AuthStore()
    if args.auth_cmd == "login":
        jwt = _read_secret_text(args.jwt, "BALE_JWT", args.jwt_file)
        if not jwt:
            raise SystemExit("Need --jwt, BALE_JWT, or --jwt-file for auth login.")
        record = store.save_jwt(jwt, user_id=args.user_id, phone=args.phone)
        print(f"saved auth for provider={record.provider} user_id={record.user_id or 'unknown'}")
        return 0
    if args.auth_cmd == "logout":
        store.clear()
        print("cleared auth store")
        return 0
    status = store.status()
    for key, value in status.items():
        print(f"{key}: {value}")
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    from baleobala.control import PairingStore

    store = PairingStore()
    if args.pair_cmd == "start":
        record = store.begin(
            args.name,
            role=args.role,
            peer_id=args.peer_id,
            peer_name=args.peer_name,
            relay_mode=args.relay_mode,
        )
        print(f"profile_id: {record.profile_id}")
        print(f"name: {record.name}")
        print(f"role: {record.role}")
        print(f"status: {record.status}")
        print(f"pair_code: {record.pair_code}")
        return 0
    if args.pair_cmd == "accept":
        record = store.accept(args.code, name=args.name)
        print(f"profile_id: {record.profile_id}")
        print(f"name: {record.name}")
        print(f"role: {record.role}")
        print(f"status: {record.status}")
        print(f"pair_code: {record.pair_code}")
        return 0
    if args.pair_cmd == "remove":
        store.remove(args.profile_id)
        print(f"removed {args.profile_id}")
        return 0

    records = store.list()
    if not records:
        print("no pairings saved")
        return 0
    for record in records:
        print(
            f"{record.profile_id}\t{record.status}\t{record.role}\t{record.name}\t{record.pair_code}"
        )
    return 0


def cmd_relay(args: argparse.Namespace) -> int:
    from baleobala.control import PairingStore, VpnProfile, VpnStore
    from baleobala.control.vpn import default_vpn_backend

    pairing_store = PairingStore()
    vpn_store = VpnStore()
    if args.relay_cmd == "enable":
        profile = None
        if args.profile_id:
            profile = pairing_store.get(args.profile_id)
        if profile is None:
            profile = pairing_store.active()
        if profile is None:
            raise SystemExit("Need a pairing profile before enabling relay.")
        pairing_store.touch(profile.profile_id)
        vpn_profile = VpnProfile(
            profile_id=profile.profile_id,
            name=args.name or profile.name,
            role="relay",
            pairing_id=profile.profile_id,
            peer_id=profile.peer_id,
            peer_name=profile.peer_name,
            answer=True,
            backend=args.backend or default_vpn_backend(),
            auto_start=args.auto_start,
            listen_host=args.listen_host,
            listen_port=args.listen_port,
            protocol=args.protocol,
            volume=args.volume,
            proxy_secret=args.proxy_secret,
        )
        vpn_store.save(vpn_profile)
        print(f"enabled relay profile {vpn_profile.profile_id}")
        return 0
    if args.relay_cmd == "disable":
        vpn_store.save(VpnProfile(profile_id="disabled", name="disabled", backend="proxy", role="relay"))
        print("relay disabled")
        return 0
    status = vpn_store.status()
    for key, value in status.items():
        print(f"{key}: {value}")
    return 0


def _vpn_namespace_from_profile(profile, auth_record):
    import argparse as _argparse

    ns = _argparse.Namespace()
    ns.listen_host = profile.listen_host
    ns.listen_port = profile.listen_port
    ns.livekit_url = None
    ns.livekit_token = None
    ns.livekit_room = None
    ns.bale_jwt = auth_record.jwt if auth_record is not None else None
    ns.bale_jwt_file = None
    ns.peer_id = profile.peer_id
    ns.peer = None
    ns.peer_name = profile.peer_name
    ns.answer = profile.answer
    ns.answer_timeout = 120.0
    ns.identity = profile.name
    ns.protocol = profile.protocol
    ns.volume = profile.volume
    ns.proxy_secret = profile.proxy_secret
    return ns


def _tunnel_namespace_from_profile(profile, auth_record):
    import argparse as _argparse

    ns = _vpn_namespace_from_profile(profile, auth_record)
    ns.tun = "vpn0"
    ns.tun_addr = "10.77.0.1/24" if profile.role == "relay" else "10.77.0.2/24"
    ns.tun_mtu = 1400
    ns.transport = "auto"
    ns.sess_id = 0x1111
    ns.psk = None
    ns.psk_file = None
    ns.wan = "eth0"
    ns.skip_nat_setup = False
    return ns


def _hold_backend(endpoint: str, *, label: str) -> int:
    print(f"{label}: {endpoint}", file=sys.stderr)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


def _start_packet_tunnel_runtime(profile, auth_record):
    from baleobala.control import CarrierTunnelService
    from baleobala.control.paths import config_dir
    from baleobala.runtime.frame import TunnelRole

    args = _vpn_namespace_from_profile(profile, auth_record)
    role = TunnelRole.SERVER if profile.role == "relay" else TunnelRole.CLIENT
    bridge = _open_audio_tunnel(args, role)
    socket_path = config_dir() / "runtime" / f"{profile.profile_id}.sock"
    runtime = CarrierTunnelService(
        bridge,
        socket_path=socket_path,
        manage_bridge=False,
    )
    return runtime, bridge


def cmd_vpn(args: argparse.Namespace) -> int:
    from baleobala.control import (
        AuthStore,
        NetnsHarness,
        NetnsProcessManager,
        NetnsProcessSpec,
        NetnsTopology,
        NetnsSessionRunner,
        PairingStore,
        NetnsScenario,
        analyze_bundle,
        build_product_verdict,
        VpnProfile,
        VpnStore,
        build_proxy_pair_scenario,
        build_tunnel_pair_scenario,
        probe_endpoint,
        render_process_script,
        render_setup_commands,
        render_shell_script,
        render_smoke_commands,
        render_teardown_commands,
        smoke_backend_status,
    )
    from baleobala.control.backend import backend_for_profile, default_backend_name
    from baleobala.control.macos_launchd import MacOSLaunchAgentManager

    vpn_store = VpnStore()
    auth_store = AuthStore()
    pairing_store = PairingStore()

    if args.vpn_cmd == "plan":
        print("baleobala vpn plan")
        if sys.platform == "darwin":
            print("default: macOS packet-tunnel backend with shared route/DNS profile")
            print("alt: --backend proxy bridges to a paired Bale relay over LiveKit")
            print("fallback: direct/proxy backends remain available for debugging and recovery")
        else:
            print("target: Linux TUN + route/DNS helper")
            print("next: wire a privileged helper for route and DNS changes")
        print("carrier: Bale LiveKit credentials + tunnel runtime underneath")
        return 0

    if args.vpn_cmd == "agent":
        if sys.platform != "darwin":
            raise SystemExit("vpn agent is only available on macOS.")
        manager = MacOSLaunchAgentManager()
        if args.agent_cmd == "install":
            path = manager.install(profile_id=args.profile_id)
            print(f"installed launch agent: {path}")
            return 0
        if args.agent_cmd == "remove":
            manager.remove()
            print("removed launch agent")
            return 0
        if args.agent_cmd == "start":
            manager.start()
            print("started launch agent")
            return 0
        if args.agent_cmd == "stop":
            manager.stop()
            print("stopped launch agent")
            return 0
        status = manager.status()
        for key, value in status.items():
            print(f"{key}: {value}")
        return 0

    if args.vpn_cmd == "status":
        auth_status = auth_store.status()
        vpn_status = vpn_store.status()
        pairing = pairing_store.active()
        profile = vpn_store.load() or vpn_store.ensure_default()
        backend = backend_for_profile(profile)
        print("auth:")
        for key, value in auth_status.items():
            print(f"  {key}: {value}")
        print("vpn:")
        for key, value in vpn_status.items():
            print(f"  {key}: {value}")
        print("backend:")
        for key, value in backend.status().items():
            print(f"  {key}: {value}")
        if sys.platform == "darwin" and profile.backend in {"proxy", "direct"}:
            from baleobala.control.macos import MacOSSystemProxySession
            system_proxy = MacOSSystemProxySession(state_path=None)
            print("system_proxy:")
            for key, value in system_proxy.status().items():
                print(f"  {key}: {value}")
        print("pairing:")
        if pairing is None:
            print("  state: empty")
        else:
            print(f"  profile_id: {pairing.profile_id}")
            print(f"  name: {pairing.name}")
            print(f"  role: {pairing.role}")
            print(f"  status: {pairing.status}")
        return 0

    if args.vpn_cmd == "probe":
        profile = vpn_store.load() or vpn_store.ensure_default()
        backend = backend_for_profile(profile)
        backend_status = backend.status()
        probe = probe_endpoint(backend_status.get("endpoint"))
        if args.json:
            print(json.dumps({"backend": backend_status, "probe": probe.to_dict()}, indent=2, sort_keys=True))
            return 0 if probe.ok else 2
        for key, value in backend_status.items():
            print(f"{key}: {value}")
        print("probe:")
        for key, value in probe.to_dict().items():
            print(f"  {key}: {value}")
        return 0 if probe.ok else 2

    if args.vpn_cmd == "smoke":
        profile = vpn_store.load() or vpn_store.ensure_default()
        backend = backend_for_profile(profile)
        backend_status = backend.status()
        report = smoke_backend_status(backend_status, timeout=args.timeout)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            for key, value in report.to_dict().items():
                print(f"{key}: {value}")
        return 0 if report.ok else 2

    if args.vpn_cmd == "analyze-bundle":
        analysis = analyze_bundle(args.bundle_path)
        payload = analysis.to_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")
        return 0 if analysis.ok == "yes" else 2

    if args.vpn_cmd == "verdict":
        status = backend_for_profile(vpn_store.load() or vpn_store.ensure_default()).status()
        bundle_analysis = analyze_bundle(args.bundle_path) if args.bundle_path else None
        verdict = build_product_verdict(status, bundle_analysis)
        payload = verdict.to_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")
        return 0 if verdict.ok == "yes" else 2

    if args.vpn_cmd == "netns-plan":
        topology = NetnsTopology.with_prefix(args.prefix)
        if args.format == "shell":
            print(render_shell_script(topology))
            return 0
        payload = {
            "topology": {
                "client_ns": topology.client_ns,
                "server_ns": topology.server_ns,
                "client_veth": topology.client_veth,
                "server_veth": topology.server_veth,
                "client_ip_cidr": topology.client_ip_cidr,
                "server_ip_cidr": topology.server_ip_cidr,
            },
            "setup": render_setup_commands(topology),
            "smoke": render_smoke_commands(topology),
            "teardown": render_teardown_commands(topology),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.vpn_cmd == "netns-run":
        topology = NetnsTopology.with_prefix(args.prefix)
        harness = NetnsHarness(topology)
        if args.phase == "setup":
            report = harness.setup()
        elif args.phase == "smoke":
            report = harness.smoke()
        elif args.phase == "teardown":
            report = harness.teardown()
        elif args.phase == "status":
            report = harness.status()
            if report is None:
                print(json.dumps({"ok": "no", "stage": "missing", "topology": {}, "steps": [], "last_error": "no saved harness state"}, indent=2, sort_keys=True))
                return 2
        else:
            report = harness.full_cycle()
        payload = report.to_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key, value in payload.items():
                if key == "steps":
                    print("steps:")
                    for item in value:
                        print(f"  - {item['phase']} rc={item['returncode']} ok={item['ok']} cmd={' '.join(item['command'])}")
                elif key == "topology":
                    print("topology:")
                    for name, item in value.items():
                        print(f"  {name}: {item}")
                else:
                    print(f"{key}: {value}")
        return 0 if payload["ok"] == "yes" else 2

    if args.vpn_cmd == "netns-process-plan":
        topology = NetnsTopology.with_prefix(args.prefix)
        server_cmd = ["sh", "-lc", args.server_cmd]
        client_cmd = ["sh", "-lc", args.client_cmd]
        harness = NetnsHarness(topology)
        specs = harness.build_process_specs(server_cmd=server_cmd, client_cmd=client_cmd)
        if args.format == "shell":
            print(
                render_process_script(
                    topology,
                    server_cmd=server_cmd,
                    client_cmd=client_cmd,
                    logs_dir=str(harness._logs_dir),
                )
            )
            return 0
        payload = {
            "topology": {
                "client_ns": topology.client_ns,
                "server_ns": topology.server_ns,
            },
            "processes": [spec.to_dict() for spec in specs],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.vpn_cmd == "netns-process-run":
        topology = NetnsTopology.with_prefix(args.prefix)
        harness = NetnsHarness(topology)
        manager = NetnsProcessManager()
        if args.phase == "status":
            statuses = manager.status()
        else:
            specs = harness.build_process_specs(
                server_cmd=["sh", "-lc", args.server_cmd],
                client_cmd=["sh", "-lc", args.client_cmd],
            )
            if args.phase == "start":
                statuses = manager.start(specs)
            else:
                statuses = manager.stop()
        payload = {"items": [item.to_dict() for item in statuses]}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.vpn_cmd == "netns-session":
        if args.kind == "proxy-pair":
            scenario = build_proxy_pair_scenario(
                server_jwt_file=args.server_jwt_file,
                client_jwt_file=args.client_jwt_file,
                peer_id=args.peer_id,
                proxy_secret=args.proxy_secret,
                listen_port=args.listen_port,
            )
        else:
            scenario = build_tunnel_pair_scenario(
                server_jwt_file=args.server_jwt_file,
                client_jwt_file=args.client_jwt_file,
                peer_id=args.peer_id,
                server_tun=args.server_tun,
                client_tun=args.client_tun,
                server_wan=args.server_wan,
                transport=args.transport,
                psk_file=args.psk_file,
            )
        topology = NetnsTopology.with_prefix(args.prefix)
        harness = NetnsHarness(topology)
        manager = NetnsProcessManager()
        artifact_root = Path(args.artifact_dir).expanduser() if getattr(args, "artifact_dir", "") else None
        session = NetnsSessionRunner(harness, manager, artifact_root=artifact_root)
        report = session.run(
            server_cmd=scenario.server_cmd,
            client_cmd=scenario.client_cmd,
            server_ready_pattern=scenario.server_ready,
            client_ready_pattern=scenario.client_ready,
            smoke_commands=[
                ["ip", "netns", "exec", topology.client_ns, "sh", "-lc", scenario.smoke_client_cmd]
            ] if scenario.smoke_client_cmd else None,
            timeout=args.timeout,
            scenario=scenario.to_dict(),
        )
        payload = report.to_dict()
        payload["scenario"] = scenario.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.ok else 2

    if args.vpn_cmd == "netns-scenario":
        if args.kind == "proxy-pair":
            scenario = build_proxy_pair_scenario(
                server_jwt_file=args.server_jwt_file,
                client_jwt_file=args.client_jwt_file,
                peer_id=args.peer_id,
                proxy_secret=args.proxy_secret,
                listen_port=args.listen_port,
            )
        else:
            scenario = build_tunnel_pair_scenario(
                server_jwt_file=args.server_jwt_file,
                client_jwt_file=args.client_jwt_file,
                peer_id=args.peer_id,
                server_tun=args.server_tun,
                client_tun=args.client_tun,
                server_wan=args.server_wan,
                transport=args.transport,
                psk_file=args.psk_file,
            )
        print(json.dumps(scenario.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.vpn_cmd == "down":
        profile = vpn_store.load() or vpn_store.ensure_default()
        backend = backend_for_profile(profile)
        backend.down()
        if sys.platform == "darwin" and profile.backend in {"proxy", "direct"}:
            from baleobala.control.macos import MacOSSystemProxySession
            restored = MacOSSystemProxySession.restore_saved_state()
            if restored:
                print("restored macOS proxy settings")
            else:
                print("no saved macOS proxy state found")
            return 0
        if sys.platform == "darwin":
            print("stopped macOS packet-tunnel backend")
            return 0
        print("vpn down: stop the foreground session with Ctrl-C if it is running, and clear saved runtime state.")
        return 0

    profile = vpn_store.load()
    if profile is None:
        profile = vpn_store.ensure_default()
    if getattr(args, "backend", None):
        profile = VpnProfile(
            profile_id=profile.profile_id,
            name=profile.name,
            backend=args.backend,
            role=profile.role,
            pairing_id=profile.pairing_id,
            peer_id=profile.peer_id,
            peer_name=profile.peer_name,
            answer=profile.answer,
            auto_start=profile.auto_start,
            listen_host=profile.listen_host,
            listen_port=profile.listen_port,
            protocol=profile.protocol,
            volume=profile.volume,
            proxy_secret=profile.proxy_secret,
        )
        vpn_store.save(profile)
    if profile.pairing_id is None:
        active_pairing = pairing_store.active()
        if active_pairing is not None:
            pairing_store.touch(active_pairing.profile_id)
            profile = VpnProfile(
                profile_id=active_pairing.name,
                name=active_pairing.name,
                backend=profile.backend or default_backend_name(),
                role=active_pairing.role,
                pairing_id=active_pairing.profile_id,
                peer_id=active_pairing.peer_id,
                peer_name=active_pairing.peer_name,
                answer=active_pairing.role == "relay",
                proxy_secret=profile.proxy_secret,
                listen_host=profile.listen_host,
                listen_port=profile.listen_port,
                protocol=profile.protocol,
                volume=profile.volume,
            )
            vpn_store.save(profile)
    if args.profile_id:
        pairing = pairing_store.get(args.profile_id)
        if pairing is None:
            raise SystemExit(f"no pairing profile {args.profile_id!r} found")
        pairing_store.touch(pairing.profile_id)
        profile = VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend=profile.backend or default_backend_name(),
            role=pairing.role,
            pairing_id=pairing.profile_id,
            peer_id=pairing.peer_id,
            peer_name=pairing.peer_name,
            answer=pairing.role == "relay",
            proxy_secret=profile.proxy_secret,
            listen_host=profile.listen_host,
            listen_port=profile.listen_port,
            protocol=profile.protocol,
            volume=profile.volume,
        )
        vpn_store.save(profile)

    auth_record = auth_store.load()
    if auth_record is None and profile.backend == "proxy":
        print("warning: no stored auth session; the current proxy path may still require explicit Bale credentials.", file=sys.stderr)

    if profile.backend == "linux-tun":
        from baleobala.vpn.cli import _run_nat_setup, _run_tunnel_session

        tunnel_args = _tunnel_namespace_from_profile(profile, auth_record)
        backend = backend_for_profile(profile)
        backend.up(profile)
        try:
            if profile.role == "relay":
                _run_nat_setup(tunnel_args.tun, tunnel_args.wan)
            return _run_tunnel_session(
                tunnel_args,
                is_exit_node=profile.role == "relay",
            )
        finally:
            backend.down()

    if profile.role == "relay":
        relay_args = _vpn_namespace_from_profile(profile, auth_record)
        backend = backend_for_profile(profile)
        backend_state = backend.up(profile)
        try:
            if profile.backend == "proxy":
                return cmd_bale_proxy_relay(relay_args)
            runtime, bridge = _start_packet_tunnel_runtime(profile, auth_record)
            try:
                runtime_state = runtime.start(
                    profile_id=profile.profile_id,
                    backend=profile.backend,
                    pairing_id=profile.pairing_id,
                )
                return _hold_backend(
                    runtime_state.endpoint or "local service",
                    label="macOS packet-tunnel backend active",
                )
            finally:
                runtime.stop()
                bridge.close()
        finally:
            backend.down()

    client_args = _vpn_namespace_from_profile(profile, auth_record)
    backend = backend_for_profile(profile)
    backend_state = backend.up(profile)
    try:
        if profile.backend == "direct":
            endpoint = backend_state.get(
                "endpoint", f"{profile.listen_host}:{profile.listen_port}"
            )
            print(
                "macOS system proxy active:",
                backend_state.get("proxy", endpoint),
                file=sys.stderr,
            )
            return _hold_backend(endpoint, label="macOS direct proxy active")
        if profile.backend == "proxy":
            print(
                "macOS system proxy active:",
                backend_state.get("proxy", f"{profile.listen_host}:{profile.listen_port}"),
                file=sys.stderr,
            )
            return cmd_bale_proxy_client(client_args)
        else:
            runtime, bridge = _start_packet_tunnel_runtime(profile, auth_record)
            try:
                runtime_state = runtime.start(
                    profile_id=profile.profile_id,
                    backend=profile.backend,
                    pairing_id=profile.pairing_id,
                )
                return _hold_backend(
                    runtime_state.endpoint or "local service",
                    label="macOS packet-tunnel backend active",
                )
            finally:
                runtime.stop()
                bridge.close()
    finally:
        backend.down()


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

    doc = sub.add_parser("doctor", help="check local readiness for baleobala")
    doc.add_argument("--strict", action="store_true", help="return nonzero when critical deps are missing")
    doc.set_defaults(func=cmd_doctor)

    auth = sub.add_parser("auth", help="manage local auth/session state")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)

    auth_login = auth_sub.add_parser("login", help="store a Bale JWT for later use")
    auth_login.add_argument("--jwt", default=None)
    auth_login.add_argument("--jwt-file", default=None)
    auth_login.add_argument("--user-id", type=int, default=None)
    auth_login.add_argument("--phone", default=None)
    auth_login.set_defaults(func=cmd_auth)

    auth_logout = auth_sub.add_parser("logout", help="clear stored auth state")
    auth_logout.set_defaults(func=cmd_auth)

    auth_status = auth_sub.add_parser("status", help="show stored auth state")
    auth_status.set_defaults(func=cmd_auth)

    pair = sub.add_parser("pair", help="manage relay pairing records")
    pair_sub = pair.add_subparsers(dest="pair_cmd", required=True)

    pair_start = pair_sub.add_parser("start", help="create a pending pairing record")
    pair_start.add_argument("--name", default="relay")
    pair_start.add_argument("--role", choices=["client", "relay"], default="client")
    pair_start.add_argument("--peer-id", type=int, default=None)
    pair_start.add_argument("--peer-name", default=None)
    pair_start.add_argument("--relay-mode", choices=["proxy"], default="proxy")
    pair_start.set_defaults(func=cmd_pair)

    pair_accept = pair_sub.add_parser("accept", help="accept a pairing code")
    pair_accept.add_argument("--code", required=True)
    pair_accept.add_argument("--name", default=None)
    pair_accept.set_defaults(func=cmd_pair)

    pair_list = pair_sub.add_parser("list", help="list pairing records")
    pair_list.set_defaults(func=cmd_pair)

    pair_remove = pair_sub.add_parser("remove", help="remove a pairing record")
    pair_remove.add_argument("--profile-id", required=True)
    pair_remove.set_defaults(func=cmd_pair)

    relay = sub.add_parser("relay", help="manage relay runtime settings")
    relay_sub = relay.add_subparsers(dest="relay_cmd", required=True)

    relay_enable = relay_sub.add_parser("enable", help="save relay settings")
    relay_enable.add_argument("--profile-id", default=None)
    relay_enable.add_argument("--name", default=None)
    relay_enable.add_argument("--backend", choices=["packet-tunnel", "proxy", "linux-tun"], default=None)
    relay_enable.add_argument("--auto-start", action="store_true")
    relay_enable.add_argument("--listen-host", default="127.0.0.1")
    relay_enable.add_argument("--listen-port", type=int, default=1080)
    relay_enable.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    relay_enable.add_argument("--volume", type=int, default=50)
    relay_enable.add_argument("--proxy-secret", default=None)
    relay_enable.set_defaults(func=cmd_relay)

    relay_disable = relay_sub.add_parser("disable", help="clear relay settings")
    relay_disable.set_defaults(func=cmd_relay)

    relay_status = relay_sub.add_parser("status", help="show relay settings")
    relay_status.set_defaults(func=cmd_relay)

    vpn = sub.add_parser("vpn", help="run or inspect the VPN control plane")
    vpn_sub = vpn.add_subparsers(dest="vpn_cmd", required=True)

    vpn_up = vpn_sub.add_parser("up", help="start the current VPN backend")
    vpn_up.add_argument("--profile-id", default=None)
    vpn_up.add_argument("--backend", choices=["packet-tunnel", "proxy", "linux-tun"], default=None)
    vpn_up.set_defaults(func=cmd_vpn)

    vpn_down = vpn_sub.add_parser("down", help="stop the current VPN backend")
    vpn_down.set_defaults(func=cmd_vpn)

    vpn_status = vpn_sub.add_parser("status", help="show VPN control-plane state")
    vpn_status.set_defaults(func=cmd_vpn)

    vpn_probe = vpn_sub.add_parser("probe", help="probe the active backend endpoint for automation")
    vpn_probe.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    vpn_probe.set_defaults(func=cmd_vpn)

    vpn_smoke = vpn_sub.add_parser("smoke", help="run a deterministic backend smoke verdict")
    vpn_smoke.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    vpn_smoke.add_argument("--timeout", type=float, default=1.0, help="probe timeout in seconds")
    vpn_smoke.set_defaults(func=cmd_vpn)

    vpn_bundle = vpn_sub.add_parser("analyze-bundle", help="deterministically classify a netns session bundle")
    vpn_bundle.add_argument("bundle_path", help="path to a verdict bundle directory")
    vpn_bundle.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    vpn_bundle.set_defaults(func=cmd_vpn)

    vpn_verdict = vpn_sub.add_parser("verdict", help="emit a canonical product verdict from backend status and bundle analysis")
    vpn_verdict.add_argument("--bundle-path", default="", help="optional verdict bundle directory")
    vpn_verdict.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    vpn_verdict.set_defaults(func=cmd_vpn)

    vpn_netns = vpn_sub.add_parser("netns-plan", help="print a single-host Linux netns topology plan")
    vpn_netns.add_argument("--prefix", default="baleobala", help="resource name prefix")
    vpn_netns.add_argument("--format", choices=["json", "shell"], default="json")
    vpn_netns.set_defaults(func=cmd_vpn)

    vpn_netns_run = vpn_sub.add_parser("netns-run", help="run or inspect the single-host Linux netns harness")
    vpn_netns_run.add_argument("--prefix", default="baleobala", help="resource name prefix")
    vpn_netns_run.add_argument("--phase", choices=["full-cycle", "setup", "smoke", "teardown", "status"], default="full-cycle")
    vpn_netns_run.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    vpn_netns_run.set_defaults(func=cmd_vpn)

    vpn_netns_process = vpn_sub.add_parser("netns-process-plan", help="print wrapped client/server process commands for Linux netns")
    vpn_netns_process.add_argument("--prefix", default="baleobala", help="resource name prefix")
    vpn_netns_process.add_argument("--server-cmd", required=True, help="server command to run inside server namespace")
    vpn_netns_process.add_argument("--client-cmd", required=True, help="client command to run inside client namespace")
    vpn_netns_process.add_argument("--format", choices=["json", "shell"], default="json")
    vpn_netns_process.set_defaults(func=cmd_vpn)

    vpn_netns_process_run = vpn_sub.add_parser("netns-process-run", help="start, stop, or inspect wrapped Linux netns processes")
    vpn_netns_process_run.add_argument("--prefix", default="baleobala", help="resource name prefix")
    vpn_netns_process_run.add_argument("--phase", choices=["start", "stop", "status"], default="status")
    vpn_netns_process_run.add_argument("--server-cmd", default="echo server", help="server command for start")
    vpn_netns_process_run.add_argument("--client-cmd", default="echo client", help="client command for start")
    vpn_netns_process_run.set_defaults(func=cmd_vpn)

    vpn_netns_session = vpn_sub.add_parser("netns-session", help="run a full single-host Linux netns session with real baleobala scenario templates")
    vpn_netns_session.add_argument("--prefix", default="baleobala", help="resource name prefix")
    vpn_netns_session.add_argument("--kind", choices=["proxy-pair", "tunnel-pair"], required=True)
    vpn_netns_session.add_argument("--server-jwt-file", required=True)
    vpn_netns_session.add_argument("--client-jwt-file", required=True)
    vpn_netns_session.add_argument("--peer-id", type=int, required=True)
    vpn_netns_session.add_argument("--proxy-secret", default="baleobala-secret")
    vpn_netns_session.add_argument("--listen-port", type=int, default=1080)
    vpn_netns_session.add_argument("--server-tun", default="vpn0")
    vpn_netns_session.add_argument("--client-tun", default="vpn0")
    vpn_netns_session.add_argument("--server-wan", default="eth0")
    vpn_netns_session.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc"], default="auto")
    vpn_netns_session.add_argument("--psk-file", default="")
    vpn_netns_session.add_argument("--timeout", type=float, default=5.0, help="readiness timeout in seconds")
    vpn_netns_session.add_argument("--artifact-dir", default="", help="directory for verdict artifacts")
    vpn_netns_session.set_defaults(func=cmd_vpn)

    vpn_netns_scenario = vpn_sub.add_parser("netns-scenario", help="print a real baleobala netns scenario template")
    vpn_netns_scenario.add_argument("--kind", choices=["proxy-pair", "tunnel-pair"], required=True)
    vpn_netns_scenario.add_argument("--server-jwt-file", required=True)
    vpn_netns_scenario.add_argument("--client-jwt-file", required=True)
    vpn_netns_scenario.add_argument("--peer-id", type=int, required=True)
    vpn_netns_scenario.add_argument("--proxy-secret", default="baleobala-secret")
    vpn_netns_scenario.add_argument("--listen-port", type=int, default=1080)
    vpn_netns_scenario.add_argument("--server-tun", default="vpn0")
    vpn_netns_scenario.add_argument("--client-tun", default="vpn0")
    vpn_netns_scenario.add_argument("--server-wan", default="eth0")
    vpn_netns_scenario.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc"], default="auto")
    vpn_netns_scenario.add_argument("--psk-file", default="")
    vpn_netns_scenario.set_defaults(func=cmd_vpn)

    vpn_plan = vpn_sub.add_parser("plan", help="print the system VPN delivery plan")
    vpn_plan.set_defaults(func=cmd_vpn)

    vpn_agent = vpn_sub.add_parser("agent", help="manage the macOS LaunchAgent")
    vpn_agent_sub = vpn_agent.add_subparsers(dest="agent_cmd", required=True)

    vpn_agent_install = vpn_agent_sub.add_parser("install", help="install the LaunchAgent")
    vpn_agent_install.add_argument("--profile-id", default=None)
    vpn_agent_install.set_defaults(func=cmd_vpn)

    vpn_agent_remove = vpn_agent_sub.add_parser("remove", help="remove the LaunchAgent")
    vpn_agent_remove.set_defaults(func=cmd_vpn)

    vpn_agent_start = vpn_agent_sub.add_parser("start", help="start the LaunchAgent")
    vpn_agent_start.set_defaults(func=cmd_vpn)

    vpn_agent_stop = vpn_agent_sub.add_parser("stop", help="stop the LaunchAgent")
    vpn_agent_stop.set_defaults(func=cmd_vpn)

    vpn_agent_status = vpn_agent_sub.add_parser("status", help="show LaunchAgent state")
    vpn_agent_status.set_defaults(func=cmd_vpn)

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

    from baleobala.bale.protos import WEB_API_KEY, WEB_APP_ID
    ba = sub.add_parser(
        "bale-auth",
        help="phone/SMS login flow → prints a JWT. Defaults to Bale Web "
             "credentials (app_id=4), which is what web.bale.ai uses.",
    )
    ba.add_argument("--phone", required=True, help="phone in E.164, with or without '+'")
    ba.add_argument("--app-id", type=int, default=WEB_APP_ID,
                    help=f"default: {WEB_APP_ID} (Bale Web)")
    ba.add_argument("--api-key", default=WEB_API_KEY,
                    help="default: Bale Web's public api_key")
    ba.add_argument("--device-title", default="baleobala")
    ba.add_argument("--device-hash-hex", default=None,
                    help="hex-encoded device hash (default: random 16B)")
    ba.set_defaults(func=cmd_bale_auth)

    g = sub.add_parser("gui", help="launch the Qt GUI (login + connect)")
    g.set_defaults(func=cmd_gui)

    from baleobala.vpn.cli import add_tunnel_subparser
    add_tunnel_subparser(sub)

    return p


def cmd_bale_auth(args: argparse.Namespace) -> int:
    """Phone/SMS login over gRPC-Web HTTP/2 — live-verified 2026-04-20.
    Prints the JWT on stdout; SMS prompt goes to stderr so you can
    pipe the JWT to a file:
        baleobala bale-auth --phone +98... > ~/.bale_jwt
    """
    from baleobala.bale.auth import BaleAuth
    from baleobala.bale.grpc_web import GrpcWebError

    phone = str(args.phone).lstrip("+")
    if not phone.isdigit():
        raise SystemExit("invalid phone number (digits only, optional +)")
    device_hash = (
        bytes.fromhex(args.device_hash_hex) if args.device_hash_hex else None
    )

    auth = BaleAuth(
        app_id=args.app_id,
        api_key=args.api_key,
        device_title=args.device_title,
        device_hash=device_hash,
    )
    try:
        tx = auth.start_phone_auth(int(phone))
    except GrpcWebError as e:
        raise SystemExit(f"StartPhoneAuth failed: {e}")
    print(f"[bale-auth] SMS sent; transaction_hash={tx}", file=sys.stderr)

    # Loop: some accounts get 1 wrong code; let them retry without
    # re-sending SMS (since the transaction is still valid).
    for attempt in range(1, 4):
        code = input("SMS code: ").strip()
        try:
            session = auth.validate_code(code)
            break
        except GrpcWebError as e:
            if "EXPIRED" in e.message:
                raise SystemExit(
                    f"code expired (attempt {attempt}); rerun to send a fresh SMS"
                )
            print(f"[bale-auth] {e.message}; retry {attempt}/3", file=sys.stderr)
    else:
        raise SystemExit("too many invalid codes; giving up")

    print(session.jwt)
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    if _qt_environment_is_contaminated(os.environ) and not os.environ.get(
        "BALEOBALA_QT_ENV_CLEANED"
    ):
        env = _clean_qt_environment(os.environ)
        env["BALEOBALA_QT_ENV_CLEANED"] = "1"
        os.execvpe(sys.executable, [sys.executable, "-m", "baleobala.cli", "gui"], env)
    try:
        from baleobala.gui import run
    except ImportError as e:
        raise SystemExit(
            "GUI requires PySide6. Install with: pip install -e \".[desktop]\" "
            f"(import failed: {e})"
        )
    return run()


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
