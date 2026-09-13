"""Mesh provisioning control messages."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass


MAGIC = b"BBMESH1:"


class ProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True)
class MeshProvisionMessage:
    version: int
    kind: str
    peer_id: int
    session_id: int
    pool_cidr: str
    prefix: str
    gateway_ip: str
    client_ip: str
    tun_mtu: int
    transport: str
    error: str = ""

    def encode(self) -> bytes:
        return MAGIC + json.dumps(
            {
                "version": self.version,
                "kind": self.kind,
                "peer_id": self.peer_id,
                "session_id": self.session_id,
                "pool_cidr": self.pool_cidr,
                "prefix": self.prefix,
                "gateway_ip": self.gateway_ip,
                "client_ip": self.client_ip,
                "tun_mtu": self.tun_mtu,
                "transport": self.transport,
                "error": self.error,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def decode(cls, payload: bytes) -> "MeshProvisionMessage":
        if not payload.startswith(MAGIC):
            raise ProvisioningError("not a provisioning control message")
        try:
            data = json.loads(payload[len(MAGIC):].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProvisioningError("invalid provisioning payload") from exc
        return cls(
            version=int(data.get("version", 1)),
            kind=str(data.get("kind", "")),
            peer_id=int(data.get("peer_id", 0)),
            session_id=int(data.get("session_id", 0)),
            pool_cidr=str(data.get("pool_cidr", "")),
            prefix=str(data.get("prefix", "")),
            gateway_ip=str(data.get("gateway_ip", "")),
            client_ip=str(data.get("client_ip", "")),
            tun_mtu=int(data.get("tun_mtu", 0)),
            transport=str(data.get("transport", "")),
            error=str(data.get("error", "")),
        )


def recv_mesh_message(transport, *, timeout: float | None = None):  # type: ignore[no-untyped-def]
    payload = transport.recv_bytes(timeout=timeout)
    if payload is None:
        return None
    if not payload.startswith(MAGIC):
        return None
    return MeshProvisionMessage.decode(payload)


def configure_tun_interface(name: str, cidr: str, mtu: int) -> None:
    """Best-effort runtime TUN configuration for Linux clients."""
    if os.name != "posix":
        return
    ip = shutil.which("ip")
    if ip is None:
        return
    cmds = [
        [ip, "addr", "replace", cidr, "dev", name],
        [ip, "link", "set", "dev", name, "mtu", str(mtu)],
        [ip, "link", "set", "dev", name, "up"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ProvisioningError(
                f"failed to configure {name} with {' '.join(cmd[2:])}: "
                f"{result.stderr.strip() or 'unknown error'}"
            )
