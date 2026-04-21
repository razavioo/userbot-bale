"""Linux VPN backend: TUN device + routes + DNS.

Symmetric to :mod:`baleobala.control.macos` but targets iproute2. The backend
orchestrates the control plane (device up/down, routes, DNS) and delegates
DNS/route application to :class:`LinuxResolver`. The data plane — actually
pushing packets into the TUN device — stays in :mod:`baleobala.vpn.runner`.

The backend does *not* require root when the TUN device is pre-created with
``ip tuntap add name vpn0 mode tun user $USER`` (see
``scripts/vpn-setup-tun.sh``). When running without privileges and no TUN
device exists, ``up()`` will raise; callers should fall back to
:class:`ProxyFallbackBackend` in that case.
"""

from __future__ import annotations

import atexit
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from baleobala.control.paths import config_dir
from baleobala.control.readiness import BackendReadiness
from baleobala.control.resolver import LinuxResolver, RoutePlan, SystemResolver
from baleobala.control.store import JsonStore


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class TunPlan:
    name: str = "vpn0"
    address: str = "10.77.0.2/24"
    mtu: int = 1400
    routes: tuple[str, ...] = ("0.0.0.0/1", "128.0.0.0/1")
    dns_servers: tuple[str, ...] = ("1.1.1.1", "9.9.9.9")


@dataclass
class _LinuxSnapshot:
    active: bool = False
    tun_name: str | None = None
    created_device: bool = False
    prior_addrs: tuple[str, ...] = field(default_factory=tuple)
    prior_link_up: bool = False

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "tun_name": self.tun_name,
            "created_device": self.created_device,
            "prior_addrs": list(self.prior_addrs),
            "prior_link_up": self.prior_link_up,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_LinuxSnapshot":
        return cls(
            active=bool(data.get("active", False)),
            tun_name=data.get("tun_name"),
            created_device=bool(data.get("created_device", False)),
            prior_addrs=tuple(data.get("prior_addrs", [])),
            prior_link_up=bool(data.get("prior_link_up", False)),
        )


class LinuxTunSession:
    """Bring a TUN interface up and apply routes/DNS; restore on stop.

    Uses ``ip`` commands via an injectable runner. Non-privileged callers
    should pre-create the device; ``start()`` will attempt ``ip tuntap add``
    and fall back to raising :class:`PermissionError` if that fails.
    """

    def __init__(
        self,
        plan: TunPlan | None = None,
        *,
        state_path: Path | None = None,
        runner: Runner | None = None,
        resolver: SystemResolver | None = None,
    ) -> None:
        self.plan = plan or TunPlan()
        self._state_store = JsonStore(
            state_path or (config_dir() / "linux_tun_session.json")
        )
        self._runner = runner or subprocess.run
        self._resolver = resolver if resolver is not None else LinuxResolver()
        self._snapshot = _LinuxSnapshot()
        self._atexit_registered = False

    @property
    def active(self) -> bool:
        return self._snapshot.active

    def start(self) -> None:
        if self._snapshot.active:
            return
        name = self.plan.name
        created = False
        if not self._device_exists(name):
            result = self._run(
                ["ip", "tuntap", "add", "dev", name, "mode", "tun", "user", os.environ.get("USER", "")]
            )
            if result.returncode != 0:
                raise PermissionError(
                    f"TUN device {name!r} does not exist and `ip tuntap add` failed: "
                    f"{result.stderr.strip() or 'insufficient privileges'}"
                )
            created = True

        prior_addrs = self._addrs(name)
        prior_up = self._link_is_up(name)

        self._run(["ip", "addr", "add", self.plan.address, "dev", name], check=False)
        self._run(["ip", "link", "set", name, "mtu", str(self.plan.mtu)], check=False)
        self._run(["ip", "link", "set", name, "up"], check=False)

        try:
            self._resolver.configure(
                RoutePlan(
                    dns_servers=self.plan.dns_servers,
                    routes=self.plan.routes,
                    interface=name,
                )
            )
        except Exception:
            self._teardown_link(name, created, prior_addrs, prior_up)
            raise

        self._snapshot = _LinuxSnapshot(
            active=True,
            tun_name=name,
            created_device=created,
            prior_addrs=prior_addrs,
            prior_link_up=prior_up,
        )
        self._state_store.save(self._snapshot.to_dict())

        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True

    def stop(self) -> None:
        if not self._snapshot.active:
            return
        try:
            self._resolver.restore()
        except Exception:
            pass
        snap = self._snapshot
        name = snap.tun_name or self.plan.name
        self._teardown_link(name, snap.created_device, snap.prior_addrs, snap.prior_link_up)
        self._snapshot = _LinuxSnapshot()
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        return {
            "active": "yes" if self._snapshot.active else "no",
            "tun": self._snapshot.tun_name or self.plan.name,
            "address": self.plan.address,
            "mtu": str(self.plan.mtu),
            "state": "running" if self._snapshot.active else "stopped",
        }

    def _teardown_link(
        self,
        name: str,
        created: bool,
        prior_addrs: tuple[str, ...],
        prior_up: bool,
    ) -> None:
        self._run(["ip", "addr", "flush", "dev", name], check=False)
        for addr in prior_addrs:
            self._run(["ip", "addr", "add", addr, "dev", name], check=False)
        if not prior_up:
            self._run(["ip", "link", "set", name, "down"], check=False)
        if created:
            self._run(["ip", "tuntap", "del", "dev", name, "mode", "tun"], check=False)

    def _device_exists(self, name: str) -> bool:
        result = self._run(["ip", "link", "show", name])
        return result.returncode == 0

    def _addrs(self, name: str) -> tuple[str, ...]:
        result = self._run(["ip", "-o", "-4", "addr", "show", "dev", name])
        addrs: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                addrs.append(parts[3])
        return tuple(addrs)

    def _link_is_up(self, name: str) -> bool:
        result = self._run(["ip", "link", "show", name])
        return "state UP" in result.stdout or "<UP" in result.stdout

    def _run(self, cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self._runner(cmd, check=check, capture_output=True, text=True)


class LinuxTunBackend:
    """VpnBackend implementation wired on top of :class:`LinuxTunSession`."""

    def __init__(
        self,
        *,
        plan: TunPlan | None = None,
        session: LinuxTunSession | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._plan = plan or TunPlan()
        self._session = session or LinuxTunSession(plan=self._plan)
        self._state_store = JsonStore(state_path or (config_dir() / "linux_backend.json"))
        self._profile_id: str | None = None
        self._pairing_id: str | None = None

    def up(self, profile) -> dict[str, str]:  # noqa: ANN001 — duck-typed VpnProfile
        self._session.start()
        self._profile_id = profile.profile_id
        self._pairing_id = profile.pairing_id
        session = self._session.status()
        payload = BackendReadiness.for_linux_tun(
            state="running",
            session_active=self._session.active,
            tun=session["tun"],
            address=session["address"],
            mtu=session["mtu"],
        ).to_dict()
        payload["profile_id"] = profile.profile_id
        payload["pairing_id"] = profile.pairing_id or ""
        payload.update(session)
        self._state_store.save(payload)
        return payload

    def down(self) -> None:
        self._session.stop()
        self._profile_id = None
        self._pairing_id = None
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        payload = self._state_store.load(default=None)
        if isinstance(payload, dict):
            return {str(k): str(v) for k, v in payload.items()}
        session = self._session.status()
        return BackendReadiness.for_linux_tun(
            state="stopped",
            session_active=False,
            tun=session["tun"],
            address=session["address"],
            mtu=session["mtu"],
        ).to_dict()
