"""VPN backend abstractions for the product control plane."""

from __future__ import annotations

import atexit
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import baleobala.control.macos as macos
from baleobala.control.paths import config_dir
from baleobala.control.store import JsonStore
from baleobala.control.tunnel_service import TunnelService, TunnelServiceState
from baleobala.control.vpn import VpnProfile
from baleobala.runtime.proxy import DirectSocks5Server


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
    """Fallback backend that manipulates macOS system proxy settings.

    Note: the caller is responsible for running a SOCKS5 listener on
    ``listen_host:listen_port`` *before* calling :meth:`up`, otherwise the
    system proxy will point at nothing and break networking. The CLI wires
    this via ``cmd_bale_proxy_client`` starting after ``backend.up(profile)``
    — see the historical ordering comment; we leave it unchanged here for
    backwards compatibility, but callers should be aware.
    """

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


class DirectProxyBackend(VpnBackend):
    """Self-contained macOS VPN backend.

    Starts an in-process SOCKS5 / HTTP CONNECT listener that opens direct
    outbound TCP sockets, then points the macOS system proxy at it via
    ``networksetup``. Unlike :class:`ProxyFallbackBackend`, this requires no
    remote peer, no auth, no LiveKit — a single machine is enough for
    ``baleobala vpn up`` to produce a working system-wide proxy.

    Ordering is strict: the listener must be bound *before* we flip system
    proxy settings. If either step fails, the other is unwound so we never
    leave networking broken. An ``atexit`` hook plus SIGTERM/SIGINT handlers
    perform a best-effort restore on crash.
    """

    def __init__(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1080,
        state_path: Path | None = None,
        server_factory=None,
        session_factory=None,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._state_store = JsonStore(state_path or (config_dir() / "macos_direct_proxy.json"))
        self._state = BackendState(backend="direct")
        self._server: DirectSocks5Server | None = None
        self._session: _ProxySessionBase | None = None
        self._server_factory = server_factory or (
            lambda: DirectSocks5Server(listen_host=listen_host, listen_port=listen_port)
        )
        if session_factory is not None:
            self._session_factory = session_factory
        elif os.sys.platform == "darwin":
            self._session_factory = lambda: macos.MacOSSystemProxySession(
                listen_host=listen_host, listen_port=listen_port
            )
        else:
            self._session_factory = lambda: _NullProxySession(
                listen_host=listen_host, listen_port=listen_port
            )
        self._cleanup_registered = False

    def up(self, profile: VpnProfile) -> dict[str, str]:
        server = self._server_factory()
        server.start()
        if not server.wait_ready(timeout=2.0):
            server.stop()
            raise RuntimeError("direct proxy listener failed to bind")

        session = self._session_factory()
        try:
            session.start()
        except Exception:
            server.stop()
            raise

        self._server = server
        self._session = session
        self._register_cleanup()
        self._state = BackendState(
            backend="direct",
            state="running",
            profile_id=profile.profile_id,
            pairing_id=profile.pairing_id,
            endpoint=f"{server.bound_host}:{server.bound_port}",
            details={
                "proxy": session.status().get("proxy", f"{self._listen_host}:{self._listen_port}"),
                "mode": "system-proxy",
            },
        )
        self._state_store.save(self._state.to_dict())
        return self.status()

    def down(self) -> None:
        session, self._session = self._session, None
        server, self._server = self._server, None
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        self._state = BackendState(backend="direct")
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, str]:
        base = self._state.to_dict()
        if self._session is not None:
            base.update(self._session.status())
        return base

    def _register_cleanup(self) -> None:
        if self._cleanup_registered:
            return
        self._cleanup_registered = True
        atexit.register(self._safe_down)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                prev = signal.getsignal(sig)

                def _handler(signum, frame, prev=prev):
                    self._safe_down()
                    if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
                        try:
                            prev(signum, frame)
                        except Exception:
                            pass
                    raise KeyboardInterrupt() if signum == signal.SIGINT else SystemExit(128 + signum)

                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass

    def _safe_down(self) -> None:
        try:
            self.down()
        except Exception:
            pass


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
        return "direct"
    return "proxy"


def backend_for_profile(profile: VpnProfile) -> VpnBackend:
    backend = profile.backend or default_backend_name()
    if backend == "direct":
        return DirectProxyBackend(
            listen_host=profile.listen_host, listen_port=profile.listen_port
        )
    if backend == "proxy":
        return ProxyFallbackBackend(listen_host=profile.listen_host, listen_port=profile.listen_port)
    return MacOSPacketTunnelBackend()
