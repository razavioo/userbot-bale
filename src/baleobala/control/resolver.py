"""DNS and route synchronization for VPN backends.

The backend flips networking state (system proxy, TUN device, routes); the
resolver is the thin layer that applies DNS servers and routing-table entries
on ``configure()`` and undoes them on ``restore()``. Resolvers snapshot prior
state so that process crashes leave a paper trail a subsequent ``restore()``
(or ``SystemResolver.restore_saved``) can replay.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RoutePlan:
    """Routes and DNS servers to install for the tunnel session."""

    dns_servers: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    interface: str | None = None
    search_domains: tuple[str, ...] = ()


@dataclass
class ResolverSnapshot:
    applied: bool = False
    interface: str | None = None
    dns_servers: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    bypass_hosts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    default_gateway: str | None = None
    default_gateway_iface: str | None = None
    prior_resolv_conf: str | None = None
    prior_resolvectl_dns: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "interface": self.interface,
            "dns_servers": list(self.dns_servers),
            "routes": list(self.routes),
            "bypass_hosts": {host: list(ips) for host, ips in self.bypass_hosts.items()},
            "default_gateway": self.default_gateway,
            "default_gateway_iface": self.default_gateway_iface,
            "prior_resolv_conf": self.prior_resolv_conf,
            "prior_resolvectl_dns": self.prior_resolvectl_dns,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResolverSnapshot":
        return cls(
            applied=bool(data.get("applied", False)),
            interface=data.get("interface"),
            dns_servers=tuple(data.get("dns_servers", [])),
            routes=tuple(data.get("routes", [])),
            bypass_hosts={
                str(host): tuple(ips)
                for host, ips in dict(data.get("bypass_hosts", {})).items()
            },
            default_gateway=data.get("default_gateway"),
            default_gateway_iface=data.get("default_gateway_iface"),
            prior_resolv_conf=data.get("prior_resolv_conf"),
            prior_resolvectl_dns=dict(data.get("prior_resolvectl_dns", {})),
        )


class SystemResolver(Protocol):
    def configure(self, plan: RoutePlan) -> None: ...
    def restore(self) -> None: ...
    def active(self) -> bool: ...
    def resolved_bypass_ips(self) -> tuple[str, ...]: ...


class NullResolver:
    """No-op resolver for tests and platforms without a concrete impl."""

    def __init__(self) -> None:
        self._applied = False

    def configure(self, plan: RoutePlan) -> None:  # noqa: ARG002
        self._applied = True

    def restore(self) -> None:
        self._applied = False

    def active(self) -> bool:
        return self._applied

    def add_bypass_host(self, hostname: str) -> None:  # noqa: ARG002
        self._applied = True

    def remove_bypass_host(self, hostname: str) -> None:  # noqa: ARG002
        self._applied = False

    def resolved_bypass_ips(self) -> tuple[str, ...]:
        return ()


class _BaseResolver:
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._state_store = JsonStore(
            state_path or (config_dir() / self._state_name)
        )
        self._runner = runner or subprocess.run
        self._snapshot = ResolverSnapshot()

    _state_name: str = "resolver.json"

    def _run(self, cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self._runner(cmd, check=check, capture_output=True, text=True)

    def active(self) -> bool:
        return self._snapshot.applied

    def resolved_bypass_ips(self) -> tuple[str, ...]:
        seen: list[str] = []
        for ips in self._snapshot.bypass_hosts.values():
            for ip in ips:
                if ip not in seen:
                    seen.append(ip)
        return tuple(seen)

    def _persist(self) -> None:
        self._state_store.save(self._snapshot.to_dict())

    def _clear_state(self) -> None:
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass


class LinuxResolver(_BaseResolver):
    """Applies routes via ``ip`` and DNS via ``resolvectl`` or /etc/resolv.conf.

    Preference order for DNS: ``resolvectl`` (systemd-resolved) when the
    interface is provided and the tool is present; otherwise rewrite
    ``/etc/resolv.conf`` after saving the prior contents.
    """

    _state_name = "linux_resolver.json"

    def configure(self, plan: RoutePlan) -> None:
        # Allow configure() to run even when applied=True, as long as we
        # haven't yet installed any tunnel routes — that lets callers
        # pre-program bypass hosts before the split-default routes capture
        # DNS, and still come back to install routes/DNS afterwards.
        if self._snapshot.applied and self._snapshot.routes:
            return
        snap = ResolverSnapshot(
            interface=plan.interface,
            dns_servers=plan.dns_servers,
            routes=plan.routes,
            default_gateway=self._snapshot.default_gateway,
            default_gateway_iface=self._snapshot.default_gateway_iface,
            bypass_hosts=dict(self._snapshot.bypass_hosts),
        )
        applied_routes: list[str] = []
        gateway, gateway_iface = self._discover_default_route()
        if gateway is not None:
            snap.default_gateway = gateway
            snap.default_gateway_iface = gateway_iface
        try:
            for route in plan.routes:
                if plan.interface is None:
                    continue
                result = self._run(["ip", "route", "add", route, "dev", plan.interface])
                if result.returncode != 0:
                    raise RuntimeError(
                        f"failed to install tunnel route {route} dev {plan.interface}: "
                        f"{(result.stderr or result.stdout or '').strip() or 'permission denied (need root or CAP_NET_ADMIN)'}"
                    )
                applied_routes.append(route)

            if plan.dns_servers:
                if plan.interface and self._has("resolvectl"):
                    snap.prior_resolvectl_dns = self._read_resolvectl(plan.interface)
                    self._run(
                        ["resolvectl", "dns", plan.interface, *plan.dns_servers]
                    )
                    if plan.search_domains:
                        self._run(
                            ["resolvectl", "domain", plan.interface, *plan.search_domains]
                        )
                else:
                    snap.prior_resolv_conf = self._read_resolv_conf()
                    self._write_resolv_conf(plan.dns_servers, plan.search_domains)
        except Exception:
            # best-effort rollback
            for route in applied_routes:
                if plan.interface:
                    self._run(["ip", "route", "del", route, "dev", plan.interface])
            raise
        snap.routes = tuple(applied_routes)
        snap.applied = True
        self._snapshot = snap
        self._persist()

    def restore(self) -> None:
        if not self._snapshot.applied:
            return
        snap = self._snapshot
        for host, ips in snap.bypass_hosts.items():
            self._remove_bypass_routes(ips, snap.default_gateway, snap.default_gateway_iface)
            self._unpin_etc_hosts(host)
        for route in snap.routes:
            if snap.interface:
                self._run(["ip", "route", "del", route, "dev", snap.interface])
        if snap.interface and snap.prior_resolvectl_dns and self._has("resolvectl"):
            prior = snap.prior_resolvectl_dns.get(snap.interface, [])
            if prior:
                self._run(["resolvectl", "dns", snap.interface, *prior])
            else:
                self._run(["resolvectl", "revert", snap.interface])
        elif snap.prior_resolv_conf is not None:
            try:
                Path("/etc/resolv.conf").write_text(snap.prior_resolv_conf)
            except OSError:
                pass
        self._snapshot = ResolverSnapshot()
        self._clear_state()

    def add_bypass_host(self, hostname: str) -> None:
        if not hostname:
            return
        snap = self._snapshot
        ips = self._resolve_host_ips(hostname)
        if not ips:
            raise RuntimeError(f"could not resolve bypass host {hostname!r}")
        gateway, gateway_iface = self._ensure_default_route()
        stored = dict(snap.bypass_hosts)
        if stored.get(hostname) == ips and snap.applied:
            return
        if gateway is not None:
            self._program_bypass_routes(ips, gateway, gateway_iface)
        # Pin name->IP in /etc/hosts so getaddrinfo doesn't need to traverse
        # the (now hijacked) tunnel DNS to resolve the bypass host.
        self._pin_etc_hosts(hostname, ips)
        stored[hostname] = ips
        self._snapshot = ResolverSnapshot(
            applied=True,
            interface=snap.interface,
            dns_servers=snap.dns_servers,
            routes=snap.routes,
            bypass_hosts=stored,
            default_gateway=gateway,
            default_gateway_iface=gateway_iface,
            prior_resolv_conf=snap.prior_resolv_conf,
            prior_resolvectl_dns=dict(snap.prior_resolvectl_dns),
        )
        self._persist()

    def remove_bypass_host(self, hostname: str) -> None:
        snap = self._snapshot
        stored = dict(snap.bypass_hosts)
        ips = stored.pop(hostname, None)
        if ips is None:
            ips = self._resolve_host_ips(hostname)
        if not ips:
            return
        gateway = snap.default_gateway
        gateway_iface = snap.default_gateway_iface
        if gateway is None:
            gateway, gateway_iface = self._discover_default_route()
        self._remove_bypass_routes(ips, gateway, gateway_iface)
        self._unpin_etc_hosts(hostname)
        if stored:
            self._snapshot = ResolverSnapshot(
                applied=True,
                interface=snap.interface,
                dns_servers=snap.dns_servers,
                routes=snap.routes,
                bypass_hosts=stored,
                default_gateway=gateway,
                default_gateway_iface=gateway_iface,
                prior_resolv_conf=snap.prior_resolv_conf,
                prior_resolvectl_dns=dict(snap.prior_resolvectl_dns),
            )
            self._persist()
        else:
            self._snapshot = ResolverSnapshot()
            if snap.routes or snap.dns_servers:
                # Keep any route/DNS state managed by configure() intact.
                self._snapshot = ResolverSnapshot(
                    applied=True,
                    interface=snap.interface,
                    dns_servers=snap.dns_servers,
                    routes=snap.routes,
                    default_gateway=gateway,
                    default_gateway_iface=gateway_iface,
                    prior_resolv_conf=snap.prior_resolv_conf,
                    prior_resolvectl_dns=dict(snap.prior_resolvectl_dns),
                )
                self._persist()
            else:
                self._clear_state()

    _ETC_HOSTS_TAG = "# baleobala-bypass"

    def _pin_etc_hosts(self, hostname: str, ips: tuple[str, ...]) -> None:
        path = Path("/etc/hosts")
        try:
            current = path.read_text().splitlines()
        except OSError:
            return
        marker = f"{self._ETC_HOSTS_TAG} {hostname}"
        kept = [line for line in current if not line.endswith(marker)]
        for ip in ips:
            kept.append(f"{ip} {hostname} {marker}")
        try:
            path.write_text("\n".join(kept) + "\n")
        except OSError:
            pass

    def _unpin_etc_hosts(self, hostname: str) -> None:
        path = Path("/etc/hosts")
        try:
            current = path.read_text().splitlines()
        except OSError:
            return
        marker = f"{self._ETC_HOSTS_TAG} {hostname}"
        kept = [line for line in current if not line.endswith(marker)]
        if len(kept) == len(current):
            return
        try:
            path.write_text("\n".join(kept) + "\n")
        except OSError:
            pass

    def _has(self, tool: str) -> bool:
        result = self._run(["which", tool])
        return result.returncode == 0 and bool(result.stdout.strip())

    def _read_resolvectl(self, interface: str) -> dict[str, list[str]]:
        result = self._run(["resolvectl", "dns", interface])
        if result.returncode != 0:
            return {}
        servers: list[str] = []
        for line in result.stdout.splitlines():
            if ":" in line:
                _, rhs = line.split(":", 1)
                servers = [tok for tok in rhs.split() if tok]
        return {interface: servers}

    @staticmethod
    def _read_resolv_conf() -> str | None:
        try:
            return Path("/etc/resolv.conf").read_text()
        except OSError:
            return None

    def _discover_default_route(self) -> tuple[str | None, str | None]:
        result = self._run(["ip", "route", "show", "default"])
        if result.returncode != 0:
            return (None, None)
        for line in result.stdout.splitlines():
            tokens = line.split()
            if not tokens or tokens[0] != "default":
                continue
            gateway = None
            iface = None
            for idx, token in enumerate(tokens):
                if token == "via" and idx + 1 < len(tokens):
                    gateway = tokens[idx + 1]
                elif token == "dev" and idx + 1 < len(tokens):
                    iface = tokens[idx + 1]
            return (gateway, iface)
        return (None, None)

    def _ensure_default_route(self) -> tuple[str | None, str | None]:
        gateway = self._snapshot.default_gateway
        gateway_iface = self._snapshot.default_gateway_iface
        if gateway is not None:
            return (gateway, gateway_iface)
        gateway, gateway_iface = self._discover_default_route()
        self._snapshot = ResolverSnapshot(
            applied=self._snapshot.applied,
            interface=self._snapshot.interface,
            dns_servers=self._snapshot.dns_servers,
            routes=self._snapshot.routes,
            bypass_hosts=dict(self._snapshot.bypass_hosts),
            default_gateway=gateway,
            default_gateway_iface=gateway_iface,
            prior_resolv_conf=self._snapshot.prior_resolv_conf,
            prior_resolvectl_dns=dict(self._snapshot.prior_resolvectl_dns),
        )
        return (gateway, gateway_iface)

    @staticmethod
    def _resolve_host_ips(hostname: str) -> tuple[str, ...]:
        resolved: list[str] = []
        seen: set[str] = set()
        for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(hostname, None):
            if family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            ip = sockaddr[0]
            if ip in seen:
                continue
            seen.add(ip)
            resolved.append(ip)
        return tuple(resolved)

    def _route_cmd(self, action: str, ip: str, gateway: str, iface: str | None) -> list[str]:
        cmd = ["ip"]
        if ":" in ip:
            cmd.append("-6")
        cmd.extend(["route", action, ip, "via", gateway])
        if iface:
            cmd.extend(["dev", iface])
        return cmd

    def _remove_bypass_routes(self, ips: tuple[str, ...], gateway: str | None, gateway_iface: str | None) -> None:
        if gateway is None:
            return
        for ip in ips:
            self._run(self._route_cmd("del", ip, gateway, gateway_iface))

    def _program_bypass_routes(
        self,
        ips: tuple[str, ...],
        gateway: str,
        gateway_iface: str | None,
    ) -> None:
        for ip in ips:
            self._run(self._route_cmd("replace", ip, gateway, gateway_iface))

    @staticmethod
    def _write_resolv_conf(
        servers: Iterable[str], search: Iterable[str] = ()
    ) -> None:
        lines = [f"nameserver {s}" for s in servers]
        search_list = list(search)
        if search_list:
            lines.insert(0, "search " + " ".join(search_list))
        try:
            Path("/etc/resolv.conf").write_text("\n".join(lines) + "\n")
        except OSError:
            pass


class MacOSResolver(_BaseResolver):
    """Applies routes via ``route`` and DNS via ``networksetup``.

    Unlike :class:`MacOSSystemProxySession` this resolver operates at the
    routing layer and is intended for use alongside a real TUN device, not
    the system-proxy fallback backend.
    """

    _state_name = "macos_resolver.json"

    def configure(self, plan: RoutePlan) -> None:
        if self._snapshot.applied:
            return
        snap = ResolverSnapshot(
            interface=plan.interface,
            dns_servers=plan.dns_servers,
            routes=plan.routes,
        )
        applied_routes: list[str] = []
        try:
            for route in plan.routes:
                if plan.interface is None:
                    continue
                result = self._run(
                    ["route", "-n", "add", "-net", route, "-interface", plan.interface]
                )
                if result.returncode == 0:
                    applied_routes.append(route)
            if plan.dns_servers:
                service = self._primary_service()
                if service:
                    snap.prior_resolvectl_dns = {service: self._current_dns(service)}
                    self._run(
                        ["networksetup", "-setdnsservers", service, *plan.dns_servers]
                    )
                    snap.interface = snap.interface or service
        except Exception:
            for route in applied_routes:
                if plan.interface:
                    self._run(
                        ["route", "-n", "delete", "-net", route, "-interface", plan.interface]
                    )
            raise
        snap.routes = tuple(applied_routes)
        snap.applied = True
        self._snapshot = snap
        self._persist()

    def restore(self) -> None:
        if not self._snapshot.applied:
            return
        snap = self._snapshot
        for route in snap.routes:
            if snap.interface:
                self._run(
                    ["route", "-n", "delete", "-net", route, "-interface", snap.interface]
                )
        for service, prior in snap.prior_resolvectl_dns.items():
            args = prior or ["Empty"]
            self._run(["networksetup", "-setdnsservers", service, *args])
        self._snapshot = ResolverSnapshot()
        self._clear_state()

    def _primary_service(self) -> str | None:
        result = self._run(["networksetup", "-listallnetworkservices"])
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("An asterisk"):
                continue
            return line
        return None

    def _current_dns(self, service: str) -> list[str]:
        result = self._run(["networksetup", "-getdnsservers", service])
        servers: list[str] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or "aren't any" in line.lower() or "there are no" in line.lower():
                continue
            servers.append(line)
        return servers


def default_resolver() -> SystemResolver:
    if sys.platform.startswith("linux"):
        return LinuxResolver()
    if sys.platform == "darwin":
        return MacOSResolver()
    return NullResolver()
