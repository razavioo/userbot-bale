from __future__ import annotations

from baleobala.control.smoke import smoke_backend_status


def test_smoke_backend_status_accepts_ready_linux_tun() -> None:
    report = smoke_backend_status(
        {
            "backend": "linux-tun",
            "state": "running",
            "control_ready": "yes",
            "route_ready": "yes",
            "dns_ready": "yes",
            "carrier_ready": "yes",
            "bypass_ready": "yes",
            "last_error": "",
        }
    )
    assert report.ok
    assert report.probe_ok == "no"


def test_smoke_backend_status_requires_all_health_checks_for_linux_tun() -> None:
    report = smoke_backend_status(
        {
            "backend": "linux-tun",
            "state": "running",
            "control_ready": "yes",
            "route_ready": "yes",
            "dns_ready": "yes",
            "carrier_ready": "no",
            "bypass_ready": "yes",
            "last_error": "",
        }
    )
    assert not report.ok


def test_smoke_backend_status_full_tunnel_opt_in() -> None:
    report = smoke_backend_status(
        {
            "backend": "linux-tun",
            "state": "running",
            "control_ready": "yes",
            "route_ready": "yes",
            "dns_ready": "yes",
            "carrier_ready": "yes",
            "bypass_ready": "yes",
            "routing_policy": "full-tunnel",
            "full_tunnel": "yes",
            "last_error": "",
        }
    )
    assert report.ok


def test_smoke_backend_status_requires_data_path_for_direct() -> None:
    report = smoke_backend_status(
        {
            "backend": "direct",
            "state": "running",
            "control_ready": "yes",
            "data_path_ready": "no",
            "last_error": "",
        }
    )
    assert not report.ok


def test_smoke_backend_status_requires_probe_when_endpoint_present() -> None:
    report = smoke_backend_status(
        {
            "backend": "packet-tunnel",
            "state": "running",
            "control_ready": "yes",
            "endpoint": "unix:///tmp/does-not-exist.sock",
            "last_error": "",
        },
        timeout=0.01,
    )
    assert not report.ok
    assert report.probe_ok == "no"


def test_smoke_backend_status_requires_call_for_android_vpn() -> None:
    report = smoke_backend_status(
        {
            "backend": "android-vpn",
            "state": "running",
            "control_ready": "yes",
            "call_established": "no",
            "last_error": "",
        }
    )
    assert not report.ok


def test_smoke_vpn_up_down_lifecycle(tmp_path) -> None:
    # A complete local up/down smoke test bridging backend and smoke checks
    from baleobala.control.linux import LinuxTunBackend, LinuxTunSession, TunPlan
    from baleobala.control.resolver import NullResolver
    from baleobala.control.vpn import VpnProfile
    from tests.test_linux_backend import FakeRunner

    runner = FakeRunner(existing_devices=("vpn0",))
    session = LinuxTunSession(
        plan=TunPlan.full_tunnel(name="vpn0"),
        state_path=tmp_path / "s.json",
        runner=runner,
        resolver=NullResolver(),
        tun_opener=lambda name: object(),
    )
    backend = LinuxTunBackend(session=session, state_path=tmp_path / "b.json")
    profile = VpnProfile(profile_id="p", name="p", backend="linux-tun")
    
    # Pre-up smoke test
    report = smoke_backend_status(backend.status())
    assert not report.ok
    assert report.state == "stopped"

    # Bring up
    backend.up(profile)
    # Simulate runtime establishing carrier
    backend.update_runtime_status(call_established="yes")
    
    status = backend.status()
    # Smoke check on running backend
    report = smoke_backend_status(status)
    assert report.ok
    assert report.carrier_ready == "yes"
    
    # Bring down
    backend.down()
    report = smoke_backend_status(backend.status())
    assert not report.ok
    assert report.state == "stopped"
