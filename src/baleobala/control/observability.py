"""Shared observability helpers for structured events and failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import json
import platform
import re
import sys
import time
import uuid


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization)\s*[:=]\s*([^\s,]+)"),
    re.compile(r"(?i)(cookie)\s*[:=]\s*([^\n]+)"),
    re.compile(r"(?i)(jwt|token|secret|psk|proxy_secret)\s*[:=]\s*([^\s,]+)"),
    re.compile(r"(?i)(bale-jwt(?:-file)?)\s+([^\s]+)"),
)


@dataclass(frozen=True)
class FailureInfo:
    failure_class: str
    failure_code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "failure_class": self.failure_class,
            "failure_code": self.failure_code,
        }


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
        return redacted
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"jwt", "token", "secret", "proxy_secret", "psk", "authorization", "cookie"}:
                out[key_text] = f"{key_text}=<redacted>"
            else:
                out[key_text] = redact_value(item)
        return out
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles if needle)


def classify_failure(
    *,
    ready: bool = False,
    smoke_ok: bool = False,
    setup_ok: bool = True,
    teardown_ok: bool = True,
    bundle_ok: bool = True,
    last_error: str = "",
    markers: list[str] | None = None,
    legacy_failure_class: str = "",
) -> FailureInfo:
    text = (last_error or "").lower()
    markers = markers or []
    legacy = (legacy_failure_class or "").strip()

    legacy_map = {
        "auth_expired": FailureInfo("auth", "jwt_expired"),
        "call_timeout": FailureInfo("call_setup", "call_setup_timeout"),
        "transport_timeout": FailureInfo("transport_runtime", "transport_closed_early"),
        "teardown_leak": FailureInfo("teardown", "teardown_leak"),
        "permission_denied": FailureInfo("environment", "permission_denied"),
        "missing_tun": FailureInfo("environment", "missing_tun"),
        "missing_iproute": FailureInfo("environment", "missing_iproute"),
        "missing_iptables": FailureInfo("environment", "missing_iptables"),
        "route_program_failed": FailureInfo("environment", "route_program_failed"),
        "dns_config_failed": FailureInfo("environment", "dns_config_failed"),
        "nat_setup_failed": FailureInfo("environment", "nat_setup_failed"),
        "host_route_failed": FailureInfo("environment", "host_route_failed"),
        "carrier_instability": FailureInfo("call_setup", "carrier_negotiation_failed"),
        "infra_flake": FailureInfo("infra", "artifact_incomplete"),
        "product_bug": FailureInfo("payload_flow", "payload_probe_failed"),
    }
    if legacy in legacy_map:
        return legacy_map[legacy]

    if _contains_any(text, "no paired relay is ready", "no relay pairing is ready", "pairing missing", "relay pairing needed"):
        return FailureInfo("auth", "pairing_missing")
    if _contains_any(text, "saved bale session expired", "session expired", "auth expired", "jwt_expired"):
        return FailureInfo("auth", "jwt_expired")
    if _contains_any(text, "relay access was revoked", "device revoked", "access_revoked"):
        return FailureInfo("auth", "device_revoked")
    if _contains_any(text, "relay provisioning is not complete", "relay provisioning is incomplete", "provisioning_pending", "waiting for relay-owner approval"):
        return FailureInfo("auth", "relay_provisioning_pending")
    if _contains_any(text, "relay credentials need a refresh", "relay credentials are missing", "credentials missing", "credentials expired"):
        return FailureInfo("auth", "relay_credentials_missing")

    if _contains_any(text, "expired", "jwt", "credential expired", "token expired"):
        return FailureInfo("auth", "jwt_expired")
    if _contains_any(text, "permission denied", "operation not permitted"):
        return FailureInfo("environment", "permission_denied")
    if _contains_any(text, "/dev/net/tun", "missing_tun", "no such device"):
        return FailureInfo("environment", "missing_tun")
    if _contains_any(text, "ip: not found", "missing_iproute", "iproute"):
        return FailureInfo("environment", "missing_iproute")
    if _contains_any(text, "iptables: not found", "missing_iptables"):
        return FailureInfo("environment", "missing_iptables")
    if _contains_any(text, "route_program_failed"):
        return FailureInfo("environment", "route_program_failed")
    if _contains_any(text, "dns_config_failed"):
        return FailureInfo("environment", "dns_config_failed")
    if _contains_any(text, "nat_setup_failed"):
        return FailureInfo("environment", "nat_setup_failed")
    if _contains_any(text, "host_route_failed"):
        return FailureInfo("environment", "host_route_failed")
    if _contains_any(text, "no contacts match", "peer lookup", "peer_id", "peer"):
        return FailureInfo("call_setup", "peer_lookup_failed")
    if _contains_any(text, "livekit credentials", "no inline credentials", "creds"):
        return FailureInfo("call_setup", "livekit_creds_missing")
    if _contains_any(text, "listen_messages setup failed"):
        return FailureInfo("transport_init", "transport_listen_setup_failed")
    if _contains_any(text, "transport factory failed"):
        return FailureInfo("transport_init", "transport_factory_failed")

    if not setup_ok:
        return FailureInfo("infra", "setup_failed")
    if not bundle_ok:
        return FailureInfo("infra", "artifact_incomplete")
    if "timeout" in text and not ready:
        if any(marker == "call_established" or marker.startswith("call_established") for marker in markers):
            return FailureInfo("transport_runtime", "transport_closed_early")
        return FailureInfo("call_setup", "call_setup_timeout")
    if ready and not smoke_ok:
        return FailureInfo("payload_flow", "payload_probe_failed")
    if not teardown_ok:
        return FailureInfo("teardown", "teardown_leak")
    if _contains_any(text, "negotiation", "carrier", "credential", "auth"):
        return FailureInfo("call_setup", "carrier_negotiation_failed")
    if text:
        return FailureInfo("infra", "unexpected_failure")
    return FailureInfo("", "")


class StructuredEventRecorder:
    """Collect JSONL events and support/debug snapshots for one run."""

    def __init__(self, *, component: str, run_id: str | None = None, base_fields: Mapping[str, Any] | None = None) -> None:
        self.component = component
        self.run_id = run_id or new_run_id()
        self.base_fields = {str(k): v for k, v in (base_fields or {}).items()}
        self._events: list[dict[str, Any]] = []
        self._last_success_stage = ""
        self._failed_stage = ""

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    @property
    def last_success_stage(self) -> str:
        return self._last_success_stage

    @property
    def failed_stage(self) -> str:
        return self._failed_stage

    def event(self, event: str, **fields: Any) -> dict[str, Any]:
        payload = {
            "event": event,
            "ts": time.time(),
            "run_id": self.run_id,
            "component": self.component,
        }
        payload.update(self.base_fields)
        payload.update({str(key): redact_value(value) for key, value in fields.items() if value is not None})
        stage = str(payload.get("stage", ""))
        outcome = str(payload.get("outcome", ""))
        if outcome == "success" and stage:
            self._last_success_stage = stage
        if outcome == "failure" and stage and not self._failed_stage:
            self._failed_stage = stage
            payload.setdefault("last_success_stage", self._last_success_stage)
        self._events.append(payload)
        return payload

    def write_jsonl(self, path: Path) -> None:
        path.write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in self._events),
            encoding="utf-8",
        )


def environment_snapshot() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
    }
