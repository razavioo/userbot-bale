"""macOS system-proxy helpers for the current VPN bridge."""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from baleobala.control.paths import APP_GROUP_IDENTIFIER, config_dir, shared_container_dir
from baleobala.control.probe import probe_endpoint
from baleobala.control.store import JsonStore


Runner = Callable[..., subprocess.CompletedProcess[str]]

_BYPASS_DOMAINS = ("localhost", "127.0.0.1", "::1")
_TUNNEL_PROFILE_NAME = "packet_tunnel_profile.json"
_TUNNEL_INSTALL_REQUEST = "packet_tunnel_install.request.json"
_APP_CONTROL_SOCKET = "bale_app_control.sock"
_DEFAULT_PROVIDER_BUNDLE_ID = "com.baleobala.app.packet-tunnel"


@dataclass(frozen=True)
class ProxyState:
    enabled: bool
    server: str | None = None
    port: int | None = None


@dataclass(frozen=True)
class ServiceSnapshot:
    name: str
    web: ProxyState
    secure_web: ProxyState
    socks: ProxyState
    bypass_domains: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CodeSigningStatus:
    state: str
    valid_identities: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "validIdentities": self.valid_identities,
            "detail": self.detail,
        }


def tunnel_profile_path() -> Path:
    return shared_container_dir() / _TUNNEL_PROFILE_NAME


def tunnel_profile_installed(config: dict[str, object] | None = None) -> bool:
    payload = load_tunnel_profile()
    if payload is None:
        return False
    if config is None:
        return True
    return payload == config


def load_tunnel_profile() -> dict[str, object] | None:
    path = tunnel_profile_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def code_signing_status(*, runner: Runner | None = None) -> CodeSigningStatus:
    if sys.platform != "darwin":
        return CodeSigningStatus("not-applicable", detail="Code signing is only required for the macOS packet tunnel app.")
    run = runner or subprocess.run
    try:
        result = run(
            ["security", "find-identity", "-v", "-p", "codesigning"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return CodeSigningStatus("missing-tool", detail="macOS security tool is unavailable.")
    except Exception as exc:  # noqa: BLE001
        return CodeSigningStatus("error", detail=str(exc))

    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    valid_count = 0
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.endswith("valid identities found"):
            try:
                valid_count = int(stripped.split(" ", 1)[0])
            except (IndexError, ValueError):
                pass
            break
        if ")" in stripped and '"' in stripped:
            valid_count += 1
    if valid_count > 0:
        return CodeSigningStatus("ready", valid_identities=valid_count, detail=f"{valid_count} valid code-signing identity found.")
    detail = output or "No valid code-signing identities found."
    return CodeSigningStatus("missing-identity", detail=detail)


def packet_tunnel_configuration(profile, pairing=None) -> dict[str, object]:  # noqa: ANN001
    relay_identifier = ""
    if pairing is not None:
        relay_identifier = str(getattr(pairing, "relay_id", "") or "")
    if not relay_identifier:
        relay_identifier = str(getattr(profile, "pairing_id", "") or "")
    try:
        from baleobala.control.app_control import load_network_policy, packet_tunnel_policy_payload

        policy = load_network_policy()
        policy_payload = packet_tunnel_policy_payload(policy)
        dns_servers = policy.custom_dns_servers if policy.dns_mode != "system" else []
    except Exception:  # noqa: BLE001
        policy_payload = {}
        dns_servers = ["1.1.1.1", "9.9.9.9"]
    return {
        "configurationVersion": 1,
        "displayName": getattr(profile, "name", "baleobala"),
        "appGroupIdentifier": APP_GROUP_IDENTIFIER,
        "providerBundleIdentifier": provider_bundle_identifier(),
        "serverAddress": "127.0.0.1",
        "tunnelIPv4Address": "10.77.0.2",
        "tunnelIPv4SubnetMask": "255.255.255.0",
        "includedIPv4Routes": ["0.0.0.0/0"],
        "includedIPv6Routes": [],
        "excludedRoutes": ["127.0.0.0/8"],
        "dnsServers": dns_servers,
        "searchDomains": [],
        "mtu": 1400,
        "overheadBytes": 80,
        "carrierSocketPath": "carrier_tunnel.sock",
        "keychainTokenKey": "auth.jwt",
        "relayIdentifier": relay_identifier,
        "packetTunnelHost": getattr(profile, "listen_host", "127.0.0.1"),
        "packetTunnelPort": getattr(profile, "listen_port", 1080),
        **policy_payload,
    }


def provider_bundle_identifier() -> str:
    return os.environ.get("BALEOBALA_PROVIDER_BUNDLE_ID", _DEFAULT_PROVIDER_BUNDLE_ID)


def carrier_socket_path(config: dict[str, object] | None = None) -> Path:
    payload = config or load_tunnel_profile() or {}
    return shared_container_dir() / str(payload.get("carrierSocketPath") or "carrier_tunnel.sock")


def cleanup_stale_carrier_socket(config: dict[str, object] | None = None, *, timeout: float = 0.2) -> bool:
    path = carrier_socket_path(config)
    if not path.exists():
        return False
    probe = probe_endpoint(f"unix://{path}", timeout=timeout)
    if probe.ok:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _notify_app(command: dict[str, object]) -> bool:
    socket_path = shared_container_dir() / _APP_CONTROL_SOCKET
    if not socket_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps(command, sort_keys=True).encode("utf-8"))
            return True
    except OSError:
        return False


def install_tunnel_profile(config: dict[str, object]) -> dict[str, str]:
    shared_container_dir().mkdir(parents=True, exist_ok=True)
    path = tunnel_profile_path()
    payload = dict(config)
    payload.setdefault("configurationVersion", 1)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    request_path = shared_container_dir() / _TUNNEL_INSTALL_REQUEST
    request_path.write_text(json.dumps({"type": "install_tunnel_profile", "profile_path": str(path)}, indent=2, sort_keys=True), encoding="utf-8")

    notified = _notify_app({"type": "install_tunnel_profile", "profile_path": str(path)})
    if not notified and sys.platform == "darwin":
        try:
            subprocess.run(["open", "-g", "-a", "BaleobalaApp"], check=False)
        except Exception:  # noqa: BLE001
            pass
    return {
        "state": "installed",
        "profile_path": str(path),
        "request_path": str(request_path),
        "notified": "yes" if notified else "no",
    }


class MacOSSystemProxySession:
    """Apply and restore macOS network proxy settings for a local tunnel."""

    def __init__(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1080,
        services: list[str] | None = None,
        state_path: Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self._services = services
        self._state_store = JsonStore(state_path or (config_dir() / "macos_system_proxy.json"))
        self._runner = runner or subprocess.run
        self._snapshots: list[ServiceSnapshot] = []
        self._active = False
        self._atexit_registered = False

    def __enter__(self) -> "MacOSSystemProxySession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def start(self) -> None:
        if self._active:
            return
        self._snapshots = [self._snapshot_service(name) for name in self._resolve_services()]
        self._persist_state()
        for name in self._service_names():
            self._set_proxy(name, "web", True, self.listen_host, self.listen_port)
            self._set_proxy(name, "secureweb", True, self.listen_host, self.listen_port)
            self._set_proxy(name, "socksfirewall", True, self.listen_host, self.listen_port)
            self._set_bypass_domains(name, _BYPASS_DOMAINS)
        self._flush_dns()
        self._active = True
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True

    def stop(self) -> None:
        if not self._active:
            return
        for snapshot in self._snapshots:
            self._restore_service(snapshot)
        self._flush_dns()
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass
        self._active = False
        if self._atexit_registered:
            try:
                atexit.unregister(self.stop)
            except Exception:  # noqa: BLE001
                pass
            self._atexit_registered = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def snapshots(self) -> list[ServiceSnapshot]:
        return list(self._snapshots)

    def status(self) -> dict[str, str]:
        return {
            "active": "yes" if self._active else "no",
            "services": str(len(self._snapshots)),
            "proxy": f"{self.listen_host}:{self.listen_port}",
            "state": "saved" if self._state_store.path.exists() else "empty",
        }

    @classmethod
    def restore_saved_state(
        cls,
        *,
        state_path: Path | None = None,
        runner: Runner | None = None,
    ) -> bool:
        store = JsonStore(state_path or (config_dir() / "macos_system_proxy.json"))
        payload = store.load(default=None)
        if not payload:
            return False
        session = cls(state_path=state_path, runner=runner)
        try:
            for item in payload.get("items", []):
                snapshot = cls._snapshot_from_dict(item)
                session._restore_service(snapshot)
            session._flush_dns()
            return True
        finally:
            try:
                store.path.unlink()
            except FileNotFoundError:
                pass

    def _resolve_services(self) -> list[str]:
        if self._services is not None:
            return list(self._services)
        out = self._run(["networksetup", "-listallnetworkservices"]).stdout
        services: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("An asterisk"):
                continue
            if line.startswith("*"):
                continue
            services.append(line)
        return services

    def _service_names(self) -> list[str]:
        return [snapshot.name for snapshot in self._snapshots]

    def _snapshot_service(self, service: str) -> ServiceSnapshot:
        return ServiceSnapshot(
            name=service,
            web=self._read_proxy(service, "web"),
            secure_web=self._read_proxy(service, "secureweb"),
            socks=self._read_proxy(service, "socksfirewall"),
            bypass_domains=tuple(self._read_bypass_domains(service)),
        )

    def _restore_service(self, snapshot: ServiceSnapshot) -> None:
        self._set_proxy(snapshot.name, "web", snapshot.web.enabled, snapshot.web.server, snapshot.web.port)
        self._set_proxy(
            snapshot.name,
            "secureweb",
            snapshot.secure_web.enabled,
            snapshot.secure_web.server,
            snapshot.secure_web.port,
        )
        self._set_proxy(
            snapshot.name,
            "socksfirewall",
            snapshot.socks.enabled,
            snapshot.socks.server,
            snapshot.socks.port,
        )
        domains = snapshot.bypass_domains or ()
        self._set_bypass_domains(snapshot.name, domains)

    def _persist_state(self) -> None:
        self._state_store.save(
            {
                "items": [
                    {
                        "name": snapshot.name,
                        "web": {
                            "enabled": snapshot.web.enabled,
                            "server": snapshot.web.server,
                            "port": snapshot.web.port,
                        },
                        "secure_web": {
                            "enabled": snapshot.secure_web.enabled,
                            "server": snapshot.secure_web.server,
                            "port": snapshot.secure_web.port,
                        },
                        "socks": {
                            "enabled": snapshot.socks.enabled,
                            "server": snapshot.socks.server,
                            "port": snapshot.socks.port,
                        },
                        "bypass_domains": list(snapshot.bypass_domains),
                    }
                    for snapshot in self._snapshots
                ]
            }
        )

    @staticmethod
    def _snapshot_from_dict(data: dict[str, object]) -> ServiceSnapshot:
        def _state(key: str) -> ProxyState:
            raw = data.get(key, {})
            if not isinstance(raw, dict):
                raw = {}
            server = raw.get("server")
            return ProxyState(
                enabled=bool(raw.get("enabled", False)),
                server=str(server) if server is not None else None,
                port=int(raw["port"]) if raw.get("port") not in {None, ""} else None,
            )

        bypass = data.get("bypass_domains", [])
        domains = tuple(str(item) for item in bypass) if isinstance(bypass, list) else ()
        return ServiceSnapshot(
            name=str(data.get("name", "")),
            web=_state("web"),
            secure_web=_state("secure_web"),
            socks=_state("socks"),
            bypass_domains=domains,
        )

    def _read_proxy(self, service: str, kind: str) -> ProxyState:
        cmd = {
            "web": ["networksetup", "-getwebproxy", service],
            "secureweb": ["networksetup", "-getsecurewebproxy", service],
            "socksfirewall": ["networksetup", "-getsocksfirewallproxy", service],
        }[kind]
        out = self._run(cmd).stdout
        values = self._parse_key_values(out)
        enabled = values.get("Enabled", "").lower() in {"yes", "on", "true", "1"}
        server = values.get("Server") or None
        port_text = values.get("Port") or ""
        try:
            port = int(port_text) if port_text else None
        except ValueError:
            port = None
        return ProxyState(enabled=enabled, server=server, port=port)

    def _read_bypass_domains(self, service: str) -> list[str]:
        out = self._run(["networksetup", "-getproxybypassdomains", service]).stdout
        domains: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("There aren't") or line.startswith("There are"):
                continue
            domains.append(line)
        return domains

    def _set_proxy(self, service: str, kind: str, enabled: bool, server: str | None, port: int | None) -> None:
        suffix = {
            "web": "-setwebproxy",
            "secureweb": "-setsecurewebproxy",
            "socksfirewall": "-setsocksfirewallproxy",
        }[kind]
        if enabled:
            if server is None or port is None:
                raise ValueError(f"cannot enable {kind} proxy without server and port")
            self._run(["networksetup", suffix, service, server, str(port), "on"])
            return
        self._run(["networksetup", suffix, service, "off"])

    def _set_bypass_domains(self, service: str, domains: tuple[str, ...] | list[str]) -> None:
        cmd = ["networksetup", "-setproxybypassdomains", service]
        if domains:
            cmd.extend(domains)
        else:
            cmd.append("Empty")
        self._run(cmd)

    def _flush_dns(self) -> None:
        for cmd in (["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]):
            try:
                self._run(cmd, check=False)
            except FileNotFoundError:
                continue

    def _run(self, cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._runner(cmd, check=check, capture_output=True, text=True)

    @staticmethod
    def _parse_key_values(text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
        return values
