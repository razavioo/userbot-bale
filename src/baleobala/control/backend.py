"""VPN backend abstractions for the product control plane."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import baleobala.control.macos as macos
from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore
from baleobala.control.tunnel_service import TunnelService, TunnelServiceState
from baleobala.control.vpn import VpnProfile


class VpnBackend(Protocol):
    def up(self, profile: VpnProfile) -> dict[str, str]:
        """Start the backend for a saved VPN profile."""

    def down(self) -> None:
        """Stop the backend and restore networking state."""

    def status(self) -> dict[str, str]:
        """Return the current backend state."""


@dataclass
class BackendState:
    backend: str
    state: str = "stopped"
    profile_id: str | None = None
    pairing_id: str | None = None
    endpoint: str | None = None
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        payload = {
            "backend": self.backend,
            "state": self.state,
        }
        if self.profile_id is not None:
            payload["profile_id"] = self.profile_id
        if self.pairing_id is not None:
            payload["pairing_id"] = self.pairing_id
        if self.endpoint is not None:
            payload["endpoint"] = self.endpoint
        payload.update(self.details)
        return payload


class MacOSPacketTunnelBackend(VpnBackend):
    """Packet-tunnel-first macOS backend scaffold.

    The native extension is not part of this Python repo yet, so this
    backend owns the durable control-plane state and the local IPC service
    boundary that the future extension will speak to.
    """

    def __init__(
        self,
        *,
        service: TunnelService | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._service = service
        self._state_store = JsonStore(state_path or (config_dir() / "macos_packet_tunnel.json"))
        self._state = BackendState(backend="packet-tunnel")

    def up(self, profile: VpnProfile) -> dict[str, str]:
        service_state = None
        if self._service is not None:
            service_state = self._service.start(
                profile_id=profile.profile_id,
                backend=profile.backend,
                pairing_id=profile.pairing_id,
            )
        self._state = BackendState(
            backend="packet-tunnel",
            state="running",
            profile_id=profile.profile_id,
            pairing_id=profile.pairing_id,
            endpoint=service_state.endpoint if service_state is not None else None,
            details={
                "policy": "full-tunnel",
                "mode": "native",
            },
        )
        self._state_store.save(self._state.to_dict())
        return self.status()

    def down(self) -> None:
        if self._service is not None:
            self._service.stop()
        self._state = BackendState(backend="packet-tunnel")
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        payload = self._state_store.load(default=None)
        if isinstance(payload, dict):
            result = {str(key): str(value) for key, value in payload.items()}
            if "backend" not in result:
                result["backend"] = "packet-tunnel"
            return result
        return self._state.to_dict()


class ProxyFallbackBackend(VpnBackend):
    """Fallback backend that manipulates macOS system proxy settings."""

    def __init__(self, *, listen_host: str = "127.0.0.1", listen_port: int = 1080) -> None:
        if os.sys.platform == "darwin":
            self._session: _ProxySessionBase = macos.MacOSSystemProxySession(
                listen_host=listen_host,
                listen_port=listen_port,
            )
        else:
            self._session = _NullProxySession(listen_host=listen_host, listen_port=listen_port)
        self._state = BackendState(backend="proxy")

    def up(self, profile: VpnProfile) -> dict[str, str]:
        self._session.start()
        self._state = BackendState(
            backend="proxy",
            state="running",
            profile_id=profile.profile_id,
            pairing_id=profile.pairing_id,
            details={"proxy": self._session.status()["proxy"]},
        )
        return self.status()

    def down(self) -> None:
        self._session.stop()
        self._state = BackendState(backend="proxy")

    def status(self) -> dict[str, str]:
        return {
            **self._state.to_dict(),
            **self._session.status(),
        }


class _ProxySessionBase:
    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def status(self) -> dict[str, str]:
        raise NotImplementedError


class _NullProxySession(_ProxySessionBase):
    def __init__(self, *, listen_host: str, listen_port: int) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._active = False

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def status(self) -> dict[str, str]:
        return {
            "active": "yes" if self._active else "no",
            "services": "0",
            "proxy": f"{self._listen_host}:{self._listen_port}",
            "state": "running" if self._active else "stopped",
        }


def default_backend_name() -> str:
    if os.environ.get("BALEOBALA_VPN_BACKEND"):
        return os.environ["BALEOBALA_VPN_BACKEND"]
    if os.sys.platform == "darwin":
        return "packet-tunnel"
    return "proxy"


def backend_for_profile(profile: VpnProfile) -> VpnBackend:
    backend = profile.backend or default_backend_name()
    if backend == "proxy":
        return ProxyFallbackBackend(listen_host=profile.listen_host, listen_port=profile.listen_port)
    return MacOSPacketTunnelBackend()
