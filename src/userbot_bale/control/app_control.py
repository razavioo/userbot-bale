"""JSON control bridge for the native macOS app.

The Swift app talks to this module with structured JSON. Human CLI text stays
out of the app boundary so the product UI can evolve independently from
terminal output.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from userbot_bale.control.paths import app_dir, data_dir, shared_container_dir
from userbot_bale.control.probe import probe_endpoint
from userbot_bale.control.service import ConnectionSnapshot, ControlService, ControlSnapshot
from userbot_bale.control.store import JsonStore
from userbot_bale.control.vpn import VpnProfile, default_vpn_backend


POLICY_VERSION = 1
POLICY_FILE_NAME = "macos_network_policy.json"
AUTH_FLOW_FILE_NAME = "macos_auth_flow.json"
KILL_SWITCH_MODES = {"off", "on", "lockdown"}
DNS_MODES = {"system", "custom"}
TRANSPORT_PREFERENCES = {"auto", "dc", "audio", "rpc", "qr", "mtproto_rpc"}
LOG_SUFFIXES = {".json", ".jsonl", ".log", ".txt"}
TEXT_REDACTIONS = (
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<redacted-jwt>"),
    (re.compile(r"(?i)(access[_-]?token|jwt|secret|password)([\"'\s:=]+)([^\"'\s,}]+)"), r"\1\2<redacted>"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]+"), r"\1<redacted>"),
)


@dataclass(frozen=True)
class NetworkPolicy:
    """User-facing network policy shared by Swift, Python, and the tunnel."""

    policy_version: int = POLICY_VERSION
    kill_switch_mode: str = "off"
    auto_connect: bool = False
    launch_at_login: bool = False
    allow_lan: bool = True
    dns_mode: str = "custom"
    custom_dns_servers: list[str] = field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    trusted_wifi_action: str = "ask"
    untrusted_wifi_action: str = "connect"
    trusted_wifi_networks: list[str] = field(default_factory=list)
    split_tunnel_mode: str = "off"
    split_tunnel_exclusions: list[str] = field(default_factory=list)
    transport_preference: str = "auto"
    fallback_proxy_enabled: bool = False
    pause_until: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NetworkPolicy":
        data = data or {}
        return cls(
            policy_version=int(data.get("policy_version", data.get("policyVersion", POLICY_VERSION))),
            kill_switch_mode=str(data.get("kill_switch_mode", data.get("killSwitchMode", "off")) or "off"),
            auto_connect=bool(data.get("auto_connect", data.get("autoConnect", False))),
            launch_at_login=bool(data.get("launch_at_login", data.get("launchAtLogin", False))),
            allow_lan=bool(data.get("allow_lan", data.get("allowLAN", True))),
            dns_mode=str(data.get("dns_mode", data.get("dnsMode", "custom")) or "custom"),
            custom_dns_servers=_string_list(data.get("custom_dns_servers", data.get("customDNSServers", ["1.1.1.1", "9.9.9.9"]))),
            trusted_wifi_action=str(data.get("trusted_wifi_action", data.get("trustedWiFiAction", "ask")) or "ask"),
            untrusted_wifi_action=str(data.get("untrusted_wifi_action", data.get("untrustedWiFiAction", "connect")) or "connect"),
            trusted_wifi_networks=_string_list(data.get("trusted_wifi_networks", data.get("trustedWiFiNetworks", []))),
            split_tunnel_mode=str(data.get("split_tunnel_mode", data.get("splitTunnelMode", "off")) or "off"),
            split_tunnel_exclusions=_string_list(data.get("split_tunnel_exclusions", data.get("splitTunnelExclusions", []))),
            transport_preference=str(data.get("transport_preference", data.get("transportPreference", "auto")) or "auto"),
            fallback_proxy_enabled=bool(data.get("fallback_proxy_enabled", data.get("fallbackProxyEnabled", False))),
            pause_until=_optional_float(data.get("pause_until", data.get("pauseUntil"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_paused(self, *, now: float | None = None) -> bool:
        if self.pause_until is None:
            return False
        return self.pause_until > (now if now is not None else time.time())


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _optional_float(value: Any) -> float | None:
    if value in {None, "", "none"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def network_policy_path() -> Path:
    return shared_container_dir() / POLICY_FILE_NAME


def auth_flow_path() -> Path:
    return shared_container_dir() / AUTH_FLOW_FILE_NAME


def load_network_policy() -> NetworkPolicy:
    payload = JsonStore(network_policy_path()).load(default=None)
    if isinstance(payload, dict):
        return NetworkPolicy.from_dict(payload)
    return NetworkPolicy()


def save_network_policy(policy: NetworkPolicy) -> NetworkPolicy:
    JsonStore(network_policy_path()).save(policy.to_dict())
    return policy


def load_auth_flow() -> dict[str, Any]:
    payload = JsonStore(auth_flow_path()).load(default={})
    return payload if isinstance(payload, dict) else {}


def save_auth_flow(payload: dict[str, Any]) -> dict[str, Any]:
    JsonStore(auth_flow_path()).save(payload)
    return payload


def clear_auth_flow() -> None:
    try:
        auth_flow_path().unlink()
    except FileNotFoundError:
        pass


def validate_network_policy(policy: NetworkPolicy) -> list[str]:
    errors: list[str] = []
    if policy.kill_switch_mode not in KILL_SWITCH_MODES:
        errors.append("Choose a valid kill switch mode.")
    if policy.dns_mode not in DNS_MODES:
        errors.append("DNS blocklist modes need the resolver/blocklist helper before they can be enabled.")
    if policy.transport_preference not in TRANSPORT_PREFERENCES:
        errors.append("Choose a valid transport preference.")
    if policy.dns_mode == "custom":
        if not policy.custom_dns_servers:
            errors.append("Custom DNS needs at least one DNS server.")
        for server in policy.custom_dns_servers:
            try:
                ipaddress.ip_address(server)
            except ValueError:
                errors.append(f"{server} is not a valid DNS server address.")
    if policy.launch_at_login:
        errors.append("Launch at login requires the signed app helper before it can be enabled.")
    if policy.fallback_proxy_enabled:
        errors.append("Proxy fallback requires the signed helper before it can be enabled.")
    if policy.split_tunnel_mode != "off" or policy.split_tunnel_exclusions:
        errors.append("Split tunneling requires the signed helper before it can enforce app exclusions.")
    if (
        policy.trusted_wifi_networks
        or policy.trusted_wifi_action != "ask"
        or policy.untrusted_wifi_action != "connect"
    ):
        errors.append("Trusted Wi-Fi rules require the signed network monitor helper.")
    return errors


def packet_tunnel_policy_payload(policy: NetworkPolicy | None = None) -> dict[str, object]:
    policy = policy or load_network_policy()
    return {
        "networkPolicyVersion": policy.policy_version,
        "killSwitchMode": policy.kill_switch_mode,
        "autoConnect": policy.auto_connect,
        "launchAtLogin": policy.launch_at_login,
        "allowLAN": policy.allow_lan,
        "dnsMode": policy.dns_mode,
        "customDNSServers": policy.custom_dns_servers,
        "trustedWiFiAction": policy.trusted_wifi_action,
        "untrustedWiFiAction": policy.untrusted_wifi_action,
        "trustedWiFiNetworks": policy.trusted_wifi_networks,
        "splitTunnelMode": policy.split_tunnel_mode,
        "splitTunnelExclusions": policy.split_tunnel_exclusions,
        "transportPreference": policy.transport_preference,
        "fallbackProxyEnabled": policy.fallback_proxy_enabled,
        "pauseUntil": policy.pause_until or 0,
    }


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "data": self.data,
        }


class AppControlBridge:
    """Small command dispatcher used by the native app and tests."""

    def __init__(
        self,
        service: ControlService | None = None,
        *,
        auth_factory: Any | None = None,
    ) -> None:
        self.service = service or ControlService()
        self._auth_factory = auth_factory
        self._auth_sessions: dict[str, Any] = {}

    def handle(self, request: dict[str, Any]) -> ActionResult:
        command = str(request.get("command", request.get("type", "status"))).strip()
        payload = request.get("payload")
        if not isinstance(payload, dict):
            payload = {key: value for key, value in request.items() if key not in {"command", "type"}}

        try:
            if command == "status":
                return ActionResult(True, data=self.app_state())
            if command == "diagnostics":
                return ActionResult(True, data=self.diagnostics())
            if command == "setNetworkPolicy":
                policy = NetworkPolicy.from_dict(payload.get("policy") if isinstance(payload.get("policy"), dict) else payload)
                errors = validate_network_policy(policy)
                if errors:
                    return ActionResult(False, " ".join(errors), data=self.app_state())
                save_network_policy(policy)
                return ActionResult(True, "Network policy saved.", data=self.app_state())
            if command == "pause":
                return self.pause(payload)
            if command == "connect":
                return self.connect(payload)
            if command == "disconnect":
                return self.disconnect(payload)
            if command == "pair":
                return self.pair(payload)
            if command == "syncPairing":
                return self.sync_pairing(payload)
            if command == "startAuth":
                return self.start_auth(payload)
            if command == "verifyAuth":
                return self.verify_auth(payload)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, friendly_bridge_error(str(exc)), data=self.app_state())

        return ActionResult(False, f"Unknown app-control command: {command}", data=self.app_state())

    def app_state(self) -> dict[str, Any]:
        snapshot = self.service.status()
        policy = load_network_policy()
        state = connection_state(snapshot, policy)
        signing = signing_payload()
        return {
            "schemaVersion": 1,
            "generatedAt": time.time(),
            "connectionState": state,
            "headline": headline_for_state(state, snapshot),
            "detail": detail_for_state(state, snapshot, policy),
            "canConnect": can_connect(snapshot, policy),
            "canDisconnect": snapshot.backend.get("state") in {"running", "degraded"},
            "auth": snapshot.auth,
            "vpn": snapshot.vpn,
            "pairing": snapshot.pairing,
            "mesh": snapshot.mesh,
            "backend": snapshot.backend,
            "connection": snapshot.connection,
            "readiness": readiness_items(snapshot),
            "relays": relay_summaries(snapshot),
            "networkPolicy": policy.to_dict(),
            "codeSigning": signing,
        }

    def diagnostics(self) -> dict[str, Any]:
        snapshot = self.service.status()
        policy = load_network_policy()
        try:
            from userbot_bale.control.macos import code_signing_status, load_tunnel_profile, tunnel_profile_path

            tunnel_profile = load_tunnel_profile() or {}
            tunnel_profile_file = str(tunnel_profile_path())
        except Exception:  # noqa: BLE001
            tunnel_profile = {}
            tunnel_profile_file = ""
        signing = signing_payload()
        socket_path = shared_container_dir() / str(tunnel_profile.get("carrierSocketPath") or "carrier_tunnel.sock")
        socket_probe = probe_endpoint(f"unix://{socket_path}", timeout=0.2) if socket_path.exists() else None
        return {
            "schemaVersion": 1,
            "generatedAt": time.time(),
            "status": self.app_state(),
            "networkPolicy": policy.to_dict(),
            "tunnelProfilePath": tunnel_profile_file,
            "tunnelProfile": _redact(tunnel_profile),
            "codeSigning": signing,
            "carrierSocketPath": str(socket_path),
            "carrierSocketExists": socket_path.exists(),
            "carrierSocketReachable": socket_probe.ok if socket_probe is not None else False,
            "carrierSocketState": socket_probe.detail if socket_probe is not None else "missing",
            "redactedLogs": redacted_log_entries(),
            "lastError": snapshot.backend.get("last_error", "") or snapshot.connection.get("message", ""),
        }

    def connect(self, payload: dict[str, Any]) -> ActionResult:
        profile_id = str(payload.get("profileID", payload.get("profile_id", "")) or "") or None
        snapshot = self.service.start_connection(profile_id)
        return ActionResult(True, "Connection requested.", data=_connection_snapshot_payload(snapshot))

    def disconnect(self, payload: dict[str, Any]) -> ActionResult:
        profile_id = str(payload.get("profileID", payload.get("profile_id", "")) or "") or None
        snapshot = self.service.stop_connection(profile_id)
        return ActionResult(True, "Connection stopped.", data=_connection_snapshot_payload(snapshot))

    def pause(self, payload: dict[str, Any]) -> ActionResult:
        seconds = int(payload.get("seconds", 900) or 900)
        policy = load_network_policy()
        save_network_policy(NetworkPolicy.from_dict({**policy.to_dict(), "pause_until": time.time() + max(seconds, 1)}))
        try:
            self.service.stop_connection()
        except Exception:
            pass
        return ActionResult(True, "Connection paused.", data=self.app_state())

    def pair(self, payload: dict[str, Any]) -> ActionResult:
        name = str(payload.get("name") or "home-relay").strip() or "home-relay"
        role = str(payload.get("role") or "client").strip() or "client"
        pair_code = str(payload.get("pairCode", payload.get("pair_code", "")) or "").strip()
        backend = str(payload.get("backend") or default_vpn_backend()).strip() or default_vpn_backend()
        transport = str(
            payload.get("transportPreference", payload.get("transport_preference", load_network_policy().transport_preference))
            or "auto"
        )
        if pair_code:
            record = self.service.accept_pairing(pair_code, name=name)
        else:
            record = self.service.begin_pairing(name, role=role, backend_preference=backend, transport_preference=transport)
        profile = self.service.ensure_profile()
        self.service.save_profile(
            VpnProfile(
                profile_id=record.profile_id,
                name=record.name,
                backend=record.backend_preference or backend,
                role=record.role,
                pairing_id=record.profile_id,
                peer_id=record.peer_id,
                peer_name=record.peer_name,
                answer=record.role == "relay",
                auto_start=profile.auto_start,
                listen_host=profile.listen_host,
                listen_port=profile.listen_port,
                protocol=profile.protocol,
                volume=profile.volume,
                proxy_secret=profile.proxy_secret,
            )
        )
        return ActionResult(True, "Relay pairing saved.", data={"pairing": asdict(record), "status": self.app_state()})

    def sync_pairing(self, payload: dict[str, Any]) -> ActionResult:
        profile_id = str(payload.get("profileID", payload.get("profile_id", "")) or "")
        if not profile_id:
            pairing = self.service.load_pairing()
            profile_id = pairing.profile_id if pairing is not None else ""
        if not profile_id:
            return ActionResult(False, "No pairing profile is available to sync.", data=self.app_state())
        record = self.service.sync_pairing(profile_id)
        return ActionResult(True, "Relay pairing synchronized.", data={"pairing": asdict(record), "status": self.app_state()})

    def start_auth(self, payload: dict[str, Any]) -> ActionResult:
        phone = str(payload.get("phone", "")).strip()
        if not phone:
            return ActionResult(False, "Enter a phone number to sign in.", data=self.app_state())
        code_channel = str(
            payload.get("codeChannel", payload.get("code_channel", "")) or ""
        ).strip().lower()
        send_code_type: int | None = None
        if code_channel in {"baleonly", "bale"}:
            from userbot_bale.bale.protos import SEND_CODE_TYPE_BALEONLY

            send_code_type = SEND_CODE_TYPE_BALEONLY
        session_id = str(int(time.time() * 1000))
        auth = self._make_auth(session_id=session_id)
        from userbot_bale.bale.auth import BaleCodeChannelUnavailable

        try:
            if send_code_type is not None:
                tx = auth.start_phone_auth(
                    int(phone.lstrip("+")),
                    send_code_type=send_code_type,
                )
            else:
                tx = auth.start_phone_auth(int(phone.lstrip("+")))
        except BaleCodeChannelUnavailable as exc:
            return ActionResult(
                False,
                "The code could not be delivered through the Bale app. "
                "Sign in to Bale on your phone with this number, then try again. "
                f"({exc})",
                data=self.app_state(),
            )
        if self._auth_factory is not None:
            self._auth_sessions[str(tx)] = auth
        save_auth_flow(
            {
                "transaction_hash": str(tx),
                "session_id": session_id,
                "phone": phone,
                "code_channel": code_channel or "default",
                "created_at": time.time(),
            }
        )
        if send_code_type is not None:
            message = "Code sent to your Bale app."
        else:
            message = "SMS code sent."
        return ActionResult(
            True,
            message,
            data={
                "transactionHash": str(tx),
                "sessionID": session_id,
                "codeChannel": code_channel or "default",
            },
        )

    def verify_auth(self, payload: dict[str, Any]) -> ActionResult:
        flow = load_auth_flow()
        tx = str(payload.get("transactionHash", payload.get("transaction_hash", flow.get("transaction_hash", ""))) or "")
        code = str(payload.get("code", "") or "").strip()
        auth = self._auth_sessions.get(tx)
        if auth is None:
            session_id = str(flow.get("session_id", "") or "")
            if not tx or not session_id:
                return ActionResult(False, "Auth session expired. Request a new SMS code.", data=self.app_state())
            auth = self._make_auth(session_id=session_id)
        session = auth.validate_code(code, transaction_hash=tx)
        phone = str(payload.get("phone", flow.get("phone", "")) or "") or None
        record = self.service.save_auth_jwt(session.jwt, phone=phone)
        clear_auth_flow()
        return ActionResult(True, "Signed in.", data={"auth": asdict(record), "status": self.app_state()})

    def _make_auth(self, *, session_id: str | None = None) -> Any:
        if self._auth_factory is not None:
            return self._auth_factory()
        from userbot_bale.bale.auth import BaleAuth

        return BaleAuth(session_id=session_id)


def connection_state(snapshot: ControlSnapshot, policy: NetworkPolicy | None = None) -> str:
    policy = policy or load_network_policy()
    if policy.is_paused():
        return "paused"
    auth_state = snapshot.auth.get("state", "empty")
    if auth_state == "expired":
        return "expiredSession"
    if auth_state in {"empty", "missing-secret"}:
        return "signedOut"
    if snapshot.connection.get("state") == "blocked":
        return "blocked"
    backend_state = snapshot.backend.get("state", "stopped")
    recovery = snapshot.backend.get("recovery_state", "")
    if backend_state == "running" and recovery == "recovering":
        return "reconnecting"
    if backend_state == "running":
        return "connected"
    if backend_state == "degraded":
        return "degraded"
    if backend_state in {"connecting", "starting"}:
        return "connecting"
    if snapshot.vpn.get("state") != "configured":
        return "firstRun"
    return "disconnected"


def can_connect(snapshot: ControlSnapshot, policy: NetworkPolicy) -> bool:
    return (
        not policy.is_paused()
        and snapshot.auth.get("state") == "configured"
        and snapshot.connection.get("state") != "blocked"
        and snapshot.backend.get("state") not in {"running", "connecting"}
    )


def headline_for_state(state: str, snapshot: ControlSnapshot) -> str:
    return {
        "firstRun": "Ready for setup",
        "signedOut": "Sign in to start",
        "expiredSession": "Session expired",
        "blocked": snapshot.connection.get("title", "Connection needs attention"),
        "paused": "VPN paused",
        "connecting": "Connecting",
        "connected": "Secure connection active",
        "reconnecting": "Reconnecting",
        "degraded": "Connection needs attention",
        "disconnected": "Ready to connect",
    }.get(state, "Connection status")


def detail_for_state(state: str, snapshot: ControlSnapshot, policy: NetworkPolicy) -> str:
    if state == "paused" and policy.pause_until:
        return f"Paused until {int(policy.pause_until)}."
    if state == "blocked":
        return snapshot.connection.get("message", "Review the readiness checks before connecting.")
    if state == "connected":
        backend = snapshot.backend.get("backend", "packet-tunnel")
        transport = snapshot.backend.get("transport_selected", "auto") or "auto"
        return f"Traffic is protected by {backend} using {transport}."
    if state == "signedOut":
        return "Use Bale sign-in before pairing a relay or starting the tunnel."
    if state == "expiredSession":
        return "Sign in again to refresh relay provisioning."
    if state == "firstRun":
        return "Create or approve a system VPN profile, then connect."
    return snapshot.backend.get("last_error") or snapshot.connection.get("message") or "The app is idle."


def readiness_items(snapshot: ControlSnapshot) -> list[dict[str, Any]]:
    auth_ready = snapshot.auth.get("state") == "configured"
    pairing = snapshot.pairing
    pairing_ready = pairing.get("state") in {"paired", "accepted", "complete"} and pairing.get("provisioning_status") in {"complete", "accepted"}
    profile_ready = snapshot.vpn.get("state") == "configured"
    route_ready = snapshot.backend.get("route_ready", "no") == "yes"
    dns_ready = snapshot.backend.get("dns_ready", "no") == "yes"
    return [
        {"key": "auth", "label": "Bale sign-in", "ready": auth_ready, "detail": "Ready" if auth_ready else "Sign in with Bale."},
        {"key": "pairing", "label": "Relay pairing", "ready": pairing_ready, "detail": pairing.get("name", "Create or sync a relay pairing.")},
        {"key": "profile", "label": "System profile", "ready": profile_ready, "detail": snapshot.vpn.get("name", "Install the system VPN profile.")},
        {"key": "route", "label": "Route", "ready": route_ready, "detail": snapshot.backend.get("route_ready", "idle")},
        {"key": "dns", "label": "DNS", "ready": dns_ready, "detail": snapshot.backend.get("dns_ready", "idle")},
    ]


def relay_summaries(snapshot: ControlSnapshot) -> list[dict[str, Any]]:
    pairing = snapshot.pairing
    if pairing.get("state") == "empty":
        return []
    return [
        {
            "id": pairing.get("profile_id", ""),
            "name": pairing.get("name", "Saved relay"),
            "role": pairing.get("role", "client"),
            "status": pairing.get("provisioning_status", pairing.get("state", "")),
            "transportPreference": pairing.get("transport_preference", "auto"),
            "backendPreference": pairing.get("backend_preference", default_vpn_backend()),
        }
    ]


def signing_payload() -> dict[str, str]:
    try:
        from userbot_bale.control.macos import code_signing_status

        raw = code_signing_status().to_dict()
    except Exception as exc:  # noqa: BLE001
        raw = {"state": "unknown", "validIdentities": 0, "detail": str(exc)}
    return {str(key): str(value) for key, value in raw.items()}


def _connection_snapshot_payload(snapshot: ConnectionSnapshot) -> dict[str, Any]:
    return {
        "profile": asdict(snapshot.profile) if snapshot.profile is not None else None,
        "pairing": asdict(snapshot.pairing) if snapshot.pairing is not None else None,
        "backend": snapshot.backend,
        "probe": snapshot.probe,
        "connection": snapshot.connection,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("jwt", "token", "secret", "password")):
                result[str(key)] = "<redacted>"
            else:
                result[str(key)] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in TEXT_REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redacted_log_entries(*, max_files: int = 12, max_chars: int = 4_000) -> list[dict[str, str]]:
    roots = []
    for root in (app_dir(), data_dir(), shared_container_dir()):
        if root.exists() and root not in roots:
            roots.append(root)

    entries: list[dict[str, str]] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if len(entries) >= max_files:
                return entries
            if not path.is_file() or path.suffix not in LOG_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = path.relative_to(root)
            entries.append(
                {
                    "root": str(root),
                    "path": str(rel),
                    "tail": _redact_text(text[-max_chars:]),
                }
            )
    return entries


def friendly_bridge_error(raw: str) -> str:
    text = raw.strip() or "Unknown error."
    lower = text.lower()
    if "no pairing" in lower or "pairing" in lower and "ready" in lower:
        return "No relay pairing is ready yet. Create or sync a relay pairing first."
    if "auth" in lower or "session" in lower or "jwt" in lower:
        return "Bale sign-in needs attention. Sign in again, then retry."
    return text


def read_request(stdin_text: str) -> dict[str, Any]:
    text = stdin_text.strip()
    if not text:
        return {"command": "status", "payload": {}}
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("app-control request must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="userbot-bale-app-control")
    parser.add_argument("command", nargs="?", default="")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    import sys

    request = read_request(sys.stdin.read())
    if args.command:
        request["command"] = args.command
    result = AppControlBridge().handle(request).to_dict()
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
