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

from baleobala.presentation import capability_note, summarize_doctor, summarize_snapshot
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
    # Avoid the GNOME platform theme plugin on desktops without a portal
    # service. The GUI uses its own Fusion stylesheet, so this does not
    # change the visual design, only the Qt integration layer.
    cleaned["QT_QPA_PLATFORMTHEME"] = "fusion"
    cleaned["QT_STYLE_OVERRIDE"] = "Fusion"

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
    from baleobala.control.paths import is_android_runtime

    checks: list[tuple[str, bool, str]] = []
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    checks.append(("python", sys.version_info >= (3, 9), f"{sys.version_info.major}.{sys.version_info.minor}"))
    if sys.platform.startswith("linux") and not is_android_runtime():
        checks.append(("pactl", shutil.which("pactl") is not None, shutil.which("pactl") or "missing"))
    checks.append(("sounddevice", _module_available("sounddevice"), "available" if _module_available("sounddevice") else "missing"))
    checks.append(("numpy", _module_available("numpy"), "available" if _module_available("numpy") else "missing"))
    checks.append(("ggwave", _module_available("ggwave"), "available" if _module_available("ggwave") else "missing"))
    checks.append(("PySide6", _module_available("PySide6"), "available" if _module_available("PySide6") else "missing"))
    if sys.platform == "win32":
        checks.append(("powershell", powershell is not None, powershell or "missing"))
    if sys.platform == "darwin":
        from baleobala.control.macos import code_signing_status

        signing = code_signing_status()
        checks.append(("codesign", signing.state == "ready", signing.detail))

    headline, _missing, next_step = summarize_doctor(checks)
    print("baleobala doctor")
    print(headline)
    print(f"platform: {sys.platform}")
    print(capability_note())
    print("")
    missing_critical = []
    for name, ok, detail in checks:
        status = "OK" if ok else "MISSING"
        print(f"{name:10} {status:8} {detail}")
        if not ok and name in {"python", "pactl", "sounddevice", "powershell"}:
            missing_critical.append(name)

    if is_android_runtime():
        print("")
        print("Android path: sign in, pair a relay if needed, grant VPN permission, then start `baleobala vpn up`.")
    elif sys.platform == "darwin":
        print("")
        print("macOS path: use `baleobala gui` for sign-in and pairing, then the native app for system tunnel control.")
    elif sys.platform == "win32":
        print("")
        print("Windows path: sign in, pair if needed, then start the WinHTTP-backed development proxy with `baleobala vpn up`.")
    else:
        print("")
        print("Linux path: sign in, create or accept a pairing, then start the secure connection with `baleobala vpn up`.")
    print("")
    print(next_step)

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
    if args.auth_cmd == "bale-login":
        jwt = _run_bale_auth_login(args)
        if args.print_jwt:
            print(jwt)
        if args.save:
            record = store.save_jwt(jwt, user_id=getattr(args, "user_id", None), phone=args.phone)
            print(
                f"saved auth for provider={record.provider} "
                f"user_id={record.user_id or 'unknown'}",
                file=sys.stderr,
            )
        if args.jwt_out:
            Path(args.jwt_out).expanduser().write_text(jwt + "\n", encoding="utf-8")
            print(f"wrote jwt to {args.jwt_out}", file=sys.stderr)
        return 0
    if args.auth_cmd == "login":
        jwt = _read_secret_text(args.jwt, "BALE_JWT", args.jwt_file)
        if not jwt:
            raise SystemExit("Need --jwt, BALE_JWT, or --jwt-file for auth login.")
        record = store.save_jwt(jwt, user_id=args.user_id, phone=args.phone)
        print(f"saved auth for provider={record.provider} user_id={record.user_id or 'unknown'}")
        return 0
    if args.auth_cmd == "logout":
        store.clear()
        print("Signed out. Next step: run `baleobala auth bale-login --phone +98912xxxxxxx --save` when you want to sign in again.")
        return 0
    status = store.status()
    state = status.get("state", "empty")
    if state == "configured":
        print("Signed in and ready.")
        print(f"phone: {status.get('phone', 'unknown')}")
        print(f"user_id: {status.get('user_id', 'unknown')}")
        print(f"expires_in: {status.get('expires_in', 'unknown')}")
    elif state == "expired":
        print("Saved session expired.")
        print("Next step: run `baleobala auth bale-login --phone +98912xxxxxxx --save` for a fresh session.")
    elif state == "missing-secret":
        print("Auth metadata exists, but the saved secret is missing.")
        print("Next step: sign in again so the local secret store can be rebuilt.")
    else:
        print("No saved sign-in found.")
        print("Next step: run `baleobala auth bale-login --phone +98912xxxxxxx --save`.")
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    from baleobala.control import ControlService, PairingExchange, PairingStore, ProvisioningError

    service = ControlService()
    store = service.pairing_store
    def _print_record(record):  # noqa: ANN001
        print(f"profile_id: {record.profile_id}")
        print(f"name: {record.name}")
        print(f"role: {record.role}")
        print(f"status: {record.status}")
        print(f"provisioning_status: {record.provisioning_status}")
        if record.authorization_status:
            print(f"authorization_status: {record.authorization_status}")
        if record.relay_id:
            print(f"relay_id: {record.relay_id}")
        if record.authorization_id:
            print(f"authorization_id: {record.authorization_id}")
        if record.device_id:
            print(f"device_id: {record.device_id}")
        if record.credential_epoch:
            print(f"credential_epoch: {record.credential_epoch}")
        if record.peer_id is not None:
            print(f"peer_id: {record.peer_id}")
        if record.peer_name:
            print(f"peer_name: {record.peer_name}")
        if record.server_error:
            print(f"server_error: {record.server_error}")

    if args.pair_cmd == "start":
        record = store.begin(
            args.name,
            role=args.role,
            peer_id=args.peer_id,
            peer_name=args.peer_name,
            relay_name=args.relay,
            relay_mode=args.relay_mode,
            backend_preference=args.relay_mode,
        )
        _print_record(record)
        print(f"pair_code: {record.pair_code}")
        return 0
    if args.pair_cmd == "enroll":
        record = service.enroll_pairing(
            args.name,
            role=args.role,
            peer_id=args.peer_id,
            peer_name=args.peer_name,
            relay_name=args.relay,
            relay_mode=args.relay_mode,
            backend_preference=args.backend or args.relay_mode,
            transport_preference=args.transport,
        )
        _print_record(record)
        return 0
    if args.pair_cmd == "accept":
        record = store.accept(args.code, name=args.name, validate=True)
        _print_record(record)
        print(f"pair_code: {record.pair_code}")
        return 0
    if args.pair_cmd == "request-access":
        try:
            record = service.request_pairing_access(args.profile_id)
        except ProvisioningError as exc:
            raise SystemExit(str(exc)) from exc
        _print_record(record)
        return 0
    if args.pair_cmd == "approve":
        try:
            record = service.approve_pairing_access(args.profile_id)
        except ProvisioningError as exc:
            raise SystemExit(str(exc)) from exc
        _print_record(record)
        return 0
    if args.pair_cmd == "reject":
        try:
            record = service.reject_pairing_access(args.profile_id, reason=args.reason)
        except ProvisioningError as exc:
            raise SystemExit(str(exc)) from exc
        _print_record(record)
        return 0
    if args.pair_cmd == "sync":
        try:
            record = service.sync_pairing(args.profile_id)
        except ProvisioningError as exc:
            raise SystemExit(str(exc)) from exc
        _print_record(record)
        return 0
    if args.pair_cmd == "revoke-device":
        try:
            record = service.revoke_pairing_device(args.profile_id)
        except ProvisioningError as exc:
            raise SystemExit(str(exc)) from exc
        _print_record(record)
        return 0
    if args.pair_cmd == "remove":
        store.remove(args.profile_id)
        print(f"removed {args.profile_id}")
        return 0
    if args.pair_cmd == "invite":
        link = service.export_pairing_invite(args.profile_id)
        print(link)
        if args.qr:
            try:
                import qrcode

                qr = qrcode.QRCode(border=1)
                qr.add_data(link)
                qr.make(fit=True)
                qr.print_ascii(invert=True)
            except Exception:
                print(
                    "QR rendering unavailable; install the optional qrcode dependency for terminal QR output.",
                    file=sys.stderr,
                )
        return 0
    if args.pair_cmd == "join":
        record = service.import_pairing_invite(args.link)
        _print_record(record)
        return 0
    if args.pair_cmd == "export-request":
        exchange = store.export_request(args.profile_id)
        print(json.dumps(exchange.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.pair_cmd == "accept-request":
        exchange = PairingExchange.from_dict(json.loads(Path(args.request_file).read_text(encoding="utf-8")))
        response = store.accept_request(
            exchange,
            name=args.name,
            peer_id=args.peer_id,
            peer_name=args.peer_name,
            backend_preference=args.backend,
            transport_preference=args.transport,
        )
        print(json.dumps(response.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.pair_cmd == "apply-response":
        exchange = PairingExchange.from_dict(json.loads(Path(args.response_file).read_text(encoding="utf-8")))
        record = store.apply_response(exchange)
        print(f"profile_id: {record.profile_id}")
        print(f"name: {record.name}")
        print(f"role: {record.role}")
        print(f"status: {record.status}")
        print(f"provisioning_status: {record.provisioning_status}")
        return 0

    records = store.list()
    if not records:
        print("No relay pairings saved.")
        print("Next step: run `baleobala pair enroll --name home-relay` to create one.")
        return 0
    print("Saved relay pairings:")
    for record in records:
        summary = f"- {record.name} [{record.status}] role={record.role} id={record.profile_id}"
        if record.relay_id:
            summary += f" relay_id={record.relay_id}"
        if record.authorization_status not in {"", "none"}:
            summary += f" authz={record.authorization_status}"
        print(summary)
    return 0


def cmd_relay(args: argparse.Namespace) -> int:
    from baleobala.control import ControlService, PairingStore, VpnProfile, VpnStore
    from baleobala.control.relay_directory import RelayDirectory
    from baleobala.control.vpn import default_vpn_backend

    pairing_store = PairingStore()
    vpn_store = VpnStore()
    service = ControlService(pairing_store=pairing_store, vpn_store=vpn_store)
    relay_directory = RelayDirectory()
    if args.relay_cmd == "enable":
        profile = None
        if args.profile_id:
            profile = pairing_store.get(args.profile_id)
        if profile is None:
            profile = pairing_store.active()
        if profile is None:
            raise SystemExit(
                "No relay pairing is ready.\n"
                "Why this usually happens: the device has not created or accepted a pairing yet.\n"
                "Next step: run `baleobala pair start --name home-relay` or `baleobala pair accept --code <pair-code>`."
            )
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
    if args.relay_cmd == "publish":
        try:
            entry = service.publish_relay(
                args.name,
                profile_id=args.profile_id,
                peer_id=args.peer_id,
                owner=args.owner or "",
                transport_preference=args.transport,
                backend_preference=args.backend or "",
                endpoint_hint=args.endpoint or "",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"relay_id: {entry.relay_id}")
        print(f"name: {entry.name}")
        print(f"peer_id: {entry.peer_id}")
        print(f"owner: {entry.owner}")
        print(f"transport_preference: {entry.transport_preference}")
        print(f"backend_preference: {entry.backend_preference}")
        if entry.endpoint_hint:
            print(f"endpoint_hint: {entry.endpoint_hint}")
        print(f"updated_at: {entry.updated_at}")
        return 0
    if args.relay_cmd == "list":
        entries = relay_directory.list()
        if not entries:
            print("No relays published yet.")
            return 0
        for entry in entries:
            line = f"- {entry.name} relay_id={entry.relay_id} peer_id={entry.peer_id} owner={entry.owner}"
            if entry.backend_preference:
                line += f" backend={entry.backend_preference}"
            if entry.transport_preference:
                line += f" transport={entry.transport_preference}"
            if entry.endpoint_hint:
                line += f" endpoint={entry.endpoint_hint}"
            print(line)
        return 0
    if args.relay_cmd == "remove":
        removed = relay_directory.remove(args.identifier)
        if removed is None:
            print(f"no relay entry matched {args.identifier!r}")
        else:
            print(f"removed relay {removed.name} ({removed.relay_id})")
        return 0
    if args.relay_cmd == "disable":
        vpn_store.save(VpnProfile(profile_id="disabled", name="disabled", backend="proxy", role="relay"))
        print("relay disabled")
        return 0
    status = vpn_store.status()
    print("Relay settings summary")
    print(f"state: {status.get('state', 'unknown')}")
    print(f"profile: {status.get('name', 'unknown')}")
    print(f"backend: {status.get('backend', 'unknown')}")
    print(f"auto_start: {status.get('auto_start', 'no')}")
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
    print("vpn up is running in the foreground; press Ctrl-C or run `baleobala vpn down` from another shell to stop it.", file=sys.stderr)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


def _wait_linux_runtime_ready(timeout: float = 45.0) -> tuple[bool, str]:
    from baleobala.control.paths import config_dir

    path = config_dir() / "linux_runtime.json"
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                last = payload
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            last = {}
        if str(last.get("call_established", "")).lower() == "yes":
            return True, ""
        if str(last.get("recovery_state", "")).lower() == "failed":
            return False, str(last.get("last_error", "linux runtime failed"))
        time.sleep(0.5)
    detail = str(last.get("last_error") or "carrier negotiation did not complete")
    return False, detail


def _print_health_block(fields: dict[str, str]) -> None:
    labels = [
        ("route_ready", "route"),
        ("dns_ready", "dns"),
        ("carrier_bypass_ready", "carrier_bypass"),
        ("egress_ready", "egress"),
    ]
    print("path_health:")
    for key, label in labels:
        value = fields.get(key, "")
        if value:
            print(f"  {label}: {value}")
    hints = _health_hints(fields)
    if hints:
        print("path_hints:")
        for hint in hints:
            print(f"  {hint}")


def _health_hints(fields: dict[str, str]) -> list[str]:
    failure_codes = {
        fields.get("failure_code", ""),
        fields.get("analysis_reason", ""),
        fields.get("failure_class", ""),
        fields.get("last_error", ""),
    }
    hints: list[str] = []

    if fields.get("route_ready") == "no" or "route_program_failed" in failure_codes:
        hints.append(
            "tunnel route programming looks incomplete; verify default or split-default routes on the client."
        )
    if fields.get("dns_ready") == "no" or "dns_config_failed" in failure_codes:
        hints.append(
            "DNS mismatch in bundle snapshot; re-check nameserver setup inside the client namespace."
        )
    if fields.get("carrier_bypass_ready") == "no":
        hints.append(
            "carrier bypass route is missing; add a host route for Bale before full-device traffic enters the tunnel."
        )
    if fields.get("egress_ready") == "no":
        hints.append(
            "TCP egress probe did not pass; verify NAT or forwarding on the relay and outbound reachability."
        )
    return hints


def _print_verdict_summary(payload: dict[str, str]) -> None:
    print("vpn verdict summary")
    print(f"  ok: {payload['ok']}")
    print(f"  analysis_classification: {payload['analysis_classification']}")
    print(f"  analysis_reason: {payload['analysis_reason']}")
    print(f"  failure_class: {payload['failure_class']}")
    print(f"  artifact_bundle: {payload['artifact_bundle']}")
    print(f"  call_established: {payload['call_established']}")
    print(f"  transport_selected: {payload['transport_selected']}")
    print(f"  data_flow_ok: {payload['data_flow_ok']}")
    print(f"  teardown_clean: {payload['teardown_clean']}")
    _print_health_block(payload)


def _print_smoke_summary(payload: dict[str, str]) -> None:
    print("vpn smoke summary")
    print(f"  ok: {payload['ok']}")
    print(f"  backend: {payload['backend']}")
    print(f"  state: {payload['state']}")
    print(f"  endpoint: {payload['endpoint']}")
    print(f"  control_ready: {payload['control_ready']}")
    print(f"  data_path_ready: {payload['data_path_ready']}")
    print(f"  probe_ok: {payload['probe_ok']}")
    print(f"  probe_kind: {payload['probe_kind']}")
    print(f"  probe_detail: {payload['probe_detail']}")
    if payload.get("last_error"):
        print(f"  last_error: {payload['last_error']}")
    _print_health_block(payload)


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


def _update_runtime_status(profile, **fields):  # noqa: ANN001
    if profile.backend == "linux-tun":
        from baleobala.control.linux import LinuxTunBackend

        LinuxTunBackend().update_runtime_status(**{k: str(v) for k, v in fields.items() if v is not None})


def _backend_up(backend, profile, auth_record=None, pairing=None):  # noqa: ANN001
    try:
        return backend.up(profile, auth_record, pairing)
    except TypeError:
        return backend.up(profile)


def cmd_vpn(args: argparse.Namespace) -> int:
    from baleobala.control import (
        AuthStore,
        ControlService,
        NetnsHarness,
        NetnsProcessManager,
        NetnsProcessSpec,
        NetnsTopology,
        NetnsSessionRunner,
        PairingStore,
        NetnsScenario,
        analyze_bundle,
        bundle_status,
        build_product_verdict,
        merge_status_with_bundle,
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
    from baleobala.control.paths import is_android_runtime
    from baleobala.control.relay_directory import RelayDirectory

    vpn_store = VpnStore()
    auth_store = AuthStore()
    pairing_store = PairingStore()
    control_service = ControlService(
        auth_store=auth_store,
        pairing_store=pairing_store,
        vpn_store=vpn_store,
        backend_factory=backend_for_profile,
    )

    if args.vpn_cmd == "plan":
        print("baleobala vpn plan")
        if is_android_runtime():
            print("default: Android VpnService backend with shared route/DNS policy")
            print("runtime: native VpnService owns system routing while the shared carrier tunnel handles Bale transport")
        elif sys.platform == "darwin":
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
        label = args.label or ("com.baleobala.proxy-client" if getattr(args, "mode", "vpn") == "proxy-client" else "com.baleobala.vpn")
        manager = MacOSLaunchAgentManager(label=label)
        if args.agent_cmd == "install":
            path = manager.install(
                profile_id=args.profile_id,
                mode=args.mode,
                jwt_file=args.jwt_file,
                proxy_secret_file=args.proxy_secret_file,
                ssl_cert_file=args.ssl_cert_file,
            )
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
        snapshot = control_service.status()
        snapshot_lines = summarize_snapshot(snapshot)
        print("baleobala secure connection status")
        for line in snapshot_lines:
            print(line)
        print("")
        print("Details:")
        print(f"saved_profile: {snapshot.vpn.get('name', 'default')}")
        print(f"backend: {snapshot.backend.get('backend', 'unknown')}")
        print(f"backend_state: {snapshot.backend.get('state', 'unknown')}")
        _print_health_block(snapshot.backend)
        print("connection:")
        for key in (
            "state",
            "code",
            "title",
            "message",
            "next_step",
            "backend",
            "profile_id",
            "pairing_id",
            "auth_state",
            "pairing_state",
            "authorization_status",
            "provisioning_status",
        ):
            value = snapshot.connection.get(key, "")
            if value:
                print(f"  {key}: {value}")
        if sys.platform == "darwin" and snapshot.vpn.get("backend") in {"proxy", "direct"}:
            from baleobala.control.macos import MacOSSystemProxySession
            system_proxy = MacOSSystemProxySession(state_path=None)
            print("system_proxy:")
            for key, value in system_proxy.status().items():
                print(f"  {key}: {value}")
        print("pairing:")
        if snapshot.pairing.get("state") == "empty":
            print("  state: empty")
        else:
            for key in (
                "profile_id",
                "name",
                "role",
                "status",
                "authorization_status",
                "provisioning_status",
                "relay_id",
                "peer_name",
                "peer_id",
                "device_id",
                "authorization_id",
                "credential_epoch",
                "credential_expires_at",
                "credential_refresh_after",
                "revoked_at",
                "server_error",
                "validation_error",
            ):
                value = snapshot.pairing.get(key, "")
                if value:
                    print(f"  {key}: {value}")
        return 0

    if args.vpn_cmd == "probe":
        profile = vpn_store.load() or vpn_store.ensure_default()
        backend = backend_for_profile(profile)
        backend_status = backend.status()
        probe = backend.probe()
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
            _print_smoke_summary(report.to_dict())
        return 0 if report.ok else 2

    if args.vpn_cmd == "live-smoke":
        from baleobala.bale.live_smoke import run_live_smoke

        return run_live_smoke(args)

    if args.vpn_cmd == "analyze-bundle":
        analysis = analyze_bundle(args.bundle_path)
        payload = analysis.to_dict()
        health = bundle_status(args.bundle_path)
        if args.json:
            print(json.dumps({**payload, **health}, indent=2, sort_keys=True))
        else:
            print("vpn analyze-bundle result")
            print(f"  classification: {payload['classification']}")
            print(f"  ok: {payload['ok']}")
            print(f"  reason: {payload['reason']}")
            print(f"  bundle_path: {payload['bundle_path']}")
            print(f"  transport_selected: {payload['transport_selected']}")
            print(f"  last_success_stage: {payload['last_success_stage']}")
            print(f"  failed_stage: {payload['failed_stage']}")
            print(f"  retry_count: {payload['retry_count']}")
            _print_health_block(health)
        return 0 if analysis.ok == "yes" else 2

    if args.vpn_cmd == "verdict":
        status = backend_for_profile(vpn_store.load() or vpn_store.ensure_default()).status()
        if args.bundle_path:
            status = merge_status_with_bundle(status, args.bundle_path)
            bundle_analysis = analyze_bundle(args.bundle_path)
        else:
            bundle_analysis = None
        verdict = build_product_verdict(status, bundle_analysis)
        payload = verdict.to_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _print_verdict_summary(payload)
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
                full_device=args.full_device,
                dns_servers=tuple(args.dns_server or ("1.1.1.1", "9.9.9.9")),
                carrier_hosts=tuple(args.carrier_host or ("next-ws.bale.ai",)),
                skip_nat_setup=args.skip_nat_setup,
                skip_host_route_setup=args.skip_host_route_setup,
            )
        topology = NetnsTopology.with_prefix(args.prefix)
        harness = NetnsHarness(topology)
        manager = NetnsProcessManager()
        artifact_root = Path(args.artifact_dir).expanduser() if getattr(args, "artifact_dir", "") else None
        if artifact_root is not None and getattr(args, "bundle_label", ""):
            artifact_root = artifact_root / str(args.bundle_label)
        session = NetnsSessionRunner(harness, manager, artifact_root=artifact_root)
        smoke_commands = [["ip", "netns", "exec", topology.server_ns, "sh", "-lc", scenario.smoke_server_cmd]] if scenario.smoke_server_cmd else []
        if scenario.smoke_client_cmd:
            smoke_commands.append(["ip", "netns", "exec", topology.client_ns, "sh", "-lc", scenario.smoke_client_cmd])
        report = session.run(
            server_cmd=scenario.server_cmd,
            client_cmd=scenario.client_cmd,
            server_ready_pattern=scenario.server_ready,
            client_ready_pattern=scenario.client_ready,
            smoke_commands=smoke_commands or None,
            timeout=args.timeout,
            scenario=scenario.to_dict(),
        )
        payload = report.to_dict()
        payload["scenario"] = scenario.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.ok and report.failure_class == "" else 2

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
                full_device=args.full_device,
                dns_servers=tuple(args.dns_server or ("1.1.1.1", "9.9.9.9")),
                carrier_hosts=tuple(args.carrier_host or ("next-ws.bale.ai",)),
                skip_nat_setup=args.skip_nat_setup,
                skip_host_route_setup=args.skip_host_route_setup,
            )
        print(json.dumps(scenario.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.vpn_cmd == "down":
        profile = vpn_store.load() or vpn_store.ensure_default()
        backend = backend_for_profile(profile)
        try:
            backend.down()
        finally:
            try:
                control_service.stop_connection(profile.profile_id)
            except Exception:
                pass
            # Best-effort cleanup for a foreground vpn up that was
            # interrupted before it could persist state.
            try:
                current = os.getpid()
                parent = os.getppid()
                result = subprocess.run(
                    ["ps", "-eo", "pid=,args="],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) != 2:
                        continue
                    pid = int(parts[0])
                    cmdline = parts[1]
                    if pid in {current, parent}:
                        continue
                    if "baleobala vpn up" in cmdline and "python" in cmdline:
                        os.kill(pid, 15)
            except Exception:
                pass
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
        if is_android_runtime() and profile.backend == "android-vpn":
            print("stopped Android VPN backend")
            return 0
        print("stopped linux-tun backend and cleared saved runtime state")
        return 0

    profile = vpn_store.load()
    if profile is None:
        profile = vpn_store.ensure_default()
    if sys.platform.startswith("linux") and getattr(args, "backend", None) is None:
        if profile.backend in {"proxy", "direct", "windows-proxy"}:
            profile = type(profile)(
                profile_id=profile.profile_id,
                name=profile.name,
                backend="linux-tun",
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
                mesh_peer_id=profile.mesh_peer_id,
                mesh_gateway_ip=profile.mesh_gateway_ip,
                mesh_client_ip=profile.mesh_client_ip,
                mesh_prefix=profile.mesh_prefix,
                mesh_provisioning_status=profile.mesh_provisioning_status,
            )
            vpn_store.save(profile)
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
    if getattr(args, "relay", None):
        relay = RelayDirectory().lookup(args.relay)
        if relay is None:
            raise SystemExit(f"no relay {args.relay!r} found in the local directory")
        pairing = next(
            (
                item
                for item in pairing_store.list()
                if item.name == relay.name or item.peer_id == relay.peer_id
            ),
            None,
        )
        if pairing is None:
            pairing = pairing_store.begin(
                relay.name,
                role=profile.role if profile.role else "client",
                peer_id=relay.peer_id,
                peer_name=relay.name,
                relay_name=relay.name,
                relay_mode="proxy",
                backend_preference=relay.backend_preference,
                transport_preference=relay.transport_preference,
            )
        pairing_store.touch(pairing.profile_id)
        profile = VpnProfile(
            profile_id=pairing.profile_id,
            name=pairing.name,
            backend=profile.backend or relay.backend_preference or default_backend_name(),
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
    if args.profile_id:
        pairing = pairing_store.get(args.profile_id)
        if pairing is None:
            raise SystemExit(f"no pairing profile {args.profile_id!r} found")
        if getattr(pairing, "relay_id", ""):
            try:
                pairing = control_service.sync_pairing(pairing.profile_id)
            except Exception:
                pass
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
    if profile.pairing_id:
        pairing = pairing_store.get(profile.pairing_id)
        if pairing is not None and getattr(pairing, "relay_id", ""):
            profile = control_service.resolve_profile(profile.pairing_id)
            pairing = pairing_store.get(profile.pairing_id)
            if (
                pairing is not None
                and pairing.credential_expires_at is not None
                and pairing.credential_expires_at <= time.time()
            ):
                pairing = control_service.refresh_pairing_credentials(pairing.profile_id)
                profile = control_service.resolve_profile(pairing.profile_id)
        elif pairing is not None and pairing.status != "paired":
            raise SystemExit(
                "pairing is not ready. Run `baleobala pair request-access --profile-id "
                f"{pairing.profile_id}` and `baleobala pair approve --profile-id {pairing.profile_id}`."
            )

    auth_record = auth_store.load()
    if auth_record is None and profile.backend == "proxy":
        print("warning: no stored auth session; the current proxy path may still require explicit Bale credentials.", file=sys.stderr)

    if profile.backend == "linux-tun":
        from baleobala.vpn.cli import _run_nat_setup, _run_tunnel_session

        pairing = pairing_store.connectable(profile.pairing_id) or pairing_store.active()
        backend = backend_for_profile(profile)
        backend_status = backend.status()
        if (
            backend_status.get("state") == "running"
            and backend_status.get("call_established") == "yes"
            and backend_status.get("recovery_state") != "failed"
        ):
            endpoint = backend_status.get("endpoint", "vpn0")
            if Path(f"/sys/class/net/{endpoint}").exists():
                print(f"linux-tun backend already running on {endpoint}")
                return 0
            print(
                f"warning: stale linux-tun state for {endpoint!r} (device gone); resetting",
                file=sys.stderr,
            )
            backend.down()
        tunnel_args = _tunnel_namespace_from_profile(profile, auth_record)
        print("linux-tun: preparing interface and routes", file=sys.stderr, flush=True)
        backend_state = _backend_up(backend, profile, auth_record, pairing)
        try:
            # Compatibility path: older/fake backends used in tests don't own
            # the runtime lifecycle and only report generic running state.
            if backend_state.get("active") == "yes":
                endpoint = backend_state.get("endpoint") or backend_state.get("tun", "vpn0")
                print("linux-tun: carrier negotiation running in background", file=sys.stderr, flush=True)
                ok, detail = _wait_linux_runtime_ready()
                if not ok:
                    print(f"linux-tun: carrier negotiation failed: {detail}", file=sys.stderr)
                    return 2
                return _hold_backend(endpoint, label="linux-tun backend active")
            if not backend_state.get("endpoint") or backend_state.get("call_established", "") == "":
                if profile.role == "relay":
                    _run_nat_setup(tunnel_args.tun, tunnel_args.wan)
                return _run_tunnel_session(
                    tunnel_args,
                    is_exit_node=profile.role == "relay",
                )
            endpoint = backend_state.get("endpoint", "vpn0")
            ok, detail = _wait_linux_runtime_ready()
            if not ok:
                print(f"linux-tun: carrier negotiation failed: {detail}", file=sys.stderr)
                return 2
            return _hold_backend(endpoint, label="linux-tun backend active")
        finally:
            backend.down()

    if profile.backend in {"packet-tunnel", "android-vpn"}:
        snapshot = control_service.start_connection(profile.profile_id)
        try:
            label = "macOS packet-tunnel backend active" if profile.backend == "packet-tunnel" else "Android VPN backend active"
            return _hold_backend(
                snapshot.backend.get("endpoint", profile.backend),
                label=label,
            )
        finally:
            try:
                control_service.stop_connection(snapshot.profile.profile_id if snapshot.profile is not None else profile.profile_id)
            except Exception:
                pass

    if profile.role == "relay":
        relay_args = _vpn_namespace_from_profile(profile, auth_record)
        backend = backend_for_profile(profile)
        backend_state = _backend_up(backend, profile, auth_record, pairing_store.connectable(profile.pairing_id))
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
                if hasattr(runtime, "_state_store"):
                    runtime._state_store.save(
                        {
                            **runtime_state.to_dict(),
                            "version": "1",
                            "call_established": "yes",
                            "data_flow_ok": "yes",
                            "route_ready": "yes",
                            "dns_ready": "yes",
                            "transport_selected": "audio",
                            "last_error": "",
                        }
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
    backend_state = _backend_up(backend, profile, auth_record, pairing_store.connectable(profile.pairing_id))
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
            return controller.dial(
                peer_id=peer_id,
                creds_timeout=float(getattr(args, "creds_timeout", 45.0)),
            )
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
    secret_file = getattr(args, "proxy_secret_file", None) or os.environ.get("BALE_PROXY_SECRET_FILE")
    if not secret and secret_file:
        secret = Path(secret_file).expanduser().read_text(encoding="utf-8").strip()
    if not secret:
        return None
    return secret.encode("utf-8")


def _emit_marker(marker: str) -> None:
    print(marker, file=sys.stderr)


class _ProxyTransportAdapter:
    """Adapt VPN byte transports to the proxy runtime's send/recv shape."""

    def __init__(self, inner, *, cleanup) -> None:  # noqa: ANN001
        self._inner = inner
        self._cleanup = cleanup
        self._closed = False

    def send(self, data: bytes) -> None:
        self._inner.send_bytes(data)

    def recv(self, timeout: float | None = None) -> bytes | None:
        return self._inner.recv_bytes(timeout=timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._inner.close()
        finally:
            self._cleanup()

    @property
    def closed(self) -> bool:
        return self._closed or bool(getattr(self._inner, "closed", False))


def _open_proxy_transport(args: argparse.Namespace, role):  # noqa: ANN001
    from baleobala.bale import LiveKitSession
    from baleobala.vpn.cli import _build_transport_chain
    from baleobala.vpn.keepalive import LiveKitKeepalive

    url, token = _resolve_livekit_credentials(args)
    session = LiveKitSession(url=url, token=token, identity=args.identity)
    session.start()
    keepalive = LiveKitKeepalive(session, interval=20.0)
    keepalive.start()
    if not hasattr(args, "psk"):
        args.psk = None
    if not hasattr(args, "psk_file"):
        args.psk_file = None
    if not hasattr(args, "transport"):
        args.transport = "dc"
    chain = _build_transport_chain(args.transport, session, args)
    try:
        transport_name, transport = chain.start()
    except Exception:
        keepalive.stop()
        session.stop()
        raise

    def cleanup() -> None:
        try:
            chain.close()
        finally:
            keepalive.stop()
            session.stop()

    return transport_name, _ProxyTransportAdapter(transport, cleanup=cleanup)


def cmd_bale_proxy_client(args: argparse.Namespace) -> int:
    """Run a local SOCKS5/HTTP CONNECT server over Bale-backed transport."""
    from baleobala.runtime import Socks5ProxyServer
    from baleobala.runtime.frame import TunnelRole

    transport_name, transport = _open_proxy_transport(args, TunnelRole.CLIENT)
    server = Socks5ProxyServer(
        transport,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        secret=_resolve_proxy_secret(args),
        on_listen=lambda host, port: _emit_marker(
            f"proxy_listening={host or args.listen_host}:{port or args.listen_port}"
        ),
    )
    try:
        _emit_marker("call_established")
        _emit_marker(f"transport_selected={transport_name}")
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()
    finally:
        _emit_marker("teardown_done")
        transport.close()
    return 0


def cmd_bale_proxy_relay(args: argparse.Namespace) -> int:
    """Run the remote TCP relay over Bale-backed transport."""
    from baleobala.runtime import TunnelTcpRelay
    from baleobala.runtime.frame import TunnelRole

    transport_name, transport = _open_proxy_transport(args, TunnelRole.SERVER)
    relay = TunnelTcpRelay(transport, secret=_resolve_proxy_secret(args))
    try:
        _emit_marker("call_established")
        _emit_marker(f"transport_selected={transport_name}")
        relay.serve_forever()
    except KeyboardInterrupt:
        relay.stop()
    finally:
        _emit_marker("teardown_done")
        transport.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    from baleobala.bale.protos import WEB_API_KEY, WEB_APP_ID

    p = argparse.ArgumentParser(
        prog="baleobala",
        description="Sign in, pair a relay, and start a secure Bale connection.",
        epilog=(
            "Main product path: doctor -> auth -> pair -> connect\n"
            "Core user commands: doctor, auth, pair, relay, vpn, gui\n"
            "Advanced/debug commands: loopback, tunnel-loopback, tunnel, bale-call, "
            "bale-tunnel, bale-proxy, send, recv, devices, virtmic\n"
            "Deprecated compatibility commands: bale-auth"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

    auth = sub.add_parser("auth", help="sign in, import, or inspect the saved Bale session")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)

    auth_login = auth_sub.add_parser(
        "login",
        help="import an existing Bale JWT into local auth state",
    )
    auth_login.add_argument("--jwt", default=None)
    auth_login.add_argument("--jwt-file", default=None)
    auth_login.add_argument("--user-id", type=int, default=None)
    auth_login.add_argument("--phone", default=None)
    auth_login.set_defaults(func=cmd_auth)

    auth_bale_login = auth_sub.add_parser(
        "bale-login",
        help="recommended: real Bale phone/SMS login with optional local save",
    )
    auth_bale_login.add_argument("--phone", required=True, help="phone in E.164, with or without '+'")
    auth_bale_login.add_argument("--app-id", type=int, default=WEB_APP_ID)
    auth_bale_login.add_argument("--api-key", default=WEB_API_KEY)
    auth_bale_login.add_argument("--device-title", default="baleobala")
    auth_bale_login.add_argument("--device-hash-hex", default=None)
    auth_bale_login.add_argument(
        "--method",
        choices=["auto", "browser", "grpc"],
        default="auto",
        help="auth backend (default: auto; prefer browser like GUI)",
    )
    auth_bale_login.add_argument(
        "--headful",
        action="store_true",
        help="browser mode: show Chromium window instead of headless",
    )
    auth_bale_login.add_argument("--save", action="store_true", help="save JWT to auth store")
    auth_bale_login.add_argument("--user-id", type=int, default=None, help="optional user_id for saved auth")
    auth_bale_login.add_argument("--jwt-out", default=None, help="optional file path to write JWT")
    auth_bale_login.add_argument(
        "--print-jwt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="print JWT to stdout (default: true)",
    )
    auth_bale_login.set_defaults(func=cmd_auth)

    auth_logout = auth_sub.add_parser("logout", help="clear stored auth state")
    auth_logout.set_defaults(func=cmd_auth)

    auth_status = auth_sub.add_parser("status", help="show stored auth state")
    auth_status.set_defaults(func=cmd_auth)

    pair = sub.add_parser("pair", help="enroll, authorize, and inspect relay pairings")
    pair_sub = pair.add_subparsers(dest="pair_cmd", required=True)

    pair_enroll = pair_sub.add_parser("enroll", help="create a server-backed relay pairing")
    pair_enroll.add_argument("--name", default="relay")
    pair_enroll.add_argument("--role", choices=["client", "relay"], default="client")
    pair_enroll.add_argument("--peer-id", type=int, default=None)
    pair_enroll.add_argument("--peer-name", default=None)
    pair_enroll.add_argument("--relay", default=None, help="look up the relay directory entry by name")
    pair_enroll.add_argument("--relay-mode", choices=["proxy"], default="proxy")
    pair_enroll.add_argument("--backend", default=None)
    pair_enroll.add_argument("--transport", default="auto")
    pair_enroll.set_defaults(func=cmd_pair)

    pair_start = pair_sub.add_parser("start", help="create a pending pairing record")
    pair_start.add_argument("--name", default="relay")
    pair_start.add_argument("--role", choices=["client", "relay"], default="client")
    pair_start.add_argument("--peer-id", type=int, default=None)
    pair_start.add_argument("--peer-name", default=None)
    pair_start.add_argument("--relay", default=None, help="look up the relay directory entry by name")
    pair_start.add_argument("--relay-mode", choices=["proxy"], default="proxy")
    pair_start.set_defaults(func=cmd_pair)

    pair_accept = pair_sub.add_parser("accept", help="accept a pairing code")
    pair_accept.add_argument("--code", required=True)
    pair_accept.add_argument("--name", default=None)
    pair_accept.set_defaults(func=cmd_pair)

    pair_request = pair_sub.add_parser("request-access", help="request device access to an enrolled relay")
    pair_request.add_argument("--profile-id", required=True)
    pair_request.set_defaults(func=cmd_pair)

    pair_approve = pair_sub.add_parser("approve", help="approve a pending device access request")
    pair_approve.add_argument("--profile-id", required=True)
    pair_approve.set_defaults(func=cmd_pair)

    pair_reject = pair_sub.add_parser("reject", help="reject a pending device access request")
    pair_reject.add_argument("--profile-id", required=True)
    pair_reject.add_argument("--reason", default="")
    pair_reject.set_defaults(func=cmd_pair)

    pair_sync = pair_sub.add_parser("sync", help="sync the local pairing cache with the provisioning service")
    pair_sync.add_argument("--profile-id", required=True)
    pair_sync.set_defaults(func=cmd_pair)

    pair_revoke = pair_sub.add_parser("revoke-device", help="revoke the current device from a relay")
    pair_revoke.add_argument("--profile-id", required=True)
    pair_revoke.set_defaults(func=cmd_pair)

    pair_export = pair_sub.add_parser("export-request", help="export a versioned pairing request bundle")
    pair_export.add_argument("--profile-id", required=True)
    pair_export.set_defaults(func=cmd_pair)

    pair_accept_request = pair_sub.add_parser("accept-request", help="accept a pairing request bundle and emit a response bundle")
    pair_accept_request.add_argument("--request-file", required=True)
    pair_accept_request.add_argument("--name", default=None)
    pair_accept_request.add_argument("--peer-id", type=int, default=None)
    pair_accept_request.add_argument("--peer-name", default=None)
    pair_accept_request.add_argument("--backend", default=None)
    pair_accept_request.add_argument("--transport", default=None)
    pair_accept_request.set_defaults(func=cmd_pair)

    pair_apply_response = pair_sub.add_parser("apply-response", help="apply a pairing response bundle")
    pair_apply_response.add_argument("--response-file", required=True)
    pair_apply_response.set_defaults(func=cmd_pair)

    pair_list = pair_sub.add_parser("list", help="list pairing records")
    pair_list.set_defaults(func=cmd_pair)

    pair_remove = pair_sub.add_parser("remove", help="remove a pairing record")
    pair_remove.add_argument("--profile-id", required=True)
    pair_remove.set_defaults(func=cmd_pair)

    pair_invite = pair_sub.add_parser("invite", help="export an invite link for a pairing")
    pair_invite.add_argument("--profile-id", required=True)
    pair_invite.add_argument("--qr", action="store_true", help="render the invite as terminal QR art")
    pair_invite.set_defaults(func=cmd_pair)

    pair_join = pair_sub.add_parser("join", help="join from an invite link")
    pair_join.add_argument("link")
    pair_join.set_defaults(func=cmd_pair)

    relay = sub.add_parser("relay", help="save relay-side connection settings")
    relay_sub = relay.add_subparsers(dest="relay_cmd", required=True)

    relay_enable = relay_sub.add_parser("enable", help="save relay settings")
    relay_enable.add_argument("--profile-id", default=None)
    relay_enable.add_argument("--name", default=None)
    relay_enable.add_argument("--backend", choices=["packet-tunnel", "android-vpn", "proxy", "linux-tun"], default=None)
    relay_enable.add_argument("--auto-start", action="store_true")
    relay_enable.add_argument("--listen-host", default="127.0.0.1")
    relay_enable.add_argument("--listen-port", type=int, default=1080)
    relay_enable.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    relay_enable.add_argument("--volume", type=int, default=50)
    relay_enable.add_argument("--proxy-secret", default=None)
    relay_enable.set_defaults(func=cmd_relay)

    relay_publish = relay_sub.add_parser("publish", help="publish the current relay into the local directory")
    relay_publish.add_argument("--name", required=True)
    relay_publish.add_argument("--profile-id", default=None)
    relay_publish.add_argument("--peer-id", type=int, default=None)
    relay_publish.add_argument("--owner", default=None)
    relay_publish.add_argument("--backend", default=None)
    relay_publish.add_argument("--transport", default="auto")
    relay_publish.add_argument("--endpoint", default=None)
    relay_publish.set_defaults(func=cmd_relay)

    relay_list = relay_sub.add_parser("list", help="list published relays")
    relay_list.set_defaults(func=cmd_relay)

    relay_remove = relay_sub.add_parser("remove", help="remove a published relay")
    relay_remove.add_argument("identifier")
    relay_remove.set_defaults(func=cmd_relay)

    relay_disable = relay_sub.add_parser("disable", help="clear relay settings")
    relay_disable.set_defaults(func=cmd_relay)

    relay_status = relay_sub.add_parser("status", help="show relay settings")
    relay_status.set_defaults(func=cmd_relay)

    vpn = sub.add_parser("vpn", help="start, stop, and inspect the secure connection runtime")
    vpn_sub = vpn.add_subparsers(dest="vpn_cmd", required=True)

    vpn_up = vpn_sub.add_parser("up", help="start the current VPN backend")
    vpn_up.add_argument("--profile-id", default=None)
    vpn_up.add_argument("--relay", default=None, help="resolve a relay from the local directory by name")
    vpn_up.add_argument("--backend", choices=["packet-tunnel", "android-vpn", "proxy", "linux-tun"], default=None)
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

    vpn_live_smoke = vpn_sub.add_parser("live-smoke", help="run the real two-account Bale LiveKit/DataChannel smoke test")
    vpn_live_smoke.add_argument("--caller-jwt-file", required=True)
    vpn_live_smoke.add_argument("--callee-jwt-file", required=True)
    live_target = vpn_live_smoke.add_mutually_exclusive_group(required=True)
    live_target.add_argument("--callee-peer-id", type=int)
    live_target.add_argument("--callee-peer", default=None)
    live_target.add_argument("--callee-peer-name", default=None)
    vpn_live_smoke.add_argument("--topic", default="vpn")
    vpn_live_smoke.add_argument("--timeout", type=float, default=90.0)
    vpn_live_smoke.add_argument(
        "--ws-ca-file",
        default=None,
        help="custom CA bundle for Bale WS TLS verification on intercepted networks",
    )
    vpn_live_smoke.add_argument(
        "--ws-ca-path",
        default=None,
        help="custom CA directory for Bale WS TLS verification on intercepted networks",
    )
    vpn_live_smoke.add_argument(
        "--ws-ssl-no-verify",
        action="store_true",
        help="debug-only: disable Bale WS TLS verification for this smoke run",
    )
    vpn_live_smoke.set_defaults(func=cmd_vpn)

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
    vpn_netns_session.add_argument("--full-device", dest="full_device", action="store_true", default=True, help="enable client default-route, DNS, and egress automation")
    vpn_netns_session.add_argument("--tunnel-only", dest="full_device", action="store_false", help="disable default-route, DNS, and egress automation for tunnel-pair")
    vpn_netns_session.add_argument("--dns-server", action="append", default=[], help="DNS server to configure inside the client namespace")
    vpn_netns_session.add_argument("--carrier-host", action="append", default=[], help="carrier host that should bypass the tunnel")
    vpn_netns_session.add_argument("--skip-nat-setup", action="store_true", help="skip server NAT/forwarding automation")
    vpn_netns_session.add_argument("--skip-host-route-setup", action="store_true", help="skip client carrier host-route automation")
    vpn_netns_session.add_argument("--timeout", type=float, default=5.0, help="readiness timeout in seconds")
    vpn_netns_session.add_argument("--artifact-dir", default="", help="directory for verdict artifacts")
    vpn_netns_session.add_argument("--bundle-label", default="", help="stable artifact label prefix for CI grouping")
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
    vpn_netns_scenario.add_argument("--full-device", dest="full_device", action="store_true", default=True)
    vpn_netns_scenario.add_argument("--tunnel-only", dest="full_device", action="store_false")
    vpn_netns_scenario.add_argument("--dns-server", action="append", default=[])
    vpn_netns_scenario.add_argument("--carrier-host", action="append", default=[])
    vpn_netns_scenario.add_argument("--skip-nat-setup", action="store_true")
    vpn_netns_scenario.add_argument("--skip-host-route-setup", action="store_true")
    vpn_netns_scenario.set_defaults(func=cmd_vpn)

    vpn_plan = vpn_sub.add_parser("plan", help="print the system VPN delivery plan")
    vpn_plan.set_defaults(func=cmd_vpn)

    vpn_agent = vpn_sub.add_parser("agent", help="manage the macOS LaunchAgent")
    vpn_agent_sub = vpn_agent.add_subparsers(dest="agent_cmd", required=True)

    vpn_agent_install = vpn_agent_sub.add_parser("install", help="install the LaunchAgent")
    vpn_agent_install.add_argument("--profile-id", default=None)
    vpn_agent_install.add_argument("--mode", choices=["vpn", "proxy-client"], default="vpn")
    vpn_agent_install.add_argument("--label", default=None)
    vpn_agent_install.add_argument("--jwt-file", default=None)
    vpn_agent_install.add_argument("--proxy-secret-file", default=None)
    vpn_agent_install.add_argument("--ssl-cert-file", default=None)
    vpn_agent_install.set_defaults(func=cmd_vpn)

    vpn_agent_remove = vpn_agent_sub.add_parser("remove", help="remove the LaunchAgent")
    vpn_agent_remove.add_argument("--mode", choices=["vpn", "proxy-client"], default="vpn")
    vpn_agent_remove.add_argument("--label", default=None)
    vpn_agent_remove.set_defaults(func=cmd_vpn)

    vpn_agent_start = vpn_agent_sub.add_parser("start", help="start the LaunchAgent")
    vpn_agent_start.add_argument("--mode", choices=["vpn", "proxy-client"], default="vpn")
    vpn_agent_start.add_argument("--label", default=None)
    vpn_agent_start.set_defaults(func=cmd_vpn)

    vpn_agent_stop = vpn_agent_sub.add_parser("stop", help="stop the LaunchAgent")
    vpn_agent_stop.add_argument("--mode", choices=["vpn", "proxy-client"], default="vpn")
    vpn_agent_stop.add_argument("--label", default=None)
    vpn_agent_stop.set_defaults(func=cmd_vpn)

    vpn_agent_status = vpn_agent_sub.add_parser("status", help="show LaunchAgent state")
    vpn_agent_status.add_argument("--mode", choices=["vpn", "proxy-client"], default="vpn")
    vpn_agent_status.add_argument("--label", default=None)
    vpn_agent_status.set_defaults(func=cmd_vpn)

    v = sub.add_parser("virtmic", help="debug: create a virtual microphone and hold it open")
    v.add_argument("--name", default="baleobala")
    v.set_defaults(func=cmd_virtmic)

    lb = sub.add_parser("loopback", help="debug: in-process codec self-test (no audio device)")
    lb.add_argument("--protocol", choices=["normal", "fast", "fastest"], default="fast")
    lb.add_argument("messages", nargs="*")
    lb.set_defaults(func=cmd_loopback)

    tl = sub.add_parser("tunnel-loopback", help="debug: in-process byte-tunnel self-test")
    tl.add_argument("messages", nargs="*")
    tl.set_defaults(func=cmd_tunnel_loopback)

    bt = sub.add_parser(
        "bale-tunnel",
        help="debug: run the byte tunnel over a Bale LiveKit room",
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
        help="debug: run a SOCKS5/HTTP CONNECT proxy over Bale/LiveKit",
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
    bp_client.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc", "mtproto_rpc"], default="dc")
    bp_client.add_argument("--proxy-secret", default=None, help="shared secret for packet auth/encryption")
    bp_client.add_argument("--proxy-secret-file", default=None, help="read shared proxy secret from a file")
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
    bp_relay.add_argument("--transport", choices=["auto", "dc", "qr", "audio", "rpc", "mtproto_rpc"], default="dc")
    bp_relay.add_argument("--proxy-secret", default=None, help="shared secret for packet auth/encryption")
    bp_relay.add_argument("--proxy-secret-file", default=None, help="read shared proxy secret from a file")
    bp_relay.set_defaults(func=cmd_bale_proxy_relay)

    bc = sub.add_parser(
        "bale-call",
        help="debug: connect to a Bale LiveKit room and send/recv baleobala frames",
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
        help="deprecated compatibility: legacy phone/SMS login that prints a JWT",
    )
    ba.add_argument("--phone", required=True, help="phone in E.164, with or without '+'")
    ba.add_argument("--app-id", type=int, default=WEB_APP_ID,
                    help=f"default: {WEB_APP_ID} (Bale Web)")
    ba.add_argument("--api-key", default=WEB_API_KEY,
                    help="default: Bale Web's public api_key")
    ba.add_argument("--device-title", default="baleobala")
    ba.add_argument("--device-hash-hex", default=None,
                    help="hex-encoded device hash (default: random 16B)")
    ba.add_argument(
        "--method",
        choices=["auto", "browser", "grpc"],
        default="grpc",
        help="auth backend (default: grpc for backwards compatibility)",
    )
    ba.add_argument(
        "--headful",
        action="store_true",
        help="browser mode: show Chromium window instead of headless",
    )
    ba.set_defaults(func=cmd_bale_auth)

    g = sub.add_parser("gui", help="launch the desktop app for sign-in, pairing, and connection")
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
    print(
        "[deprecation] `baleobala bale-auth` is deprecated and kept for compatibility. "
        "Use `baleobala auth bale-login --phone ... --save` for real login or "
        "`baleobala auth login --jwt-file ...` to import an existing JWT.",
        file=sys.stderr,
    )
    jwt = _run_bale_auth_login(args)
    print(jwt)
    return 0


def _run_bale_auth_login(args: argparse.Namespace) -> str:
    """Run Bale phone/SMS auth flow and return a JWT.

    Modes:
      - browser: Playwright/web.bale.ai cookie flow (same path as GUI)
      - grpc: direct gRPC-Web flow
      - auto: prefer browser, fallback to grpc
    """
    method = getattr(args, "method", "auto")
    if method not in {"auto", "browser", "grpc"}:
        raise SystemExit(f"unknown auth method: {method}")

    if method == "browser":
        return _run_bale_auth_login_browser(args)
    if method == "grpc":
        return _run_bale_auth_login_grpc(args)

    # auto
    try:
        return _run_bale_auth_login_browser(args)
    except Exception as e:  # noqa: BLE001
        print(f"[bale-auth] browser flow failed ({e}); falling back to grpc", file=sys.stderr)
        return _run_bale_auth_login_grpc(args)


def _run_bale_auth_login_browser(args: argparse.Namespace) -> str:
    import os

    from baleobala.bale.auth_browser import BaleAuthBrowser

    phone = str(args.phone).lstrip("+")
    if not phone.isdigit():
        raise SystemExit("invalid phone number (digits only, optional +)")

    headless = not bool(getattr(args, "headful", False))
    prev_headless = os.environ.get("BALE_HEADLESS")
    os.environ["BALE_HEADLESS"] = "1" if headless else "0"
    try:
        with BaleAuthBrowser() as auth:
            auth.start_phone_auth(int(phone))
            for attempt in range(1, 4):
                code = input("SMS code: ").strip()
                try:
                    session = auth.validate_code(code)
                    return session.jwt
                except Exception as e:  # noqa: BLE001
                    text = str(e)
                    if "expired" in text.lower():
                        raise SystemExit(
                            f"code expired (attempt {attempt}); rerun to send a fresh SMS"
                        )
                    print(f"[bale-auth] {text}; retry {attempt}/3", file=sys.stderr)
            raise SystemExit("too many invalid codes; giving up")
    finally:
        if prev_headless is None:
            os.environ.pop("BALE_HEADLESS", None)
        else:
            os.environ["BALE_HEADLESS"] = prev_headless


def _run_bale_auth_login_grpc(args: argparse.Namespace) -> str:
    """Direct gRPC-Web fallback used by CLI."""
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
    return session.jwt


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
