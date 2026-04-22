"""Deterministic probes for local backend/runtime readiness."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    kind: str
    target: str
    detail: str
    payload: dict[str, str] | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "ok": "yes" if self.ok else "no",
            "kind": self.kind,
            "target": self.target,
            "detail": self.detail,
        }
        if self.payload:
            payload.update(self.payload)
        return payload


def probe_endpoint(endpoint: str | None, *, timeout: float = 1.0) -> ProbeResult:
    if not endpoint:
        return ProbeResult(False, "none", "", "no endpoint")
    if endpoint.startswith("unix://"):
        return _probe_unix_socket(endpoint.removeprefix("unix://"), timeout=timeout)
    if ":" in endpoint:
        host, port_text = endpoint.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            return ProbeResult(False, "tcp", endpoint, "invalid tcp endpoint")
        return _probe_tcp_socket(host, port, timeout=timeout)
    return ProbeResult(False, "unknown", endpoint, "unsupported endpoint")


def _probe_unix_socket(path: str, *, timeout: float) -> ProbeResult:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(path)
            client.sendall(b"status")
            payload = client.recv(4096)
        except OSError as exc:
            return ProbeResult(False, "unix", path, str(exc))
    try:
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            decoded = {}
        detail = str(decoded.get("state", "unknown"))
        normalized = {str(key): str(value) for key, value in decoded.items()}
    except Exception:
        detail = payload.decode("utf-8", errors="replace").strip() or "connected"
        normalized = None
    return ProbeResult(True, "unix", path, detail, normalized)


def _probe_tcp_socket(host: str, port: int, *, timeout: float) -> ProbeResult:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return ProbeResult(False, "tcp", f"{host}:{port}", str(exc))
    return ProbeResult(True, "tcp", f"{host}:{port}", "connected")
