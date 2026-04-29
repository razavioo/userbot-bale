"""Shared backend readiness/status helpers for automation-friendly probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _yn(value: bool) -> str:
    return "yes" if value else "no"


@dataclass(frozen=True)
class BackendReadiness:
    backend: str
    state: str
    control_ready: str = "no"
    carrier_ready: str = "no"
    bypass_ready: str = "no"
    transport_ready: str = "no"
    data_path_ready: str = "no"
    route_ready: str = "no"
    dns_ready: str = "no"
    call_established: str = "no"
    data_flow_ok: str = "no"
    recovery_state: str = ""
    transport_previous: str = ""
    failover_count: str = "0"
    recovering_since: str = ""
    carrier_session_id: str = ""
    peer_coordination: str = ""
    teardown_clean: str = "no"
    failure_class: str = ""
    carrier_latency_ms: str = ""
    last_error: str = ""
    endpoint: str | None = None
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        payload = {
            "backend": self.backend,
            "state": self.state,
            "control_ready": self.control_ready,
            "carrier_ready": self.carrier_ready,
            "bypass_ready": self.bypass_ready,
            "transport_ready": self.transport_ready,
            "data_path_ready": self.data_path_ready,
            "route_ready": self.route_ready,
            "dns_ready": self.dns_ready,
            "call_established": self.call_established,
            "data_flow_ok": self.data_flow_ok,
            "recovery_state": self.recovery_state,
            "transport_previous": self.transport_previous,
            "failover_count": self.failover_count,
            "recovering_since": self.recovering_since,
            "carrier_session_id": self.carrier_session_id,
            "peer_coordination": self.peer_coordination,
            "teardown_clean": self.teardown_clean,
            "failure_class": self.failure_class,
            "carrier_latency_ms": self.carrier_latency_ms,
            "last_error": self.last_error,
        }
        if self.endpoint:
            payload["endpoint"] = self.endpoint
        payload.update(self.details)
        return payload

    def merged_with(self, extra: dict[str, Any]) -> "BackendReadiness":
        merged = self.to_dict()
        for key, value in extra.items():
            merged[str(key)] = str(value)
        return type(self).from_dict(merged)

    @classmethod
    def for_direct_proxy(
        cls,
        *,
        state: str,
        session_active: bool,
        listener_ready: bool,
        endpoint: str | None,
        proxy: str,
    ) -> "BackendReadiness":
        data_ready = session_active and listener_ready and state == "running"
        return cls(
            backend="direct",
            state=state,
            control_ready=_yn(state == "running"),
            call_established=_yn(session_active and listener_ready and state == "running"),
            data_path_ready=_yn(data_ready),
            data_flow_ok=_yn(data_ready),
            teardown_clean=_yn(state != "running" or session_active),
            endpoint=endpoint,
            details={
                "proxy": proxy,
                "mode": "system-proxy",
            },
        )

    @classmethod
    def for_windows_proxy(
        cls,
        *,
        state: str,
        session_active: bool,
        listener_ready: bool,
        endpoint: str | None,
        proxy: str,
    ) -> "BackendReadiness":
        data_ready = session_active and listener_ready and state == "running"
        return cls(
            backend="windows-proxy",
            state=state,
            control_ready=_yn(state == "running"),
            call_established=_yn(data_ready),
            data_path_ready=_yn(data_ready),
            data_flow_ok=_yn(data_ready),
            teardown_clean=_yn(state != "running" or session_active),
            endpoint=endpoint,
            details={
                "proxy": proxy,
                "mode": "winhttp-system-proxy",
            },
        )

    @classmethod
    def for_proxy_fallback(
        cls,
        *,
        state: str,
        session_active: bool,
        proxy: str,
        pairing_id: str | None,
        profile_id: str | None,
    ) -> "BackendReadiness":
        return cls(
            backend="proxy",
            state=state,
            control_ready=_yn(session_active and state == "running"),
            call_established=_yn(session_active and state == "running"),
            teardown_clean=_yn(state != "running" or session_active),
            endpoint=proxy,
            details={
                "proxy": proxy,
                "profile_id": profile_id or "",
                "pairing_id": pairing_id or "",
            },
        )

    @classmethod
    def for_linux_tun(
        cls,
        *,
        state: str,
        session_active: bool,
        tun: str,
        address: str,
        mtu: str,
        call_established: str = "no",
        data_flow_ok: str = "no",
        transport_selected: str = "",
        recovery_state: str = "",
        transport_previous: str = "",
        failover_count: str = "0",
        recovering_since: str = "",
        carrier_session_id: str = "",
        peer_coordination: str = "",
        endpoint: str | None = None,
        last_error: str = "",
    ) -> "BackendReadiness":
        ready = session_active and state == "running"
        return cls(
            backend="linux-tun",
            state=state,
            control_ready=_yn(ready),
            carrier_ready=_yn(call_established == "yes"),
            bypass_ready=_yn(ready),
            transport_ready=_yn(transport_selected != ""),
            route_ready=_yn(ready),
            dns_ready=_yn(ready),
            call_established=call_established,
            data_flow_ok=data_flow_ok,
            recovery_state=recovery_state,
            transport_previous=transport_previous,
            failover_count=failover_count,
            recovering_since=recovering_since,
            carrier_session_id=carrier_session_id,
            peer_coordination=peer_coordination,
            teardown_clean=_yn(state != "running" or session_active),
            carrier_latency_ms="0" if call_established == "yes" else "",
            last_error=last_error,
            endpoint=endpoint,
            details={
                "tun": tun,
                "address": address,
                "mtu": mtu,
                "transport_selected": transport_selected,
                "transport_previous": transport_previous,
                "failover_count": failover_count,
                "peer_coordination": peer_coordination,
            },
        )

    @classmethod
    def for_packet_tunnel(
        cls,
        *,
        state: str,
        endpoint: str | None,
        profile_id: str | None,
        pairing_id: str | None,
        runtime_active: bool,
        call_established: str = "no",
        data_flow_ok: str = "no",
        route_ready: str = "no",
        dns_ready: str = "no",
        transport_selected: str = "",
        recovery_state: str = "",
        transport_previous: str = "",
        failover_count: str = "0",
        recovering_since: str = "",
        carrier_session_id: str = "",
        peer_coordination: str = "",
        last_error: str = "",
    ) -> "BackendReadiness":
        return cls(
            backend="packet-tunnel",
            state=state,
            control_ready=_yn(state == "running"),
            transport_ready=_yn(runtime_active),
            route_ready=route_ready,
            dns_ready=dns_ready,
            call_established=call_established,
            data_flow_ok=data_flow_ok,
            recovery_state=recovery_state,
            transport_previous=transport_previous,
            failover_count=failover_count,
            recovering_since=recovering_since,
            carrier_session_id=carrier_session_id,
            peer_coordination=peer_coordination,
            teardown_clean=_yn(state != "running" or runtime_active),
            last_error=last_error,
            endpoint=endpoint,
            details={
                "profile_id": profile_id or "",
                "pairing_id": pairing_id or "",
                "mode": "native",
                "policy": "full-tunnel",
                "transport_selected": transport_selected,
                "transport_previous": transport_previous,
                "failover_count": failover_count,
                "peer_coordination": peer_coordination,
            },
        )

    @classmethod
    def for_android_vpn(
        cls,
        *,
        state: str,
        endpoint: str | None,
        profile_id: str | None,
        pairing_id: str | None,
        runtime_active: bool,
        call_established: str = "no",
        data_flow_ok: str = "no",
        route_ready: str = "no",
        dns_ready: str = "no",
        transport_selected: str = "",
        recovery_state: str = "",
        transport_previous: str = "",
        failover_count: str = "0",
        recovering_since: str = "",
        carrier_session_id: str = "",
        peer_coordination: str = "",
        last_error: str = "",
    ) -> "BackendReadiness":
        return cls(
            backend="android-vpn",
            state=state,
            control_ready=_yn(state == "running"),
            transport_ready=_yn(runtime_active),
            route_ready=route_ready,
            dns_ready=dns_ready,
            call_established=call_established,
            data_flow_ok=data_flow_ok,
            recovery_state=recovery_state,
            transport_previous=transport_previous,
            failover_count=failover_count,
            recovering_since=recovering_since,
            carrier_session_id=carrier_session_id,
            peer_coordination=peer_coordination,
            teardown_clean=_yn(state != "running" or runtime_active),
            last_error=last_error,
            endpoint=endpoint,
            details={
                "profile_id": profile_id or "",
                "pairing_id": pairing_id or "",
                "mode": "native",
                "policy": "vpn-service",
                "transport_selected": transport_selected,
                "transport_previous": transport_previous,
                "failover_count": failover_count,
                "peer_coordination": peer_coordination,
            },
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackendReadiness":
        details = {
            str(k): str(v)
            for k, v in data.items()
            if k
            not in {
                "backend",
                "state",
                "control_ready",
                "carrier_ready",
                "bypass_ready",
                "transport_ready",
                "data_path_ready",
                "route_ready",
                "dns_ready",
                "call_established",
                "data_flow_ok",
                "recovery_state",
                "transport_previous",
                "failover_count",
                "recovering_since",
                "carrier_session_id",
                "peer_coordination",
                "teardown_clean",
                "failure_class",
                "carrier_latency_ms",
                "last_error",
                "endpoint",
            }
        }
        return cls(
            backend=str(data.get("backend", "")),
            state=str(data.get("state", "stopped")),
            control_ready=str(data.get("control_ready", "no")),
            carrier_ready=str(data.get("carrier_ready", "no")),
            bypass_ready=str(data.get("bypass_ready", "no")),
            transport_ready=str(data.get("transport_ready", "no")),
            data_path_ready=str(data.get("data_path_ready", "no")),
            route_ready=str(data.get("route_ready", "no")),
            dns_ready=str(data.get("dns_ready", "no")),
            call_established=str(data.get("call_established", "no")),
            data_flow_ok=str(data.get("data_flow_ok", "no")),
            recovery_state=str(data.get("recovery_state", "")),
            transport_previous=str(data.get("transport_previous", "")),
            failover_count=str(data.get("failover_count", "0")),
            recovering_since=str(data.get("recovering_since", "")),
            carrier_session_id=str(data.get("carrier_session_id", "")),
            peer_coordination=str(data.get("peer_coordination", "")),
            teardown_clean=str(data.get("teardown_clean", "no")),
            failure_class=str(data.get("failure_class", "")),
            carrier_latency_ms=str(data.get("carrier_latency_ms", "")),
            last_error=str(data.get("last_error", "")),
            endpoint=str(data["endpoint"]) if data.get("endpoint") else None,
            details=details,
        )
