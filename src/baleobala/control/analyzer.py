"""Deterministic bundle analysis for netns session verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class BundleAnalysis:
    classification: str
    reason: str
    bundle_path: str
    ok: str

    def to_dict(self) -> dict[str, str]:
        return {
            "classification": self.classification,
            "reason": self.reason,
            "bundle_path": self.bundle_path,
            "ok": self.ok,
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

    if not started or not stopped or not scenario:
        return BundleAnalysis("infra_flake", "incomplete bundle", str(path), "no")
    if str(setup.get("ok", "no")) != "yes":
        return BundleAnalysis("infra_flake", "setup failed", str(path), "no")
    if str(teardown.get("ok", "no")) != "yes":
        return BundleAnalysis("infra_flake", "teardown failed", str(path), "no")
    if failure_class in {"carrier_instability", "auth_expired", "call_timeout", "transport_timeout"}:
        return BundleAnalysis("carrier_instability", failure_class, str(path), "no")
    if failure_class in {"permission_denied", "missing_tun", "missing_iproute", "teardown_leak"}:
        return BundleAnalysis("infra_flake", failure_class, str(path), "no")
    if str(smoke.get("ok", "no")) != "yes":
        last_error = str(smoke.get("last_error", ""))
        if "expired" in last_error or "credential" in last_error or "peer" in last_error or "negotiation" in last_error:
            return BundleAnalysis("carrier_instability", last_error or "carrier negotiation failure", str(path), "no")
        if markers and any(marker.startswith("transport_selected") for marker in markers) and "call_established" in markers:
            return BundleAnalysis("product_bug", last_error or "payload flow failed", str(path), "no")
        return BundleAnalysis("infra_flake", last_error or "smoke failed", str(path), "no")
    if "call_established" not in markers and not any("call_established" in tail for tail in log_tails.values()):
        return BundleAnalysis("infra_flake", "missing call_established marker", str(path), "no")
    return BundleAnalysis("accepted_flow", "bundle indicates accepted flow", str(path), "yes")


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
