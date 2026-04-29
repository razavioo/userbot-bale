"""Automation-friendly smoke checks for control-plane backends."""

from __future__ import annotations

from dataclasses import dataclass

from baleobala.control.probe import ProbeResult, probe_endpoint


@dataclass(frozen=True)
class SmokeReport:
    ok: bool
    backend: str
    state: str
    endpoint: str
    control_ready: str
    carrier_ready: str
    bypass_ready: str
    data_path_ready: str
    route_ready: str
    dns_ready: str
    probe_ok: str
    probe_kind: str
    probe_detail: str
    last_error: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ok": "yes" if self.ok else "no",
            "backend": self.backend,
            "state": self.state,
            "endpoint": self.endpoint,
            "control_ready": self.control_ready,
            "carrier_ready": self.carrier_ready,
            "bypass_ready": self.bypass_ready,
            "data_path_ready": self.data_path_ready,
            "route_ready": self.route_ready,
            "dns_ready": self.dns_ready,
            "probe_ok": self.probe_ok,
            "probe_kind": self.probe_kind,
            "probe_detail": self.probe_detail,
            "last_error": self.last_error,
        }


def smoke_backend_status(status: dict[str, str], *, timeout: float = 1.0) -> SmokeReport:
    endpoint = status.get("endpoint", "")
    probe = probe_endpoint(endpoint or None, timeout=timeout)
    return _build_report(status, probe)


def _build_report(status: dict[str, str], probe: ProbeResult) -> SmokeReport:
    control_ready = status.get("control_ready", "no")
    carrier_ready = status.get("carrier_ready", "no")
    bypass_ready = status.get("bypass_ready", "no")
    data_path_ready = status.get("data_path_ready", "no")
    route_ready = status.get("route_ready", "no")
    dns_ready = status.get("dns_ready", "no")
    call_established = status.get("call_established", "no")
    data_flow_ok = status.get("data_flow_ok", "no")
    state = status.get("state", "stopped")
    backend = status.get("backend", "")
    last_error = status.get("last_error", "")

    if carrier_ready == "no" and call_established == "yes":
        carrier_ready = "yes"
    if bypass_ready == "no" and route_ready == "yes" and dns_ready == "yes" and state == "running":
        bypass_ready = "yes"

    ok = control_ready == "yes" and not last_error
    if status.get("endpoint"):
        ok = ok and probe.ok
    if backend == "linux-tun":
        ok = ok and route_ready == "yes" and dns_ready == "yes" and carrier_ready == "yes" and bypass_ready == "yes"
    if backend in {"packet-tunnel", "android-vpn"}:
        ok = ok and call_established == "yes"
    if backend in {"direct", "windows-proxy"}:
        ok = ok and data_path_ready == "yes"

    return SmokeReport(
        ok=ok,
        backend=backend,
        state=state,
        endpoint=status.get("endpoint", ""),
        control_ready=control_ready,
        carrier_ready=carrier_ready,
        bypass_ready=bypass_ready,
        data_path_ready=data_path_ready,
        route_ready=route_ready,
        dns_ready=dns_ready,
        probe_ok="yes" if probe.ok else "no",
        probe_kind=probe.kind,
        probe_detail=probe.detail,
        last_error=last_error,
    )
