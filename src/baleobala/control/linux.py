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
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from baleobala.control.paths import config_dir
from baleobala.control.probe import ProbeResult, probe_endpoint
from baleobala.control.readiness import BackendReadiness
from baleobala.control.resolver import LinuxResolver, RoutePlan, SystemResolver
from baleobala.control.store import JsonStore


Runner = Callable[..., subprocess.CompletedProcess[str]]

_NFT_TABLE = "baleobala_ks"


class LinuxKillSwitch:
    """Block all outbound traffic that does not exit through the VPN interface.

    Uses nftables when available, falls back to iptables.  The kill switch is
    installed after the TUN interface is up so that the carrier bypass IPs
    (already resolved to numeric addresses by the resolver) can be whitelisted
    before the drop rule fires.

    The class is designed to be injected into :class:`LinuxTunSession` for
    testing — callers can pass a fake ``runner`` and ``nft_runner`` to drive it
    without real kernel access.
    """

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        nft_runner: Runner | None = None,
    ) -> None:
        self._runner = runner or subprocess.run
        self._nft_runner = nft_runner or subprocess.run
        self._backend: str | None = None  # "nftables" | "iptables" | None
        self._tun: str | None = None
        self._bypass_ips: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, tun: str, bypass_ips: tuple[str, ...] = ()) -> None:
        """Install kill-switch rules for *tun*, whitelisting *bypass_ips*."""
        self._tun = tun
        self._bypass_ips = bypass_ips
        if self._has_nftables():
            self._apply_nftables(tun, bypass_ips)
            self._backend = "nftables"
        elif self._has_iptables():
            self._apply_iptables(tun, bypass_ips)
            self._backend = "iptables"

    def remove(self) -> None:
        """Remove all kill-switch rules installed by :meth:`apply`."""
        if self._backend == "nftables":
            self._remove_nftables()
        elif self._backend == "iptables":
            self._remove_iptables(self._tun or "", self._bypass_ips)
        self._backend = None
        self._tun = None
        self._bypass_ips = ()

    @property
    def active(self) -> bool:
        return self._backend is not None

    @property
    def backend(self) -> str | None:
        return self._backend

    # ------------------------------------------------------------------
    # nftables implementation
    # ------------------------------------------------------------------

    def _has_nftables(self) -> bool:
        r = self._runner(["which", "nft"], check=False, capture_output=True, text=True)
        return r.returncode == 0

    def _apply_nftables(self, tun: str, bypass_ips: tuple[str, ...]) -> None:
        # Flush any previous baleobala table, then recreate.
        self._nft_runner(
            ["nft", "delete", "table", "inet", _NFT_TABLE],
            check=False, capture_output=True, text=True,
        )
        bypass_set = ""
        if bypass_ips:
            elems = ", ".join(bypass_ips)
            bypass_set = (
                f"  set bypass4 {{\n"
                f"    type ipv4_addr\n"
                f"    flags interval\n"
                f"    elements = {{ {elems} }}\n"
                f"  }}\n"
            )
        bypass_rule = "    ip daddr @bypass4 accept\n" if bypass_ips else ""
        ruleset = (
            f"table inet {_NFT_TABLE} {{\n"
            f"{bypass_set}"
            f"  chain output {{\n"
            f"    type filter hook output priority -100; policy accept;\n"
            f"    oif lo accept\n"
            f"    oif \"{tun}\" accept\n"
            f"{bypass_rule}"
            f"    drop\n"
            f"  }}\n"
            f"}}"
        )
        self._nft_runner(
            ["nft", "-f", "-"],
            input=ruleset, check=False, capture_output=True, text=True,
        )

    def _remove_nftables(self) -> None:
        self._nft_runner(
            ["nft", "delete", "table", "inet", _NFT_TABLE],
            check=False, capture_output=True, text=True,
        )

    # ------------------------------------------------------------------
    # iptables fallback
    # ------------------------------------------------------------------

    def _has_iptables(self) -> bool:
        r = self._runner(["which", "iptables"], check=False, capture_output=True, text=True)
        return r.returncode == 0

    def _apply_iptables(self, tun: str, bypass_ips: tuple[str, ...]) -> None:
        # Insert at the top of OUTPUT so they take precedence.
        for ip in reversed(bypass_ips):
            self._runner(
                ["iptables", "-I", "OUTPUT", "-d", ip, "-j", "ACCEPT"],
                check=False, capture_output=True, text=True,
            )
        self._runner(
            ["iptables", "-I", "OUTPUT", "-o", tun, "-j", "ACCEPT"],
            check=False, capture_output=True, text=True,
        )
        self._runner(
            ["iptables", "-I", "OUTPUT", "-o", "lo", "-j", "ACCEPT"],
            check=False, capture_output=True, text=True,
        )
        self._runner(
            ["iptables", "-A", "OUTPUT", "-j", "DROP"],
            check=False, capture_output=True, text=True,
        )

    def _remove_iptables(self, tun: str, bypass_ips: tuple[str, ...]) -> None:
        self._runner(
            ["iptables", "-D", "OUTPUT", "-j", "DROP"],
            check=False, capture_output=True, text=True,
        )
        if tun:
            self._runner(
                ["iptables", "-D", "OUTPUT", "-o", tun, "-j", "ACCEPT"],
                check=False, capture_output=True, text=True,
            )
        self._runner(
            ["iptables", "-D", "OUTPUT", "-o", "lo", "-j", "ACCEPT"],
            check=False, capture_output=True, text=True,
        )
        for ip in bypass_ips:
            self._runner(
                ["iptables", "-D", "OUTPUT", "-d", ip, "-j", "ACCEPT"],
                check=False, capture_output=True, text=True,
            )


@dataclass(frozen=True)
class TunPlan:
    name: str = "vpn0"
    address: str = "10.77.0.2/24"
    mtu: int = 1400
    # Default to NOT capturing the system default route. Installing
    # 0.0.0.0/1 + 128.0.0.0/1 before the carrier is verified causes the
    # user to lose internet connectivity if the data plane fails. The
    # tunnel is reachable on its /24 link route regardless, so ping/probe
    # tests still validate the carrier. A separate, explicit step (or a
    # caller passing routes=("0.0.0.0/1","128.0.0.0/1")) is needed to
    # promote this into a full-system VPN.
    routes: tuple[str, ...] = ()
    dns_servers: tuple[str, ...] = ()
    kill_switch: bool = False

    @classmethod
    def full_tunnel(
        cls,
        *,
        name: str = "vpn0",
        address: str = "10.77.0.2/24",
        mtu: int = 1400,
        dns_servers: tuple[str, ...] = ("1.1.1.1", "9.9.9.9"),
        kill_switch: bool = False,
    ) -> "TunPlan":
        return cls(
            name=name,
            address=address,
            mtu=mtu,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            dns_servers=dns_servers,
            kill_switch=kill_switch,
        )


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
        tun_opener: Callable[[str], object] | None = None,
        kill_switch: LinuxKillSwitch | None = None,
    ) -> None:
        self.plan = plan or TunPlan()
        self._state_store = JsonStore(
            state_path or (config_dir() / "linux_tun_session.json")
        )
        self._runner = runner or subprocess.run
        self._resolver = resolver if resolver is not None else LinuxResolver()
        self._tun_opener = tun_opener
        self._kill_switch = kill_switch if kill_switch is not None else (
            LinuxKillSwitch(runner=runner or subprocess.run) if self.plan.kill_switch else None
        )
        self._snapshot = _LinuxSnapshot()
        self._atexit_registered = False
        self._tun = None

    @property
    def active(self) -> bool:
        return self._snapshot.active

    def start(self) -> None:
        if self._snapshot.active:
            return
        user = os.environ.get("USER", "")
        last_error: Exception | None = None
        for name in self._candidate_names(self.plan.name):
            created = False
            try:
                if not self._device_exists(name):
                    result = self._run(
                        ["ip", "tuntap", "add", "dev", name, "mode", "tun", "user", user]
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
                self._tun = self._open_tun(name)

                try:
                    # Pre-install the carrier bypass while system DNS still
                    # works — once configure() installs split-default routes
                    # and rewrites DNS to 1.1.1.1/9.9.9.9 over vpn0, any
                    # subsequent hostname resolution would be black-holed.
                    self._add_bypass_host_best_effort("next-ws.bale.ai")
                    # Bypass the configured tunnel DNS servers and the
                    # system resolver's current upstreams so name resolution
                    # keeps working after the tunnel captures default
                    # routes. Without this, the LiveKit signaling URL
                    # (resolved by the runtime thread *after* configure())
                    # cannot be looked up.
                    for dns_ip in self.plan.dns_servers:
                        self._add_bypass_host_best_effort(dns_ip)
                    for dns_ip in self._current_system_dns():
                        try:
                            self._add_bypass_host_best_effort(dns_ip)
                        except Exception:
                            pass
                    self._resolver.configure(
                        RoutePlan(
                            dns_servers=self.plan.dns_servers,
                            routes=self.plan.routes,
                            interface=name,
                        )
                    )
                    # The carrier transport (Bale call WebRTC/LiveKit
                    # DataChannel) runs in this same process as root. If we
                    # capture the default route, the carrier media gets
                    # looped back into the tunnel it is supposed to power.
                    # The uid-based bypass rule is only meaningful when we
                    # have actually installed split-default routes.
                    if self.plan.routes:
                        self._add_uid_bypass_rule()
                    # Kill switch: block all outbound traffic not exiting
                    # through the TUN. Applied after bypass hosts are
                    # resolved so their IPs can be whitelisted before the
                    # drop rule fires.
                    if self._kill_switch is not None and self.plan.kill_switch:
                        bypass_ips = self._resolver.resolved_bypass_ips()
                        self._kill_switch.apply(name, bypass_ips)
                except Exception:
                    if self._kill_switch is not None:
                        try:
                            self._kill_switch.remove()
                        except Exception:
                            pass
                    try:
                        self._resolver.restore()
                    except Exception:
                        pass
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
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if self._tun is not None:
                    try:
                        self._tun.close()
                    except Exception:
                        pass
                    self._tun = None
                if created:
                    self._teardown_link(name, created, (), False)
                continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("no usable Linux TUN interface name was available")

    def _add_bypass_host_best_effort(self, host: str) -> None:
        try:
            self._resolver.add_bypass_host(host)
        except Exception:
            # The carrier bypass is important for production, but it must not
            # block startup in test or offline environments where DNS
            # resolution is unavailable yet the tunnel itself is otherwise
            # usable.
            pass

    def stop(self) -> None:
        if not self._snapshot.active:
            return
        if self._kill_switch is not None:
            try:
                self._kill_switch.remove()
            except Exception:
                pass
        self._remove_uid_bypass_rule()
        try:
            self._resolver.restore()
        except Exception:
            pass
        if self._tun is not None:
            try:
                self._tun.close()
            except Exception:
                pass
            self._tun = None
        snap = self._snapshot
        name = snap.tun_name or self.plan.name
        self._teardown_link(name, snap.created_device, snap.prior_addrs, snap.prior_link_up)
        self._snapshot = _LinuxSnapshot()
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        ks = self._kill_switch
        return {
            "active": "yes" if self._snapshot.active else "no",
            "tun": self._snapshot.tun_name or self.plan.name,
            "address": self.plan.address,
            "mtu": str(self.plan.mtu),
            "routes": ",".join(self.plan.routes),
            "dns_servers": ",".join(self.plan.dns_servers),
            "routing_policy": "full-tunnel" if self.plan.routes else "link-local",
            "full_tunnel": "yes" if self.plan.routes else "no",
            "kill_switch": "yes" if (ks is not None and ks.active) else ("configured" if self.plan.kill_switch else "no"),
            "kill_switch_backend": ks.backend or "" if ks is not None else "",
            "state": "running" if self._snapshot.active else "stopped",
        }

    def tun(self):
        return self._tun

    _UID_RULE_PREF = "100"

    def _add_uid_bypass_rule(self) -> None:
        if os.geteuid() != 0:
            return
        # idempotent: clean any prior leftover at this preference, then add
        self._run(["ip", "rule", "del", "pref", self._UID_RULE_PREF], check=False)
        self._run(
            ["ip", "rule", "add", "uidrange", "0-0", "lookup", "main", "pref", self._UID_RULE_PREF],
            check=False,
        )

    def _remove_uid_bypass_rule(self) -> None:
        if os.geteuid() != 0:
            return
        # remove until none remain at this preference
        for _ in range(4):
            result = self._run(
                ["ip", "rule", "del", "pref", self._UID_RULE_PREF], check=False
            )
            if result.returncode != 0:
                return

    def _current_system_dns(self) -> tuple[str, ...]:
        try:
            result = subprocess.run(
                ["resolvectl", "status"], capture_output=True, text=True, check=False
            )
        except FileNotFoundError:
            return ()
        ips: list[str] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("DNS Servers:") or stripped.startswith("Current DNS Server:"):
                _, _, rhs = stripped.partition(":")
                for tok in rhs.split():
                    if tok and tok not in ips and not tok.startswith("127."):
                        ips.append(tok)
        return tuple(ips)

    def _candidate_names(self, preferred: str) -> tuple[str, ...]:
        names = [preferred]
        if preferred == "vpn0":
            names.extend(f"vpn{i}" for i in range(1, 6))
        return tuple(dict.fromkeys(names))

    def _open_tun(self, name: str):
        if self._tun_opener is not None:
            return self._tun_opener(name)
        from baleobala.vpn.tun import TunDevice

        return TunDevice.open(name)

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


class LinuxTunnelRuntime:
    """Own the Bale carrier + tunnel runner lifecycle for linux-tun profiles."""

    def __init__(self, state_store: JsonStore | None = None) -> None:
        self._state_store = state_store or JsonStore(config_dir() / "linux_runtime.json")
        self._stop = threading.Event()
        self._active = False
        self._tun = None
        self._session = None
        self._chain = None
        self._runner = None
        self._keepalive = None
        self._health = None
        self._controller = None
        self._carrier_resolver = None
        self._carrier_hosts: tuple[str, ...] = ()
        self._credential_watcher = None
        self._refresh_credentials = None
        self._pairing_store = None
        self._profile_id = None

    def set_credential_refresh_context(self, pairing_store, refresh_credentials) -> None:  # noqa: ANN001
        self._pairing_store = pairing_store
        self._refresh_credentials = refresh_credentials

    def set_tun_session(self, session: LinuxTunSession) -> None:
        self._session = session

    def refresh_pairing_credentials(self, profile_id: str):  # noqa: ANN001
        if self._refresh_credentials is None:
            raise RuntimeError("credential refresh callback is not configured")
        return self._refresh_credentials(profile_id)

    def start(self, profile, auth_record) -> dict[str, str]:  # noqa: ANN001
        if auth_record is None or not auth_record.jwt:
            raise RuntimeError("linux-tun backend requires a stored Bale auth session")
        if profile.peer_id is None and not profile.answer:
            raise RuntimeError("linux-tun backend requires a paired peer_id")

        from baleobala.cli import _resolve_livekit_credentials
        from baleobala.vpn.cli import _build_transport_chain, _safe_mtu
        from baleobala.vpn.keepalive import LiveKitKeepalive
        from baleobala.vpn.router import FailoverController
        from baleobala.vpn.runner import RunnerConfig, VpnRunner
        from baleobala.bale import LiveKitSession
        from baleobala.control.credential_watcher import CredentialWatcher
        from baleobala.control.resolver import LinuxResolver
        import argparse

        args = argparse.Namespace(
            livekit_url=None,
            livekit_token=None,
            livekit_room=None,
            bale_jwt=auth_record.jwt,
            bale_jwt_file=None,
            peer_id=profile.peer_id,
            peer=None,
            peer_name=profile.peer_name,
            answer=profile.answer,
            answer_timeout=120.0,
            creds_timeout=45.0,
            identity=profile.name,
            tun="vpn0",
            tun_addr="10.77.0.1/24" if profile.role == "relay" else "10.77.0.2/24",
            tun_mtu=1400,
            transport="dc",
            sess_id=0x1111,
            psk=None,
            psk_file=None,
            wan="eth0",
            protocol=profile.protocol,
            volume=profile.volume,
        )

        tun = self._session.tun()
        if tun is None:
            raise RuntimeError("linux-tun session did not expose an open TUN device")
        session_status = self._session.status()
        args.tun = session_status.get("tun", args.tun)
        url, token = _resolve_livekit_credentials(args)
        session = LiveKitSession(url=url, token=token, identity=args.identity)
        carrier_resolver = LinuxResolver()
        carrier_hosts_attr = session.carrier_hosts
        carrier_hosts = tuple(sorted(carrier_hosts_attr() if callable(carrier_hosts_attr) else carrier_hosts_attr))
        for host in carrier_hosts:
            carrier_resolver.add_bypass_host(host)
        session.start()
        keepalive = LiveKitKeepalive(session, interval=20.0)
        keepalive.start()
        credential_watcher = None
        if self._pairing_store is not None and self._refresh_credentials is not None:
            profile_id = getattr(profile, "profile_id", None) or getattr(profile, "pairing_id", None)
            if profile_id:
                credential_watcher = CredentialWatcher(
                    self._pairing_store,
                    self,
                    profile_id,
                    lambda refreshed: None,
                )
                credential_watcher.start()
        chain = _build_transport_chain(args.transport, session, args)
        transport_name, transport = chain.start()
        mtu_floor = min(_safe_mtu(c.factory, precomputed=transport if c.name == transport_name else None) for c in chain._choices)  # type: ignore[attr-defined]
        cfg = RunnerConfig(
            sess_id=args.sess_id,
            ack_timeout=2.0 if transport_name == "dc" else 0.5,
            window=64 if transport_name == "dc" else 1,
            max_retries=3 if transport_name == "dc" else 16,
            mtu_override=mtu_floor,
        )
        runner = VpnRunner(tun, transport, cfg)
        runner.start()
        controller = FailoverController(
            runner,
            chain,
            carrier_session=session,
            carrier_factory=lambda: self._rebuild_runtime(args),
            status_callback=self._update_status,
        )
        controller.start(transport_name)

        self._tun = tun
        self._session = session
        self._chain = chain
        self._runner = runner
        self._keepalive = keepalive
        self._controller = controller
        self._carrier_resolver = carrier_resolver
        self._carrier_hosts = carrier_hosts
        self._credential_watcher = credential_watcher
        self._active = True
        payload = {
            "endpoint": session_status.get("tun", args.tun),
            "transport_selected": transport_name,
            "call_established": "yes",
            "data_flow_ok": "yes",
            "last_error": "",
            "recovery_state": "healthy",
            "transport_previous": "",
            "failover_count": "0",
            "recovering_since": "",
            "carrier_session_id": hex(id(session)),
        }
        self._state_store.save(payload)
        return payload

    def stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        if self._chain is not None:
            try:
                self._chain.close()
            except Exception:
                pass
            self._chain = None
        if self._keepalive is not None:
            self._keepalive.stop()
            self._keepalive = None
        if self._credential_watcher is not None:
            self._credential_watcher.stop()
            self._credential_watcher = None
        if self._carrier_resolver is not None:
            for host in self._carrier_hosts:
                try:
                    self._carrier_resolver.remove_bypass_host(host)
                except Exception:
                    pass
            self._carrier_resolver = None
            self._carrier_hosts = ()
        if self._session is not None:
            self._session.stop()
            self._session = None
        if self._tun is not None:
            try:
                self._tun.close()
            except Exception:
                pass
            self._tun = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def _rebuild_runtime(self, args):  # noqa: ANN001
        from baleobala.cli import _resolve_livekit_credentials
        from baleobala.bale import LiveKitSession
        from baleobala.vpn.cli import _build_transport_chain
        from baleobala.vpn.keepalive import LiveKitKeepalive

        url, token = _resolve_livekit_credentials(args)
        session = LiveKitSession(url=url, token=token, identity=args.identity)
        carrier_resolver = LinuxResolver()
        carrier_hosts_attr = session.carrier_hosts
        carrier_hosts = tuple(sorted(carrier_hosts_attr() if callable(carrier_hosts_attr) else carrier_hosts_attr))
        for host in carrier_hosts:
            carrier_resolver.add_bypass_host(host)
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
                for host in carrier_hosts:
                    try:
                        carrier_resolver.remove_bypass_host(host)
                    except Exception:
                        pass
                session.stop()

        chain.close = close_with_session  # type: ignore[method-assign]
        return session, chain

    def _update_status(self, fields: dict[str, str]) -> None:
        payload = self._state_store.load(default={})
        if not isinstance(payload, dict):
            payload = {}
        payload = {str(k): str(v) for k, v in payload.items()}
        payload.update({str(k): str(v) for k, v in fields.items()})
        self._state_store.save(payload)


class LinuxTunBackend:
    """VpnBackend implementation wired on top of :class:`LinuxTunSession`."""

    def __init__(
        self,
        *,
        plan: TunPlan | None = None,
        session: LinuxTunSession | None = None,
        runtime: LinuxTunnelRuntime | None = None,
        state_path: Path | None = None,
        runtime_state_path: Path | None = None,
    ) -> None:
        if plan is None:
            full_tunnel = os.environ.get("BALEOBALA_FULL_TUNNEL") in {"1", "yes", "true"}
            kill_switch = os.environ.get("BALEOBALA_KILL_SWITCH") in {"1", "yes", "true"}
            if not kill_switch:
                try:
                    from baleobala.control.app_control import load_network_policy
                    policy = load_network_policy()
                    kill_switch = getattr(policy, "kill_switch_mode", "off") not in {"off", "", None}
                except Exception:
                    pass
            if full_tunnel:
                plan = TunPlan(
                    routes=("0.0.0.0/1", "128.0.0.0/1"),
                    dns_servers=("1.1.1.1", "9.9.9.9"),
                    kill_switch=kill_switch,
                )
            else:
                plan = TunPlan(kill_switch=kill_switch)
        self._plan = plan
        self._session = session or LinuxTunSession(plan=self._plan)
        self._state_store = JsonStore(state_path or (config_dir() / "linux_backend.json"))
        self._runtime_store = JsonStore(runtime_state_path or (config_dir() / "linux_runtime.json"))
        self._runtime = runtime or LinuxTunnelRuntime(self._runtime_store)
        self._runtime.set_tun_session(self._session)
        self._profile_id: str | None = None
        self._pairing_id: str | None = None
        self._pairing_store = None
        self._refresh_credentials = None

    def set_credential_refresh_context(self, pairing_store, refresh_credentials) -> None:  # noqa: ANN001
        self._pairing_store = pairing_store
        self._refresh_credentials = refresh_credentials
        self._runtime.set_credential_refresh_context(pairing_store, refresh_credentials)

    def up(self, profile, auth_record=None, pairing=None) -> dict[str, str]:  # noqa: ANN001
        self._session.start()
        self._profile_id = profile.profile_id
        self._pairing_id = profile.pairing_id
        session = self._session.status()
        self._state_store.save(
            BackendReadiness.for_linux_tun(
                state="starting",
                session_active=True,
                tun=session["tun"],
                address=session["address"],
                mtu=session["mtu"],
                call_established="no",
                data_flow_ok="no",
                transport_selected="",
                recovery_state="starting",
                endpoint=None,
                last_error="",
            ).to_dict()
        )

        def _start_runtime() -> None:
            if auth_record is None or (pairing is None and profile.peer_id is None):
                return
            try:
                self._runtime.start(profile, auth_record)
            except Exception as exc:  # noqa: BLE001
                self.update_runtime_status(
                    call_established="no",
                    data_flow_ok="no",
                    last_error=f"{type(exc).__name__}: {exc}",
                    transport_selected="",
                    recovery_state="failed",
                )

        if auth_record is not None and (pairing is not None or profile.peer_id is not None):
            threading.Thread(target=_start_runtime, name="linux-tun-runtime", daemon=True).start()

        runtime = self._runtime_status()
        payload = BackendReadiness.for_linux_tun(
            state="running" if self._session.active else "stopped",
            session_active=self._session.active,
            tun=session["tun"],
            address=session["address"],
            mtu=session["mtu"],
            call_established=runtime.get("call_established", "no"),
            data_flow_ok=runtime.get("data_flow_ok", "no"),
            transport_selected=runtime.get("transport_selected", ""),
            recovery_state=runtime.get("recovery_state", ""),
            transport_previous=runtime.get("transport_previous", ""),
            failover_count=runtime.get("failover_count", "0"),
            recovering_since=runtime.get("recovering_since", ""),
            carrier_session_id=runtime.get("carrier_session_id", ""),
            peer_coordination=runtime.get("peer_coordination", ""),
            endpoint=runtime.get("endpoint"),
            last_error=runtime.get("last_error", ""),
        ).to_dict()
        payload["profile_id"] = profile.profile_id
        payload["pairing_id"] = profile.pairing_id or ""
        payload["active"] = session["active"]
        payload["tun"] = session["tun"]
        payload["address"] = session["address"]
        payload["mtu"] = session["mtu"]
        payload["routes"] = session.get("routes", "")
        payload["routing_policy"] = session.get("routing_policy", "")
        payload["full_tunnel"] = session.get("full_tunnel", "no")
        payload["kill_switch"] = session.get("kill_switch", "no")
        payload["kill_switch_backend"] = session.get("kill_switch_backend", "")
        for key in (
            "transport_selected",
            "transport_previous",
            "recovery_state",
            "failover_count",
            "recovering_since",
            "carrier_session_id",
            "peer_coordination",
            "last_error",
            "endpoint",
        ):
            if runtime.get(key):
                payload[key] = str(runtime[key])
        payload["state"] = "running" if self._session.active else "stopped"
        self._state_store.save(payload)
        return payload

    def down(self) -> None:
        self._runtime.stop()
        self._session.stop()
        self._profile_id = None
        self._pairing_id = None
        self.clear_runtime_status()
        # Remove every linux backend state file so a crashed prior run
        # can't masquerade as "already running" and short-circuit a fresh
        # `vpn up`.
        for path in (
            self._state_store.path,
            self._runtime_store.path,
            config_dir() / "linux_tun_session.json",
            config_dir() / "linux_resolver.json",
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def status(self) -> dict[str, str]:
        payload = self._state_store.load(default=None)
        if isinstance(payload, dict):
            merged = {str(k): str(v) for k, v in payload.items()}
            runtime = self._runtime_status()
            for key in (
                "call_established",
                "data_flow_ok",
                "transport_selected",
                "transport_previous",
                "recovery_state",
                "failover_count",
                "recovering_since",
                "carrier_session_id",
                "peer_coordination",
                "last_error",
                "endpoint",
            ):
                if key in runtime and runtime[key] not in {None, ""}:
                    merged[key] = str(runtime[key])
            return merged
        session = self._session.status()
        runtime = self._runtime_status()
        return BackendReadiness.for_linux_tun(
            state="stopped",
            session_active=False,
            tun=session["tun"],
            address=session["address"],
            mtu=session["mtu"],
            call_established=runtime.get("call_established", "no"),
            data_flow_ok=runtime.get("data_flow_ok", "no"),
            transport_selected=runtime.get("transport_selected", ""),
            recovery_state=runtime.get("recovery_state", ""),
            transport_previous=runtime.get("transport_previous", ""),
            failover_count=runtime.get("failover_count", "0"),
            recovering_since=runtime.get("recovering_since", ""),
            carrier_session_id=runtime.get("carrier_session_id", ""),
            peer_coordination=runtime.get("peer_coordination", ""),
            endpoint=runtime.get("endpoint"),
            last_error=runtime.get("last_error", ""),
        ).to_dict()

    def probe(self, *, timeout: float = 1.0) -> ProbeResult:
        return probe_endpoint(self.status().get("endpoint"), timeout=timeout)

    def update_runtime_status(self, **fields: str) -> None:
        payload = {str(k): str(v) for k, v in self._runtime_store.load(default={}).items()}
        payload.update({str(k): str(v) for k, v in fields.items() if v is not None})
        self._runtime_store.save(payload)

    def clear_runtime_status(self) -> None:
        try:
            self._runtime_store.path.unlink()
        except FileNotFoundError:
            pass

    def _runtime_status(self) -> dict[str, str]:
        payload = self._runtime_store.load(default={})
        if not isinstance(payload, dict):
            return {}
        return {str(k): str(v) for k, v in payload.items()}
