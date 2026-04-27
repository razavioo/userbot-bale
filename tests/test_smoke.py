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
            "last_error": "",
        }
    )
    assert report.ok
    assert report.probe_ok == "no"


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
