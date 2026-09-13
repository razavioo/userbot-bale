"""Local tunnel-service scaffolding for the macOS packet-tunnel backend."""

from __future__ import annotations

import json
import hashlib
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import Protocol, runtime_checkable

from userbot_bale.control.paths import config_dir, shared_container_dir
from userbot_bale.control.store import JsonStore


def _socket_path(candidate: Path, *, prefix: str) -> Path:
    """Keep AF_UNIX socket paths short enough for macOS and Linux."""
    raw = str(candidate)
    if len(raw.encode("utf-8")) <= 100:
        return candidate
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    short_dir = Path("/tmp") / "userbot-bale"
    return short_dir / f"{prefix}-{digest}.sock"


def _bind_unix_socket(server: socket.socket, socket_path: Path) -> None:
    """Bind a UNIX socket, removing stale files first if needed."""
    for attempt in range(2):
        try:
            if socket_path.exists() or socket_path.is_socket():
                socket_path.unlink()
        except OSError:
            pass
        try:
            server.bind(str(socket_path))
            return
        except FileExistsError:
            if attempt == 0:
                continue
            raise


@runtime_checkable
class TunnelBridge(Protocol):
    def start(self) -> None:
        ...

    def send(self, data: bytes) -> int:
        ...

    def recv(self, timeout: float | None = None) -> bytes | None:
        ...

    def close(self) -> None:
        ...

    @property
    def closed(self) -> bool:
        ...


@dataclass(frozen=True)
class TunnelServiceState:
    state: str
    endpoint: str | None = None
    profile_id: str | None = None
    backend: str | None = None
    pairing_id: str | None = None
    version: str = "1"
    transport_selected: str = ""
    call_established: str = "no"
    data_flow_ok: str = "no"
    recovery_state: str = ""
    transport_previous: str = ""
    failover_count: str = "0"
    recovering_since: str = ""
    carrier_session_id: str = ""
    peer_coordination: str = ""
    route_ready: str = "no"
    dns_ready: str = "no"
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "endpoint": self.endpoint,
            "profile_id": self.profile_id,
            "backend": self.backend,
            "pairing_id": self.pairing_id,
            "version": self.version,
            "transport_selected": self.transport_selected,
            "call_established": self.call_established,
            "data_flow_ok": self.data_flow_ok,
            "recovery_state": self.recovery_state,
            "transport_previous": self.transport_previous,
            "failover_count": self.failover_count,
            "recovering_since": self.recovering_since,
            "carrier_session_id": self.carrier_session_id,
            "peer_coordination": self.peer_coordination,
            "route_ready": self.route_ready,
            "dns_ready": self.dns_ready,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TunnelServiceState":
        return cls(
            state=str(data.get("state", "stopped")),
            endpoint=data.get("endpoint"),
            profile_id=data.get("profile_id"),
            backend=data.get("backend"),
            pairing_id=data.get("pairing_id"),
            version=str(data.get("version", "1")),
            transport_selected=str(data.get("transport_selected", "")),
            call_established=str(data.get("call_established", "no")),
            data_flow_ok=str(data.get("data_flow_ok", "no")),
            recovery_state=str(data.get("recovery_state", "")),
            transport_previous=str(data.get("transport_previous", "")),
            failover_count=str(data.get("failover_count", "0")),
            recovering_since=str(data.get("recovering_since", "")),
            carrier_session_id=str(data.get("carrier_session_id", "")),
            peer_coordination=str(data.get("peer_coordination", "")),
            route_ready=str(data.get("route_ready", "no")),
            dns_ready=str(data.get("dns_ready", "no")),
            last_error=str(data.get("last_error", "")),
            updated_at=float(data.get("updated_at", time.time())),
        )


class TunnelService:
    def start(self, *, profile_id: str, backend: str, pairing_id: str | None = None) -> TunnelServiceState:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def status(self) -> TunnelServiceState:
        raise NotImplementedError


class LocalTunnelService(TunnelService):
    """Narrow local IPC endpoint for a future packet-tunnel extension."""

    def __init__(self, *, state_path: Path | None = None, socket_path: Path | None = None) -> None:
        self._state_store = JsonStore(state_path or (config_dir() / "tunnel_service.json"))
        self._socket_path = _socket_path(socket_path or (shared_container_dir() / "tunnel_service.sock"), prefix="tunnel")
        self._active = False
        self._state = TunnelServiceState(state="stopped")
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, *, profile_id: str, backend: str, pairing_id: str | None = None) -> TunnelServiceState:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._server is not None:
            self.stop()
        self._stop.clear()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _bind_unix_socket(server, self._socket_path)
        server.listen(1)
        server.settimeout(0.25)
        self._server = server
        self._state = TunnelServiceState(
            state="running",
            endpoint=f"unix://{self._socket_path}",
            profile_id=profile_id,
            backend=backend,
            pairing_id=pairing_id,
            updated_at=time.time(),
        )
        self._state_store.save(self._state.to_dict())
        self._active = True
        try:
            os.chmod(self._socket_path, 0o600)
        except OSError:
            pass
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self._state

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        with client:
            try:
                data = client.recv(4096)
            except OSError:
                return
            if not data:
                return
            message = data.decode("utf-8", errors="replace").strip().lower()
            if message == "ping":
                reply = b"pong\n"
            elif message == "status":
                reply = (json.dumps(self.status().to_dict(), sort_keys=True) + "\n").encode("utf-8")
            elif message == "stop":
                reply = b"ok\n"
                try:
                    client.sendall(reply)
                finally:
                    self.stop()
                return
            else:
                reply = data
            try:
                client.sendall(reply)
            except OSError:
                return

    def stop(self) -> None:
        self._active = False
        self._state = TunnelServiceState(state="stopped")
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> TunnelServiceState:
        payload = self._state_store.load(default=None)
        if isinstance(payload, dict):
            self._state = TunnelServiceState.from_dict(payload)
            return self._state
        return self._state

    @property
    def endpoint(self) -> str | None:
        return self.status().endpoint


class CarrierTunnelService(TunnelService):
    """Byte-stream service that bridges a local socket to the tunnel runtime."""

    def __init__(
        self,
        bridge: TunnelBridge,
        *,
        state_path: Path | None = None,
        socket_path: Path | None = None,
        manage_bridge: bool = False,
    ) -> None:
        self._bridge = bridge
        self._manage_bridge = manage_bridge
        self._state_store = JsonStore(state_path or (config_dir() / "carrier_tunnel_service.json"))
        self._socket_path = _socket_path(socket_path or (shared_container_dir() / "carrier_tunnel.sock"), prefix="carrier")
        self._state = TunnelServiceState(state="stopped")
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = False

    def start(self, *, profile_id: str, backend: str, pairing_id: str | None = None) -> TunnelServiceState:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._server is not None:
            self.stop()
        self._stop.clear()
        if self._manage_bridge:
            self._bridge.start()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _bind_unix_socket(server, self._socket_path)
        server.listen(1)
        server.settimeout(0.25)
        self._server = server
        self._state = TunnelServiceState(
            state="running",
            endpoint=f"unix://{self._socket_path}",
            profile_id=profile_id,
            backend=backend,
            pairing_id=pairing_id,
            updated_at=time.time(),
        )
        self._state_store.save(self._state.to_dict())
        self._active = True
        try:
            os.chmod(self._socket_path, 0o600)
        except OSError:
            pass
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self._state

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        with client:
            stop = threading.Event()

            def pump_bridge_to_client() -> None:
                try:
                    while not stop.is_set():
                        payload = self._bridge.recv(timeout=0.25)
                        if payload is None:
                            if self._bridge.closed or self._stop.is_set():
                                break
                            continue
                        client.sendall(_encode_frame(payload))
                finally:
                    stop.set()

            worker = threading.Thread(target=pump_bridge_to_client, daemon=True)
            worker.start()
            try:
                while not stop.is_set():
                    try:
                        header = _recv_exact(client, 4)
                        length = int.from_bytes(header, "big")
                        if length < 0:
                            break
                        payload = _recv_exact(client, length) if length else b""
                    except EOFError:
                        break
                    if self._handle_control_frame(client, payload):
                        continue
                    self._bridge.send(payload)
            finally:
                stop.set()
                worker.join(timeout=1.0)

    def _handle_control_frame(self, client: socket.socket, payload: bytes) -> bool:
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except Exception:
            return False
        if not isinstance(decoded, dict):
            return False
        message_type = str(decoded.get("type", "")).strip().lower()
        if not message_type:
            return False

        if message_type == "ping":
            self._send_control_reply(
                client,
                {"type": "pong", "version": str(decoded.get("version", self._state.version))},
            )
            return True

        if message_type == "status":
            current = self.status()
            updated = TunnelServiceState(
                state=str(decoded.get("state", current.state or "running")),
                endpoint=current.endpoint,
                profile_id=current.profile_id,
                backend=current.backend,
                pairing_id=current.pairing_id,
                version=str(decoded.get("version", current.version)),
                transport_selected=str(decoded.get("transport_selected", current.transport_selected)),
                call_established=str(decoded.get("call_established", current.call_established)),
                data_flow_ok=str(decoded.get("data_flow_ok", current.data_flow_ok)),
                recovery_state=str(decoded.get("recovery_state", current.recovery_state)),
                transport_previous=str(decoded.get("transport_previous", current.transport_previous)),
                failover_count=str(decoded.get("failover_count", current.failover_count)),
                recovering_since=str(decoded.get("recovering_since", current.recovering_since)),
                carrier_session_id=str(decoded.get("carrier_session_id", current.carrier_session_id)),
                peer_coordination=str(decoded.get("peer_coordination", current.peer_coordination)),
                route_ready=str(decoded.get("route_ready", current.route_ready)),
                dns_ready=str(decoded.get("dns_ready", current.dns_ready)),
                last_error=str(decoded.get("last_error", current.last_error)),
                updated_at=time.time(),
            )
            self._state = updated
            self._state_store.save(updated.to_dict())
            self._send_control_reply(client, updated.to_dict())
            return True

        if message_type == "stop":
            self._send_control_reply(
                client,
                {"type": "ok", "version": str(decoded.get("version", self._state.version))},
            )
            self.stop()
            return True
        return False

    def _send_control_reply(self, client: socket.socket, payload: dict[str, Any]) -> None:
        try:
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
            client.sendall(_encode_frame(data))
        except OSError:
            return

    def stop(self) -> None:
        self._active = False
        self._state = TunnelServiceState(state="stopped")
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._manage_bridge:
            try:
                self._bridge.close()
            except OSError:
                pass
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        try:
            self._state_store.path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> TunnelServiceState:
        payload = self._state_store.load(default=None)
        if isinstance(payload, dict):
            self._state = TunnelServiceState.from_dict(payload)
            return self._state
        return self._state


def _encode_frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise EOFError("socket closed")
        buf.extend(chunk)
    return bytes(buf)
