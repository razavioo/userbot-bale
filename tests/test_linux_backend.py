"""Unit tests for Linux VPN backend and resolver (fake runner, no real kernel)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from baleobala.control.linux import LinuxKillSwitch, LinuxTunBackend, LinuxTunSession, TunPlan
from baleobala.control.resolver import (
    LinuxResolver,
    MacOSResolver,
    NullResolver,
    RoutePlan,
)
from baleobala.control.vpn import VpnProfile


class FakeRunner:
    def __init__(self, *, existing_devices: tuple[str, ...] = (), has_resolvectl: bool = False) -> None:
        self.calls: list[list[str]] = []
        self._existing = set(existing_devices)
        self._has_resolvectl = has_resolvectl

    def __call__(self, cmd, check=False, capture_output=True, text=True):  # noqa: ARG002
        self.calls.append(list(cmd))
        stdout = ""
        rc = 0
        if cmd[:3] == ["ip", "link", "show"]:
            dev = cmd[3]
            if dev in self._existing:
                stdout = f"2: {dev}: <BROADCAST,MULTICAST,UP> mtu 1500 state UP"
            else:
                rc = 1
        elif cmd[:4] == ["ip", "-o", "-4", "addr"]:
            stdout = ""
        elif cmd[:2] == ["ip", "tuntap"]:
            if cmd[2] == "add":
                self._existing.add(cmd[4])
            elif cmd[2] == "del":
                self._existing.discard(cmd[4])
        elif cmd[:1] == ["which"]:
            if cmd[1] == "resolvectl" and self._has_resolvectl:
                stdout = "/usr/bin/resolvectl\n"
                rc = 0
            else:
                rc = 1
        elif cmd[:1] == ["resolvectl"]:
            stdout = "Link 5: DNS Servers: \n"
        elif cmd[:1] == ["route"]:
            pass
        elif cmd[:1] == ["networksetup"]:
            if cmd[1] == "-listallnetworkservices":
                stdout = "An asterisk denotes disabled.\nWi-Fi\n"
            elif cmd[1] == "-getdnsservers":
                stdout = "There aren't any DNS Servers set on Wi-Fi.\n"
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")


def test_null_resolver_toggles_active() -> None:
    r = NullResolver()
    assert not r.active()
    r.configure(RoutePlan(dns_servers=("1.1.1.1",)))
    assert r.active()
    r.restore()
    assert not r.active()


def test_linux_resolver_applies_and_restores_resolvectl(tmp_path: Path) -> None:
    runner = FakeRunner(has_resolvectl=True)
    r = LinuxResolver(state_path=tmp_path / "s.json", runner=runner)
    r.configure(RoutePlan(dns_servers=("1.1.1.1",), routes=("10.0.0.0/8",), interface="vpn0"))
    assert r.active()
    assert ["resolvectl", "dns", "vpn0", "1.1.1.1"] in runner.calls
    assert ["ip", "route", "add", "10.0.0.0/8", "dev", "vpn0"] in runner.calls
    r.restore()
    assert not r.active()
    assert ["ip", "route", "del", "10.0.0.0/8", "dev", "vpn0"] in runner.calls


def test_linux_tun_session_creates_and_teardown(tmp_path: Path) -> None:
    runner = FakeRunner(has_resolvectl=True)
    session = LinuxTunSession(
        plan=TunPlan(name="vpntest", address="10.77.0.2/24", routes=("0.0.0.0/1",), dns_servers=("1.1.1.1",)),
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=LinuxResolver(state_path=tmp_path / "r.json", runner=runner),
        tun_opener=lambda name: object(),
    )
    session.start()
    assert session.active
    assert any(c[:4] == ["ip", "tuntap", "add", "dev"] for c in runner.calls)
    assert any(c[:3] == ["ip", "link", "set"] and "up" in c for c in runner.calls)
    session.stop()
    assert not session.active
    assert any(c[:4] == ["ip", "tuntap", "del", "dev"] for c in runner.calls)


def test_linux_tun_session_ignores_best_effort_bypass_resolution_failures(tmp_path: Path) -> None:
    runner = FakeRunner(has_resolvectl=True)

    class FailingResolver(NullResolver):
        def add_bypass_host(self, hostname: str) -> None:  # noqa: ARG002
            raise RuntimeError("dns lookup failed")

    session = LinuxTunSession(
        plan=TunPlan(name="vpntest", address="10.77.0.2/24", routes=("0.0.0.0/1",), dns_servers=("1.1.1.1",)),
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=FailingResolver(),
        tun_opener=lambda name: object(),
    )
    session.start()
    assert session.active
    session.stop()
    assert not session.active


def test_linux_tun_session_raises_without_device_or_privs(tmp_path: Path) -> None:
    class DenyingRunner(FakeRunner):
        def __call__(self, cmd, check=False, capture_output=True, text=True):  # noqa: ARG002
            if cmd[:2] == ["ip", "tuntap"] and cmd[2] == "add":
                self.calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Operation not permitted")
            return super().__call__(cmd, check=check, capture_output=capture_output, text=text)

    runner = DenyingRunner()
    session = LinuxTunSession(
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=NullResolver(),
    )
    with pytest.raises(PermissionError):
        session.start()


def test_linux_tun_backend_up_down(tmp_path: Path) -> None:
    runner = FakeRunner(existing_devices=("vpn0",))
    session = LinuxTunSession(
        plan=TunPlan(name="vpn0"),
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
    )
    backend = LinuxTunBackend(session=session, state_path=tmp_path / "b.json")
    profile = VpnProfile(profile_id="p", name="p", backend="linux-tun")
    status = backend.up(profile)
    assert status["state"] == "running"
    assert status["route_ready"] == "yes"
    assert status["dns_ready"] == "yes"
    assert backend.status()["state"] == "running"
    backend.down()
    assert backend.status()["state"] == "stopped"


def test_linux_tun_backend_full_tunnel(tmp_path: Path) -> None:
    runner = FakeRunner(existing_devices=("vpn0",))
    resolver = LinuxResolver(state_path=tmp_path / "r.json", runner=runner)
    session = LinuxTunSession(
        plan=TunPlan.full_tunnel(name="vpn0"),
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=resolver,
        tun_opener=lambda name: object(),
    )
    backend = LinuxTunBackend(session=session, state_path=tmp_path / "b.json")
    profile = VpnProfile(profile_id="p", name="p", backend="linux-tun")
    
    backend.up(profile)
    assert any(c[:4] == ["ip", "route", "add", "0.0.0.0/1"] for c in runner.calls)
    assert any(c[:4] == ["ip", "route", "add", "128.0.0.0/1"] for c in runner.calls)
    
    status = backend.status()
    assert "0.0.0.0/1" in status.get("routes", "")
    assert "128.0.0.0/1" in status.get("routes", "")
    assert status.get("routing_policy") == "full-tunnel"
    assert status.get("full_tunnel") == "yes"
    
    backend.down()


def test_linux_tun_backend_merges_recovery_runtime_fields(tmp_path: Path) -> None:
    runner = FakeRunner(existing_devices=("vpn0",))
    session = LinuxTunSession(
        plan=TunPlan(name="vpn0"),
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
    )
    backend = LinuxTunBackend(
        session=session,
        state_path=tmp_path / "b.json",
        runtime_state_path=tmp_path / "runtime.json",
    )
    profile = VpnProfile(profile_id="p", name="p", backend="linux-tun")
    backend.up(profile)
    backend.update_runtime_status(
        transport_selected="audio",
        transport_previous="dc",
        recovery_state="recovering",
        failover_count="4",
        recovering_since="123.4",
        carrier_session_id="0xabc",
        peer_coordination="active",
        call_established="yes",
        data_flow_ok="no",
    )
    status = backend.status()
    assert status["transport_selected"] == "audio"
    assert status["transport_previous"] == "dc"
    assert status["recovery_state"] == "recovering"
    assert status["failover_count"] == "4"
    assert status["carrier_session_id"] == "0xabc"
    assert status["peer_coordination"] == "active"
    backend.down()


def test_macos_resolver_configure_restore(tmp_path: Path) -> None:
    runner = FakeRunner()
    r = MacOSResolver(state_path=tmp_path / "m.json", runner=runner)
    r.configure(RoutePlan(dns_servers=("8.8.8.8",), routes=("10.0.0.0/8",), interface="utun7"))
    assert r.active()
    assert ["networksetup", "-setdnsservers", "Wi-Fi", "8.8.8.8"] in runner.calls
    r.restore()
    assert not r.active()


# ---------------------------------------------------------------------------
# LinuxKillSwitch tests
# ---------------------------------------------------------------------------

class FakeKsRunner:
    """Records calls and controls which tools are 'installed'."""

    def __init__(self, *, has_nft: bool = False, has_iptables: bool = False) -> None:
        self.calls: list[list[str]] = []
        self._has_nft = has_nft
        self._has_iptables = has_iptables
        self._nft_ruleset: str | None = None

    def __call__(self, cmd, check=False, capture_output=True, text=True, input=None):  # noqa: ARG002, A002
        self.calls.append(list(cmd))
        if input is not None:
            self._nft_ruleset = input
        rc = 0
        if cmd[:1] == ["which"]:
            if cmd[1] == "nft" and self._has_nft:
                return subprocess.CompletedProcess(cmd, 0, stdout="/usr/sbin/nft\n", stderr="")
            if cmd[1] == "iptables" and self._has_iptables:
                return subprocess.CompletedProcess(cmd, 0, stdout="/sbin/iptables\n", stderr="")
            rc = 1
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")


def test_kill_switch_nftables_apply_remove() -> None:
    runner = FakeKsRunner(has_nft=True)
    ks = LinuxKillSwitch(runner=runner, nft_runner=runner)
    ks.apply("vpn0", bypass_ips=("1.2.3.4", "5.6.7.8"))
    assert ks.active
    assert ks.backend == "nftables"
    assert any("nft" in " ".join(c) and "-f" in c for c in runner.calls)
    assert runner._nft_ruleset is not None
    assert "vpn0" in runner._nft_ruleset
    assert "1.2.3.4" in runner._nft_ruleset
    assert "5.6.7.8" in runner._nft_ruleset
    assert "drop" in runner._nft_ruleset

    ks.remove()
    assert not ks.active
    assert ks.backend is None
    assert any(c[:4] == ["nft", "delete", "table", "inet"] for c in runner.calls)


def test_kill_switch_nftables_no_bypass_ips() -> None:
    runner = FakeKsRunner(has_nft=True)
    ks = LinuxKillSwitch(runner=runner, nft_runner=runner)
    ks.apply("vpn0", bypass_ips=())
    assert runner._nft_ruleset is not None
    assert "bypass4" not in runner._nft_ruleset
    assert "drop" in runner._nft_ruleset


def test_kill_switch_iptables_fallback() -> None:
    runner = FakeKsRunner(has_nft=False, has_iptables=True)
    ks = LinuxKillSwitch(runner=runner, nft_runner=runner)
    ks.apply("vpn0", bypass_ips=("10.0.0.1",))
    assert ks.active
    assert ks.backend == "iptables"
    assert any(c == ["iptables", "-I", "OUTPUT", "-o", "vpn0", "-j", "ACCEPT"] for c in runner.calls)
    assert any(c == ["iptables", "-I", "OUTPUT", "-d", "10.0.0.1", "-j", "ACCEPT"] for c in runner.calls)
    assert any(c == ["iptables", "-A", "OUTPUT", "-j", "DROP"] for c in runner.calls)

    ks.remove()
    assert not ks.active
    assert any(c == ["iptables", "-D", "OUTPUT", "-j", "DROP"] for c in runner.calls)


def test_kill_switch_no_tool_available() -> None:
    runner = FakeKsRunner(has_nft=False, has_iptables=False)
    ks = LinuxKillSwitch(runner=runner, nft_runner=runner)
    ks.apply("vpn0")
    assert not ks.active
    assert ks.backend is None


def test_tun_session_applies_kill_switch_on_start(tmp_path: Path) -> None:
    ks_runner = FakeKsRunner(has_nft=True)
    ks = LinuxKillSwitch(runner=ks_runner, nft_runner=ks_runner)
    session = LinuxTunSession(
        plan=TunPlan(name="vpn0", routes=("0.0.0.0/1",), kill_switch=True),
        state_path=tmp_path / "s.json",
        runner=FakeRunner(existing_devices=("vpn0",), has_resolvectl=False),
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
        kill_switch=ks,
    )
    session.start()
    assert ks.active
    assert session.status()["kill_switch"] == "yes"
    assert session.status()["kill_switch_backend"] == "nftables"
    session.stop()
    assert not ks.active
    assert session.status()["kill_switch"] == "configured"


def test_tun_session_removes_kill_switch_on_stop(tmp_path: Path) -> None:
    ks_runner = FakeKsRunner(has_nft=True)
    ks = LinuxKillSwitch(runner=ks_runner, nft_runner=ks_runner)
    session = LinuxTunSession(
        plan=TunPlan(name="vpn0", kill_switch=True),
        state_path=tmp_path / "s.json",
        runner=FakeRunner(existing_devices=("vpn0",)),
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
        kill_switch=ks,
    )
    session.start()
    session.stop()
    assert not ks.active
    # Removal command was issued
    assert any(c[:4] == ["nft", "delete", "table", "inet"] for c in ks_runner.calls)


def test_tun_plan_kill_switch_propagates_to_session(tmp_path: Path) -> None:
    session = LinuxTunSession(
        plan=TunPlan(name="vpn0", kill_switch=False),
        state_path=tmp_path / "s.json",
        runner=FakeRunner(existing_devices=("vpn0",)),
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
    )
    assert session.status()["kill_switch"] == "no"


def test_linux_tun_backend_status_includes_kill_switch(tmp_path: Path) -> None:
    ks_runner = FakeKsRunner(has_nft=True)
    ks = LinuxKillSwitch(runner=ks_runner, nft_runner=ks_runner)
    session = LinuxTunSession(
        plan=TunPlan(name="vpn0", kill_switch=True),
        state_path=tmp_path / "s.json",
        runner=FakeRunner(existing_devices=("vpn0",)),
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
        kill_switch=ks,
    )
    backend = LinuxTunBackend(session=session, state_path=tmp_path / "b.json")
    profile = VpnProfile(profile_id="p", name="p", backend="linux-tun")
    backend.up(profile)
    status = backend.status()
    assert status["kill_switch"] == "yes"
    assert status["kill_switch_backend"] == "nftables"
    backend.down()
    assert not ks.active
