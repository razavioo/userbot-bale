"""Deterministic bundle analysis for netns session verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from baleobala.control.observability import classify_failure


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
    status = {
        "artifact_bundle": str(path),
        "call_established": "yes" if "call_established" in markers else "no",
        "transport_selected": next(
            (marker.split("=", 1)[1].strip() for marker in markers if marker.startswith("transport_selected=")),
            "",
        ),
        "data_flow_ok": str(smoke.get("ok", "no")),
        "teardown_clean": str(teardown.get("ok", "no")),
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


def analyze_bundle(bundle_path: str | Path) -> BundleAnalysis:
    path = Path(bundle_path)
    verdict_path = path / "verdict.json"
    if not verdict_path.exists():
        return BundleAnalysis("infra_flake", "missing verdict.json", str(path), "no")
    try:
        payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    except Exception:
        return BundleAnalysis("infra_flake", "invalid bundle json", str(path), "no")

    setup = payload.get("setup", {})
    smoke = payload.get("smoke", {})
    teardown = payload.get("teardown", {})
    scenario = payload.get("scenario", {})
    markers = set(payload.get("markers", []))
    started = payload.get("started", [])
    stopped = payload.get("stopped", [])
    log_tails = payload.get("log_tails", {})
    failure_class = str(payload.get("failure_class", ""))
    failure_code = str(payload.get("failure_code", ""))
    last_success_stage = str(payload.get("last_success_stage", ""))
    failed_stage = str(payload.get("failed_stage", ""))
    transport_selected = next(
        (marker.split("=", 1)[1].strip() for marker in markers if str(marker).startswith("transport_selected=")),
        "",
    )

    events = _read_events(path)
    retry_count = str(sum(1 for item in events if item.get("event") == "supervisor_retry_scheduled"))
    if not last_success_stage:
        for item in reversed(events):
            if str(item.get("outcome", "")) == "success" and item.get("stage"):
                last_success_stage = str(item.get("stage", ""))
                break
    if not failed_stage:
        for item in events:
            if str(item.get("outcome", "")) == "failure" and item.get("stage"):
                failed_stage = str(item.get("stage", ""))
                break

    if not started or not stopped or not scenario:
        info = classify_failure(setup_ok=False, bundle_ok=False, legacy_failure_class=failure_class)
        return BundleAnalysis("infra_flake", "incomplete bundle", str(path), "no", info.failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    if str(setup.get("ok", "no")) != "yes":
        info = classify_failure(setup_ok=False, last_error=str(setup.get("last_error", "")), legacy_failure_class=failure_class)
        return BundleAnalysis("infra_flake", "setup failed", str(path), "no", info.failure_code, last_success_stage, failed_stage or "setup", transport_selected, retry_count)
    if str(teardown.get("ok", "no")) != "yes":
        info = classify_failure(teardown_ok=False, last_error=str(teardown.get("last_error", "")), legacy_failure_class=failure_class)
        return BundleAnalysis("infra_flake", info.failure_code or "teardown failed", str(path), "no", info.failure_code, last_success_stage, failed_stage or "teardown", transport_selected, retry_count)
    if failure_class in {"auth", "call_setup", "transport_init", "transport_runtime"} or failure_code in {"jwt_expired", "call_setup_timeout", "carrier_negotiation_failed", "transport_closed_early", "transport_factory_failed", "transport_listen_setup_failed"}:
        return BundleAnalysis("carrier_instability", failure_code or failure_class, str(path), "no", failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    if failure_class in {"environment", "infra", "teardown"}:
        return BundleAnalysis("infra_flake", failure_code or failure_class, str(path), "no", failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    if str(smoke.get("ok", "no")) != "yes":
        last_error = str(smoke.get("last_error", ""))
        info = classify_failure(
            ready="call_established" in markers,
            smoke_ok=False,
            setup_ok=True,
            teardown_ok=True,
            last_error=last_error,
            bundle_ok=True,
            markers=list(markers),
            legacy_failure_class=failure_class,
        )
        if info.failure_class in {"auth", "call_setup", "transport_init", "transport_runtime"}:
            return BundleAnalysis("carrier_instability", last_error or info.failure_code, str(path), "no", info.failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
        if markers and any(str(marker).startswith("transport_selected") for marker in markers) and "call_established" in markers:
            return BundleAnalysis("product_bug", last_error or "payload flow failed", str(path), "no", info.failure_code or "payload_probe_failed", last_success_stage, failed_stage or "smoke", transport_selected, retry_count)
        return BundleAnalysis("infra_flake", last_error or "smoke failed", str(path), "no", info.failure_code, last_success_stage, failed_stage, transport_selected, retry_count)
    if "call_established" not in markers and not any("call_established" in tail for tail in log_tails.values()):
        return BundleAnalysis("infra_flake", "missing call_established marker", str(path), "no", failure_code, last_success_stage, failed_stage or "call_setup", transport_selected, retry_count)
    return BundleAnalysis("accepted_flow", "bundle indicates accepted flow", str(path), "yes", failure_code, last_success_stage, failed_stage, transport_selected, retry_count)


def build_product_verdict(status: dict[str, str], bundle_analysis: BundleAnalysis | None = None) -> ProductVerdict:
    bundle = bundle_analysis or BundleAnalysis(
        classification="infra_flake",
        reason="missing bundle analysis",
        bundle_path=str(status.get("artifact_bundle", "")),
        ok="no",
    )
    ok = "yes" if status.get("call_established") == "yes" and status.get("data_flow_ok") == "yes" and status.get("teardown_clean", "no") == "yes" and bundle.ok == "yes" else "no"
    failure_class = status.get("failure_class") or ("" if bundle.ok == "yes" else bundle.classification)
    return ProductVerdict(
        ok=ok,
        backend=str(status.get("backend", "")),
        state=str(status.get("state", "stopped")),
        call_established=str(status.get("call_established", "no")),
        transport_selected=str(status.get("transport_selected", "")),
        data_flow_ok=str(status.get("data_flow_ok", "no")),
        teardown_clean=str(status.get("teardown_clean", "no")),
        failure_class=failure_class,
        artifact_bundle=str(status.get("artifact_bundle", bundle.bundle_path)),
        analysis_classification=bundle.classification,
        analysis_reason=bundle.reason,
    )
