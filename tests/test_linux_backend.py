"""Unit tests for Linux VPN backend and resolver (fake runner, no real kernel)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from baleobala.control.linux import LinuxTunBackend, LinuxTunSession, TunPlan
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
