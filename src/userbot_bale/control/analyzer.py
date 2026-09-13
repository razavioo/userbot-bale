"""Deterministic bundle analysis for netns session verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from userbot_bale.control.observability import classify_failure


@dataclass(frozen=True)
class BundleAnalysis:
    classification: str
    reason: str
    bundle_path: str
    ok: str
    failure_code: str = ""
    last_success_stage: str = ""
    failed_stage: str = ""
    transport_selected: str = ""
    retry_count: str = "0"

    def to_dict(self) -> dict[str, str]:
        return {
            "classification": self.classification,
            "reason": self.reason,
            "bundle_path": self.bundle_path,
            "ok": self.ok,
            "failure_code": self.failure_code,
            "last_success_stage": self.last_success_stage,
            "failed_stage": self.failed_stage,
            "transport_selected": self.transport_selected,
            "retry_count": self.retry_count,
        }


def _bundle_marker_transport(markers: list[str]) -> str:
    return next(
        (marker.split("=", 1)[1].strip() for marker in markers if marker.startswith("transport_selected=")),
        "",
    )


def _route_and_dns_readiness(
    scenario: dict[str, object],
    route_snapshot: dict[str, object],
    dns_snapshot: dict[str, object],
) -> tuple[str, str]:
    failure_code = _classify_snapshot_failure(scenario, route_snapshot, dns_snapshot)
    full_device_checks = [str(item) for item in scenario.get("full_device_checks", [])]
    route_ready = "yes"
    dns_ready = "yes"
    if "route" in full_device_checks:
        route_ready = "no" if failure_code == "route_program_failed" else "yes"
    if "dns" in full_device_checks:
        dns_ready = "no" if failure_code == "dns_config_failed" else "yes"
    return route_ready, dns_ready


def _carrier_and_egress_readiness(
    scenario: dict[str, object],
    route_snapshot: dict[str, object],
    smoke: dict[str, object],
) -> tuple[str, str]:
    full_device_checks = [str(item) for item in scenario.get("full_device_checks", [])]
    network_plan = scenario.get("network_plan", {})
    if not isinstance(network_plan, dict):
        network_plan = {}

    client_routes = _snapshot_stdout(route_snapshot, "client")
    carrier_hosts = [str(item) for item in network_plan.get("carrier_hosts", [])]
    setup_host_routes = bool(network_plan.get("setup_host_routes", False))

    if setup_host_routes and carrier_hosts:
        carrier_bypass_ready = "yes" if all(host in client_routes for host in carrier_hosts) else "no"
    else:
        carrier_bypass_ready = "no"

    if "tcp-egress" in full_device_checks:
        egress_ready = "yes" if str(smoke.get("ok", "no")) == "yes" else "no"
    else:
        egress_ready = "no"

    return carrier_bypass_ready, egress_ready


def bundle_status(bundle_path: str | Path) -> dict[str, str]:
    path = Path(bundle_path)
    verdict_path = path / "verdict.json"
    if not verdict_path.exists():
        return {}
    try:
        payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    markers = [str(item) for item in payload.get("markers", [])]
    smoke = payload.get("smoke", {})
    teardown = payload.get("teardown", {})
    scenario = payload.get("scenario", {})
    route_snapshot = _read_json_artifact(path, "route_snapshot.json")
    dns_snapshot = _read_json_artifact(path, "dns_snapshot.json")
    route_ready, dns_ready = _route_and_dns_readiness(
        scenario if isinstance(scenario, dict) else {},
        route_snapshot,
        dns_snapshot,
    )
    carrier_bypass_ready, egress_ready = _carrier_and_egress_readiness(
        scenario if isinstance(scenario, dict) else {},
        route_snapshot,
        smoke if isinstance(smoke, dict) else {},
    )
    status = {
        "artifact_bundle": str(path),
        "call_established": "yes" if "call_established" in markers else "no",
        "transport_selected": _bundle_marker_transport(markers),
        "data_flow_ok": str(smoke.get("ok", "no")),
        "teardown_clean": str(teardown.get("ok", "no")),
        "route_ready": route_ready,
        "dns_ready": dns_ready,
        "carrier_bypass_ready": carrier_bypass_ready,
        "egress_ready": egress_ready,
        "failure_class": str(payload.get("failure_class", "")),
        "failure_code": str(payload.get("failure_code", "")),
        "run_id": str(payload.get("run_id", "")),
        "last_success_stage": str(payload.get("last_success_stage", "")),
        "failed_stage": str(payload.get("failed_stage", "")),
    }
    if "scenario" in payload:
        scenario = payload.get("scenario", {})
        if isinstance(scenario, dict):
            status["backend"] = str(scenario.get("kind", ""))
    return status


def merge_status_with_bundle(status: dict[str, str], bundle_path: str | Path) -> dict[str, str]:
    merged = {str(key): str(value) for key, value in status.items()}
    for key, value in bundle_status(bundle_path).items():
        if value != "":
            merged[key] = value
    return merged


@dataclass(frozen=True)
class ProductVerdict:
    ok: str
    backend: str
    state: str
    call_established: str
    transport_selected: str
    data_flow_ok: str
    teardown_clean: str
    route_ready: str
    dns_ready: str
    carrier_bypass_ready: str
    egress_ready: str
    failure_class: str
    artifact_bundle: str
    analysis_classification: str
    analysis_reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "state": self.state,
            "call_established": self.call_established,
            "transport_selected": self.transport_selected,
            "data_flow_ok": self.data_flow_ok,
            "teardown_clean": self.teardown_clean,
            "route_ready": self.route_ready,
            "dns_ready": self.dns_ready,
            "carrier_bypass_ready": self.carrier_bypass_ready,
            "egress_ready": self.egress_ready,
            "failure_class": self.failure_class,
            "artifact_bundle": self.artifact_bundle,
            "analysis_classification": self.analysis_classification,
            "analysis_reason": self.analysis_reason,
        }


def _read_events(path: Path) -> list[dict[str, object]]:
    events_path = path / "events.jsonl"
    if not events_path.exists():
        return []
    try:
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception:
        return []


def _read_json_artifact(path: Path, name: str) -> dict[str, object]:
    artifact = path / name
    if not artifact.exists():
        return {}
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _snapshot_stdout(snapshot: dict[str, object], side: str) -> str:
    entry = snapshot.get(side, {})
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("stdout", ""))


def _classify_snapshot_failure(
    scenario: dict[str, object],
    route_snapshot: dict[str, object],
    dns_snapshot: dict[str, object],
) -> str:
    full_device_checks = [str(item) for item in scenario.get("full_device_checks", [])]
    network_plan = scenario.get("network_plan", {})
    if not isinstance(network_plan, dict):
        network_plan = {}
    client_plan = network_plan.get("client", {})
    if not isinstance(client_plan, dict):
        client_plan = {}

    client_routes = _snapshot_stdout(route_snapshot, "client")
    client_dns = _snapshot_stdout(dns_snapshot, "client")
    tun_name = str(client_plan.get("tun", "vpn0"))
    expected_routes = [str(item) for item in client_plan.get("routes", [])]
    dns_servers = [str(item) for item in network_plan.get("dns_servers", [])]

    if "route" in full_device_checks:
        has_default_route = f"default via 10.77.0.1 dev {tun_name}" in client_routes
        has_split_default = bool(expected_routes) and all(route in client_routes for route in expected_routes)
        if not client_routes.strip() or not (has_default_route or has_split_default):
            return "route_program_failed"
    if "dns" in full_device_checks and dns_servers:
        if not client_dns.strip() or not all(f"nameserver {server}" in client_dns for server in dns_servers):
            return "dns_config_failed"
    return ""


def analyze_bundle(bundle_path: str | Path) -> BundleAnalysis:
    path = Path(bundle_path)
    verdict_path = path / "verdict.json"
    if not verdict_path.exists():
        return BundleAnalysis("infra_flake", "missing verdict.json", str(path), "no")
    try:
        payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    except Exception:
        return BundleAnalysis("infra_flake", "invalid JSON", str(path), "no")

    required = ["summary.json", "scenario.json", "environment.json", "events.jsonl", "process_status.json", "markers.json", "smoke.json", "teardown.json", "server.log", "client.log", "route_snapshot.json", "dns_snapshot.json", "command_transcript.json"]
    if any(not (path / name).exists() for name in required):
        return BundleAnalysis("infra_flake", "missing artifacts", str(path), "no")

    markers = [str(item) for item in payload.get("markers", [])]
    smoke = payload.get("smoke", {})
    teardown = payload.get("teardown", {})
    scenario = payload.get("scenario", {})
    started = payload.get("started", [])
    stopped = payload.get("stopped", [])
    failure_class = str(payload.get("failure_class", ""))
    failure_code = str(payload.get("failure_code", ""))
    last_success_stage = str(payload.get("last_success_stage", ""))
    failed_stage = str(payload.get("failed_stage", ""))
    transport_selected = _bundle_marker_transport(markers)
    events = _read_events(path)
    retry_count = str(sum(1 for item in events if item.get("event") == "supervisor_retry_scheduled"))
    route_snapshot = _read_json_artifact(path, "route_snapshot.json")
    dns_snapshot = _read_json_artifact(path, "dns_snapshot.json")

    if failure_code in {"jwt_expired", "peer_lookup_failed"}:
        return BundleAnalysis("carrier_instability", failure_code, str(path), "no", failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    if failure_code in {"call_setup_timeout", "carrier_negotiation_failed", "transport_listen_setup_failed", "transport_closed_early"}:
        return BundleAnalysis("carrier_instability", failure_code, str(path), "no", failure_code, last_success_stage, failed_stage or "call_setup", transport_selected, retry_count)
    if failure_code in {"missing_iproute", "missing_tun", "permission_denied"}:
        return BundleAnalysis("infra_flake", failure_code, str(path), "no", failure_code, last_success_stage, failed_stage or "setup", transport_selected, retry_count)
    if not started or not stopped:
        return BundleAnalysis("infra_flake", "missing artifacts", str(path), "no", failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    snapshot_failure_code = _classify_snapshot_failure(
        scenario if isinstance(scenario, dict) else {},
        route_snapshot,
        dns_snapshot,
    )
    if snapshot_failure_code:
        return BundleAnalysis("infra_flake", snapshot_failure_code, str(path), "no", snapshot_failure_code, last_success_stage, failed_stage or "smoke", transport_selected, retry_count)
    if str(smoke.get("ok", "no")) != "yes":
        if "call_established" in markers and transport_selected:
            return BundleAnalysis("product_bug", str(smoke.get("last_error", "payload verification failed")), str(path), "no", failure_code or "payload_probe_failed", last_success_stage, failed_stage or "smoke", transport_selected, retry_count)
        return BundleAnalysis("infra_flake", str(smoke.get("last_error", "smoke failed")), str(path), "no", failure_code, last_success_stage, failed_stage or "smoke", transport_selected, retry_count)
    if str(teardown.get("ok", "no")) != "yes":
        return BundleAnalysis("infra_flake", str(teardown.get("last_error", "teardown leak")), str(path), "no", failure_code or "teardown_leak", last_success_stage, failed_stage or "teardown", transport_selected, retry_count)
    if "call_established" in markers and transport_selected and str(smoke.get("ok", "no")) == "yes" and str(teardown.get("ok", "no")) == "yes":
        return BundleAnalysis("accepted_flow", "bundle indicates accepted flow", str(path), "yes", failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    return BundleAnalysis("product_bug", "bundle did not meet acceptance contract", str(path), "no", failure_code or "payload_probe_failed", last_success_stage, failed_stage, transport_selected, retry_count)


def build_product_verdict(status: dict[str, str], bundle_analysis: BundleAnalysis | None = None) -> ProductVerdict:
    bundle = bundle_analysis or BundleAnalysis(
        classification="infra_flake",
        reason="missing bundle analysis",
        bundle_path=str(status.get("artifact_bundle", "")),
        ok="no",
    )
    ok = "yes" if status.get("call_established") == "yes" and status.get("data_flow_ok") == "yes" and status.get("teardown_clean", "no") == "yes" and bundle.classification == "accepted_flow" else "no"
    failure_class = status.get("failure_class") or ("" if bundle.ok == "yes" else bundle.classification)
    return ProductVerdict(
        ok=ok,
        backend=str(status.get("backend", "")),
        state=str(status.get("state", "stopped")),
        call_established=str(status.get("call_established", "no")),
        transport_selected=str(status.get("transport_selected", "")),
        data_flow_ok=str(status.get("data_flow_ok", "no")),
        teardown_clean=str(status.get("teardown_clean", "no")),
        route_ready=str(status.get("route_ready", "no")),
        dns_ready=str(status.get("dns_ready", "no")),
        carrier_bypass_ready=str(status.get("carrier_bypass_ready", "no")),
        egress_ready=str(status.get("egress_ready", "no")),
        failure_class=failure_class,
        artifact_bundle=str(status.get("artifact_bundle", bundle.bundle_path)),
        analysis_classification=bundle.classification,
        analysis_reason=bundle.reason,
    )
