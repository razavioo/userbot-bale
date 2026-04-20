"""DNS and route synchronization for VPN backends.

The backend flips networking state (system proxy, TUN device, routes); the
resolver is the thin layer that applies DNS servers and routing-table entries
on ``configure()`` and undoes them on ``restore()``. Resolvers snapshot prior
state so that process crashes leave a paper trail a subsequent ``restore()``
(or ``SystemResolver.restore_saved``) can replay.
"""

from __future__ import annotations

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
    prior_resolv_conf: str | None = None
    prior_resolvectl_dns: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "interface": self.interface,
            "dns_servers": list(self.dns_servers),
            "routes": list(self.routes),
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
            prior_resolv_conf=data.get("prior_resolv_conf"),
            prior_resolvectl_dns=dict(data.get("prior_resolvectl_dns", {})),
        )


class SystemResolver(Protocol):
    def configure(self, plan: RoutePlan) -> None: ...
    def restore(self) -> None: ...
    def active(self) -> bool: ...


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
                result = self._run(["ip", "route", "add", route, "dev", plan.interface])
                if result.returncode == 0:
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
