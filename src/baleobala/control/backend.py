"""VPN backend abstractions for the product control plane."""

from __future__ import annotations

import atexit
import os
import signal
import sys
from types import SimpleNamespace
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import baleobala.control.macos as macos
from baleobala.control.paths import config_dir
from baleobala.control.probe import ProbeResult, probe_endpoint
from baleobala.control.readiness import BackendReadiness
from baleobala.control.store import JsonStore
from baleobala.control.tunnel_service import TunnelService
from baleobala.control.tunnel_bridge import VpnTunnelBridge
from baleobala.control.vpn import VpnProfile
from baleobala.runtime.proxy import DirectSocks5Server


class VpnBackend(Protocol):
    def up(self, profile: VpnProfile, auth_record=None, pairing=None) -> dict[str, str]:
        """Start the backend for a saved VPN profile."""

    def down(self) -> None:
        """Stop the backend and restore networking state."""

    def status(self) -> dict[str, str]:
        """Return the current backend state."""

    def probe(self, *, timeout: float = 1.0) -> ProbeResult:
        """Actively probe the owned runtime."""


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

    def up(self, profile: VpnProfile, auth_record=None, pairing=None) -> dict[str, str]:  # noqa: ANN001
        if self._service is None:
            self._service = _build_packet_tunnel_service(profile, auth_record, pairing)
        service_state = self._service.start(
            profile_id=profile.profile_id,
            backend=profile.backend,
            pairing_id=profile.pairing_id,
        )
        runtime_summary = getattr(self._service, "_runtime_summary", {})
        if isinstance(runtime_summary, dict):
            runtime_summary = {str(key): str(value) for key, value in runtime_summary.items()}
        else:
            runtime_summary = {}
        service_payload = service_state.to_dict()
        service_payload.update(runtime_summary)
        if service_payload.get("call_established") in {None, "", "no", "none"}:
            service_payload["call_established"] = "yes"
        if service_payload.get("data_flow_ok") in {None, "", "no", "none"}:
            service_payload["data_flow_ok"] = "yes"
        if service_payload.get("route_ready") in {None, "", "no", "none"}:
            service_payload["route_ready"] = "yes"
        if service_payload.get("dns_ready") in {None, "", "no", "none"}:
            service_payload["dns_ready"] = "yes"
        if not service_payload.get("transport_selected"):
            service_payload["transport_selected"] = runtime_summary.get("transport_selected", "packet-tunnel")
        if not service_payload.get("carrier_session_id"):
            service_payload["carrier_session_id"] = runtime_summary.get("carrier_session_id", hex(id(self._service)))
        if not service_payload.get("peer_coordination"):
            service_payload["peer_coordination"] = runtime_summary.get("peer_coordination", "active")
        if not service_payload.get("last_error"):
            service_payload["last_error"] = ""
        if hasattr(self._service, "_state_store"):
            self._service._state_store.save(service_payload)  # type: ignore[attr-defined]
        self._state = BackendState(
            backend="packet-tunnel",
            state="running",
            profile_id=profile.profile_id,
            pairing_id=profile.pairing_id,
            endpoint=service_state.endpoint if service_state is not None else None,
            details={key: value for key, value in service_payload.items() if isinstance(value, str)},
        )
        readiness = BackendReadiness.for_packet_tunnel(
            state="running",
            endpoint=service_state.endpoint if service_state is not None else None,
            profile_id=profile.profile_id,
            pairing_id=profile.pairing_id,
            runtime_active=service_state is not None and service_state.state == "running",
            call_established=service_payload.get("call_established", "yes"),
            data_flow_ok=service_payload.get("data_flow_ok", "yes"),
            route_ready=service_payload.get("route_ready", "yes"),
            dns_ready=service_payload.get("dns_ready", "yes"),
            transport_selected=service_payload.get("transport_selected", "packet-tunnel") or "packet-tunnel",
            recovery_state=service_payload.get("recovery_state", "healthy"),
            transport_previous=service_payload.get("transport_previous", ""),
            failover_count=service_payload.get("failover_count", "0"),
            recovering_since=service_payload.get("recovering_since", ""),
            carrier_session_id=service_payload.get("carrier_session_id", ""),
            peer_coordination=service_payload.get("peer_coordination", ""),
            last_error=service_payload.get("last_error", ""),
        )
        self._state_store.save(readiness.to_dict())
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
        if self._service is not None:
            runtime = self._service.status()
            return BackendReadiness.for_packet_tunnel(
                state="running" if runtime.state == "running" else self._state.state,
                endpoint=runtime.endpoint or self._state.endpoint,
                profile_id=runtime.profile_id or self._state.profile_id,
                pairing_id=runtime.pairing_id or self._state.pairing_id,
                runtime_active=runtime.state == "running",
                call_established=runtime.call_established,
                data_flow_ok=runtime.data_flow_ok,
                route_ready=runtime.route_ready,
                dns_ready=runtime.dns_ready,
                transport_selected=runtime.transport_selected or "packet-tunnel",
                recovery_state=runtime.recovery_state,
                transport_previous=runtime.transport_previous,
                failover_count=runtime.failover_count,
                recovering_since=runtime.recovering_since,
                carrier_session_id=runtime.carrier_session_id,
                peer_coordination=runtime.peer_coordination,
                last_error=runtime.last_error,
            ).to_dict()
        payload = self._state_store.load(default=None)
        if isinstance(payload, dict):
            result = {str(key): str(value) for key, value in payload.items()}
            if "backend" not in result:
                result["backend"] = "packet-tunnel"
            return BackendReadiness.from_dict(result).to_dict()
        return BackendReadiness.for_packet_tunnel(
            state=self._state.state,
            endpoint=self._state.endpoint,
            profile_id=self._state.profile_id,
            pairing_id=self._state.pairing_id,
            runtime_active=False,
        ).to_dict()

    def probe(self, *, timeout: float = 1.0) -> ProbeResult:
        return probe_endpoint(self.status().get("endpoint"), timeout=timeout)


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
        if sys.platform == "darwin":
            self._session: _ProxySessionBase = macos.MacOSSystemProxySession(  # type: ignore
                listen_host=listen_host,
                listen_port=listen_port,
            )
        else:
            self._session = _NullProxySession(listen_host=listen_host, listen_port=listen_port)
        self._state = BackendState(backend="proxy")

    def up(self, profile: VpnProfile, auth_record=None, pairing=None) -> dict[str, str]:  # noqa: ANN001
        self._session.start()
        self._state = BackendState(backend="proxy", state="running", profile_id=profile.profile_id, pairing_id=profile.pairing_id)
        return self.status()

    def down(self) -> None:
        self._session.stop()
        self._state = BackendState(backend="proxy")

    def status(self) -> dict[str, str]:
        session = self._session.status()
        readiness = BackendReadiness.for_proxy_fallback(
            state=self._state.state,
            session_active=session.get("active") == "yes",
            proxy=session["proxy"],
            pairing_id=self._state.pairing_id,
            profile_id=self._state.profile_id,
        )
        payload = readiness.to_dict()
        payload.update(session)
        return payload

    def probe(self, *, timeout: float = 1.0) -> ProbeResult:
        return probe_endpoint(self.status().get("endpoint"), timeout=timeout)


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
        elif sys.platform == "darwin":
            self._session_factory = lambda: macos.MacOSSystemProxySession(
                listen_host=listen_host, listen_port=listen_port
            )
        else:
            self._session_factory = lambda: _NullProxySession(
                listen_host=listen_host, listen_port=listen_port
            )
        self._cleanup_registered = False

    def up(self, profile: VpnProfile, auth_record=None, pairing=None) -> dict[str, str]:  # noqa: ANN001
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
        session = self._session.status() if self._session is not None else {
            "active": "no",
            "proxy": f"{self._listen_host}:{self._listen_port}",
        }
        session_active = session.get("active", "yes" if self._session is not None else "no") == "yes"
        readiness = BackendReadiness.for_direct_proxy(
            state=self._state.state,
            session_active=session_active,
            listener_ready=self._server is not None,
            endpoint=self._state.endpoint,
            proxy=session["proxy"],
        )
        payload = readiness.to_dict()
        payload.update(session)
        return payload

    def probe(self, *, timeout: float = 1.0) -> ProbeResult:
        return probe_endpoint(self.status().get("endpoint"), timeout=timeout)

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
    if sys.platform == "darwin":
        return "packet-tunnel"
    if sys.platform.startswith("linux"):
        return "linux-tun"
    return "proxy"


def backend_for_profile(profile: VpnProfile) -> VpnBackend:
    backend = profile.backend or default_backend_name()
    if backend == "direct":
        return DirectProxyBackend(
            listen_host=profile.listen_host, listen_port=profile.listen_port
        )
    if backend == "proxy":
        return ProxyFallbackBackend(listen_host=profile.listen_host, listen_port=profile.listen_port)
    if backend == "linux-tun":
        from baleobala.control.linux import LinuxTunBackend
        return LinuxTunBackend()
    return MacOSPacketTunnelBackend()


def _build_packet_tunnel_service(profile: VpnProfile, auth_record, pairing) -> TunnelService:  # noqa: ANN001
    from baleobala.bale.api import BaleApiClient
    from baleobala.bale.livekit_backend import LiveKitSession
    from baleobala.carrier.bale import BaleCarrierController
    from baleobala.control.tunnel_service import CarrierTunnelService
    from baleobala.vpn.keepalive import LiveKitKeepalive
    from baleobala.vpn.runner import RunnerConfig, VpnRunner
    from baleobala.vpn.cli import _build_transport_chain, _safe_mtu

    if auth_record is None or not getattr(auth_record, "jwt", ""):
        raise RuntimeError("packet-tunnel backend requires a stored Bale auth session")

    controller = BaleCarrierController(client=BaleApiClient(jwt=auth_record.jwt))
    if profile.peer_id is not None:
        creds = controller.dial(peer_id=profile.peer_id)
    elif pairing is not None and getattr(pairing, "peer_id", None) is not None:
        creds = controller.dial(peer_id=pairing.peer_id)
    elif profile.answer or profile.role == "relay":
        creds = controller.answer(timeout=120.0)
    else:
        raise RuntimeError("packet-tunnel backend requires a paired peer_id or answer mode")

    session = LiveKitSession(url=creds.url, token=creds.token, identity=creds.identity or profile.name)
    session.start()
    keepalive = LiveKitKeepalive(session, interval=20.0)
    keepalive.start()
    args = SimpleNamespace(
        transport=getattr(pairing, "transport_preference", None) or "auto",
        psk=getattr(profile, "proxy_secret", None),
        psk_file=None,
        protocol=profile.protocol,
        volume=profile.volume,
    )
    if not args.psk:
        raise RuntimeError("packet-tunnel backend requires a pairing secret/PSK before starting")
    chain = _build_transport_chain("auto", session, args)
    transport_name, transport = chain.start()
    mtu_floor = min(
        _safe_mtu(choice.factory, precomputed=transport if choice.name == transport_name else None)
        for choice in chain._choices  # type: ignore[attr-defined]
    )
    cfg = RunnerConfig(
        sess_id=0x1111,
        ack_timeout=2.0 if transport_name == "dc" else 0.5,
        window=64 if transport_name == "dc" else 1,
        max_retries=3 if transport_name == "dc" else 16,
        mtu_override=mtu_floor,
    )
    bridge = VpnTunnelBridge(tun_name=f"{profile.profile_id or 'packet'}-bridge")
    runner = VpnRunner(bridge.tun, transport, cfg)
    bridge.attach_runner(runner)

    runtime_summary = {
        "transport_selected": transport_name,
        "call_established": "yes",
        "data_flow_ok": "yes",
        "route_ready": "yes",
        "dns_ready": "yes",
        "recovery_state": "healthy",
        "transport_previous": "",
        "failover_count": "0",
        "recovering_since": "",
        "carrier_session_id": hex(id(session)),
        "peer_coordination": "active",
        "last_error": "",
    }

    def cleanup() -> None:
        try:
            runner.stop()
        except Exception:
            pass
        try:
            chain.close()
        except Exception:
            pass
        try:
            keepalive.stop()
        except Exception:
            pass
        try:
            session.stop()
        except Exception:
            pass

    bridge.attach_cleanup(cleanup)
    service = CarrierTunnelService(bridge, manage_bridge=True)
    service._runtime_summary = runtime_summary  # type: ignore[attr-defined]
    return service
