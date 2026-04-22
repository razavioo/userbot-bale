"""Qt worker threads — keep blocking Bale/proxy calls off the UI thread."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from baleobala.control.observability import StructuredEventRecorder, classify_failure

log = logging.getLogger(__name__)


class StartSmsWorker(QObject):
    """Step 1 of phone login: POST StartPhoneAuth → SMS → transaction_hash."""

    ok = Signal(str)           # transaction_hash
    failed = Signal(str)       # human-readable error

    def __init__(self, phone: str) -> None:
        super().__init__()
        self._phone = phone.lstrip("+").strip()

    def run(self) -> None:
        from baleobala.bale.grpc_web import GrpcWebError

        if not self._phone.isdigit():
            self.failed.emit("Phone must be digits (with or without +).")
            return
        # Prefer browser-based auth (real Chrome TLS fingerprint → server
        # sets JWT cookie). Fall back to direct httpx if Playwright is absent.
        try:
            from baleobala.bale.auth_browser import BaleAuthBrowser
            self._auth = BaleAuthBrowser()
            log.info("Using browser auth (Playwright)")
        except ImportError:
            from baleobala.bale.auth import BaleAuth
            self._auth = BaleAuth()
            log.info("Using direct gRPC-Web auth")
        try:
            tx = self._auth.start_phone_auth(int(self._phone))
        except GrpcWebError as e:
            self.failed.emit(f"Bale rejected: {e.message or e}")
            return
        except Exception as e:
            self.failed.emit(f"Network error: {e}")
            return
        self.ok.emit(tx)

    def auth_handle(self):
        return getattr(self, "_auth", None)


class ValidateCodeWorker(QObject):
    """Step 2: POST ValidateCode(code) → AuthSession(jwt)."""

    ok = Signal(str)           # jwt
    failed = Signal(str)

    def __init__(self, auth, code: str) -> None:
        super().__init__()
        self._auth = auth
        self._code = code.strip()

    def run(self) -> None:
        from baleobala.bale.grpc_web import GrpcWebError

        try:
            session = self._auth.validate_code(self._code)
        except GrpcWebError as e:
            self.failed.emit(e.message or str(e))
            return
        except Exception as e:
            self.failed.emit(f"Validation error: {e}")
            return
        self.ok.emit(session.jwt)


class ProxyWorker(QObject):
    """Runs `bale-proxy client` or `bale-proxy relay` on a background thread.

    The carrier bring-up (LiveKit dial/answer) is synchronous and long;
    we emit `connecting`, `connected`, `stopped`, `failed` so the UI can
    reflect the real state.
    """

    connecting = Signal(str)   # status text
    connected = Signal(str)    # "SOCKS5 on 127.0.0.1:1080" / "relay ready"
    stopped = Signal()
    failed = Signal(str)
    log_line = Signal(str)

    def __init__(
        self,
        *,
        role: str,                # "client" | "relay"
        jwt: str,
        peer_name: str = "",
        peer_id: Optional[int] = None,
        answer: bool = False,
        answer_timeout: float = 120.0,
        dial_timeout: float = 120.0,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1080,
        proxy_secret: Optional[str] = None,
        protocol: str = "fast",
        volume: int = 50,
        identity: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._role = role
        self._jwt = jwt
        self._peer_name = peer_name
        self._peer_id = peer_id
        self._answer = answer
        self._answer_timeout = answer_timeout
        self._dial_timeout = dial_timeout
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._proxy_secret = proxy_secret
        self._protocol = protocol
        self._volume = volume
        self._identity = identity
        self._stop_event = threading.Event()
        self._server = None
        self._bridge = None
        self._transport = None

    def stop(self) -> None:
        self._stop_event.set()
        srv = self._server
        if srv is not None:
            try:
                srv.stop()
            except Exception:
                pass

    def run(self) -> None:
        try:
            log.info(
                "proxy worker starting role=%s peer_id=%s peer_name=%r answer=%s listen=%s:%d",
                self._role,
                self._peer_id,
                self._peer_name,
                self._answer,
                self._listen_host,
                self._listen_port,
            )
            self._run_inner()
        except Exception as e:
            log.exception("proxy worker crashed")
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            try:
                if self._transport is not None:
                    self._transport.close()
            except Exception:
                pass
            try:
                if self._bridge is not None:
                    self._bridge.close()
            except Exception:
                pass
            self.stopped.emit()

    # --------- internal ----------

    def _run_inner(self) -> None:
        from baleobala.bale.api import BaleApiClient
        from baleobala.carrier.bale import BaleCarrierController
        from baleobala.runtime import (
            QueuedTunnelTransport,
            Socks5ProxyServer,
            TunnelTcpRelay,
        )
        from baleobala.runtime.bridge import AudioTunnelBridge
        from baleobala.runtime.frame import TunnelRole

        recorder = StructuredEventRecorder(component="gui-proxy-connect")
        self.connecting.emit("Resolving Bale call session…")
        recorder.event("call_session_resolution_started", stage="call_setup", outcome="begin")

        controller = BaleCarrierController(client=BaleApiClient(jwt=self._jwt))
        try:
            log.info("resolving call session")
            peer_id = self._peer_id
            if peer_id is None and self._peer_name:
                recorder.event("peer_lookup_started", stage="call_setup", outcome="begin", peer_id=self._peer_name)
                log.info("searching contact by name=%r", self._peer_name)
                matches = controller.search_contacts(self._peer_name)
                if not matches:
                    info = classify_failure(last_error=f"no contacts match name {self._peer_name!r}")
                    recorder.event(
                        "peer_lookup_failed",
                        stage="call_setup",
                        outcome="failure",
                        failure_class=info.failure_class,
                        failure_code=info.failure_code,
                        error_message=f"no contacts match name {self._peer_name!r}",
                    )
                    raise RuntimeError(
                        f"no contacts match name {self._peer_name!r}"
                    )
                peer_id = matches[0].user_id
                recorder.event("peer_lookup_completed", stage="call_setup", outcome="success", peer_id=peer_id)
                self.log_line.emit(
                    f"matched {self._peer_name!r} -> user_id {peer_id}"
                )
            if peer_id is not None:
                recorder.event("dial_started", stage="call_setup", outcome="begin", peer_id=peer_id)
                log.info("dialing peer_id=%d", peer_id)
                creds = controller.dial(
                    peer_id=peer_id,
                    creds_timeout=self._dial_timeout,
                    cancel_event=self._stop_event,
                )
            elif self._answer:
                recorder.event("answer_started", stage="call_setup", outcome="begin")
                self.log_line.emit("Waiting for incoming call…")
                log.info("waiting for incoming call timeout=%ss", self._answer_timeout)
                creds = controller.answer(
                    timeout=self._answer_timeout,
                    cancel_event=self._stop_event,
                )
            else:
                raise RuntimeError(
                    "Either peer_name/peer_id or 'answer' mode required."
                )
            recorder.event("credentials_received", stage="call_setup", outcome="success", session_id=creds.room)
        finally:
            try:
                controller._client.stop()
            except Exception:
                pass

        if self._stop_event.is_set():
            return

        self.connecting.emit("Opening LiveKit audio tunnel…")
        recorder.event("carrier_session_open_started", stage="carrier_session", outcome="begin", session_id=creds.room)
        log.info("opening livekit session room=%s identity=%s", creds.room, creds.identity)
        carrier_ctl = BaleCarrierController()
        carrier = carrier_ctl.open_session(creds)
        recorder.event("carrier_session_opened", stage="carrier_session", outcome="success", session_id=creds.room)
        role = TunnelRole.CLIENT if self._role == "client" else TunnelRole.SERVER
        bridge = AudioTunnelBridge(
            carrier=carrier,
            role=role,
            protocol=self._protocol,
            volume=self._volume,
        )
        bridge.start()
        self._bridge = bridge

        transport = QueuedTunnelTransport(bridge)
        self._transport = transport

        secret = self._proxy_secret.encode("utf-8") if self._proxy_secret else None
        if secret is not None:
            log.info("proxy secret configured")
        else:
            log.info("proxy secret not configured")

        if self._role == "client":
            server = Socks5ProxyServer(
                transport,
                listen_host=self._listen_host,
                listen_port=self._listen_port,
                secret=secret,
            )
            self._server = server
            self.connected.emit(
                f"SOCKS5 listening on {self._listen_host}:{self._listen_port}"
            )
            recorder.event("transport_selected", stage="transport_init", outcome="success", transport_selected="socks5-proxy")
            log.info("starting socks5 server")
            server.serve_forever()
        else:
            relay = TunnelTcpRelay(transport, secret=secret)
            self._server = relay
            self.connected.emit("Relay ready — awaiting client packets")
            recorder.event("transport_selected", stage="transport_init", outcome="success", transport_selected="tcp-relay")
            log.info("starting tcp relay")
            relay.serve_forever()


class ControlPlaneConnectWorker(QObject):
    """Run connect/disconnect through ControlService instead of legacy proxy UX."""

    connecting = Signal(str)
    connected = Signal(dict)
    stopped = Signal(dict)
    failed = Signal(str)
    log_line = Signal(str)

    def __init__(self, service, *, profile_id: Optional[str] = None, disconnect: bool = False) -> None:  # noqa: ANN001
        super().__init__()
        self._service = service
        self._profile_id = profile_id
        self._disconnect = disconnect

    def run(self) -> None:
        recorder = StructuredEventRecorder(component="gui-control-plane")
        try:
            if self._disconnect:
                self.connecting.emit("Stopping connection…")
                recorder.event("disconnect_started", stage="teardown", outcome="begin")
                result = self._service.stop_connection(self._profile_id)
                self.log_line.emit("connection stopped")
                recorder.event("disconnect_completed", stage="teardown", outcome="success", backend=result.backend)
                self.stopped.emit({"backend": result.backend, "probe": result.probe})
                return

            self.connecting.emit("Reconciling saved profile…")
            recorder.event("reconcile_started", stage="call_setup", outcome="begin")
            snapshot = self._service.reconcile_runtime()
            if snapshot.pairing is None:
                info = classify_failure(last_error="No paired relay is ready")
                recorder.event(
                    "reconcile_failed",
                    stage="call_setup",
                    outcome="failure",
                    failure_class=info.failure_class,
                    failure_code=info.failure_code,
                    error_message="No paired relay is ready. Create or accept a pairing first.",
                )
                raise RuntimeError("No paired relay is ready. Create or accept a pairing first.")
            self.log_line.emit(f"pairing={snapshot.pairing.name} backend={snapshot.pairing.backend_preference}")
            self.connecting.emit("Starting backend…")
            recorder.event("backend_starting", stage="transport_init", outcome="begin", backend=snapshot.pairing.backend_preference)
            result = self._service.start_connection(self._profile_id)
            time.sleep(0.05)
            recorder.event("backend_started", stage="transport_init", outcome="success", backend=result.backend)
            self.connected.emit(
                {
                    "backend": result.backend,
                    "probe": result.probe,
                    "pairing": {
                        "name": result.pairing.name if result.pairing else "",
                        "profile_id": result.pairing.profile_id if result.pairing else "",
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            info = classify_failure(last_error=str(exc))
            recorder.event(
                "control_plane_failed",
                stage=recorder.failed_stage or "call_setup",
                outcome="failure",
                failure_class=info.failure_class,
                failure_code=info.failure_code,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            log.exception("control-plane worker crashed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# Keep a global registry so workers + threads are never GC'd while
# running. When a thread finishes, it's removed from the set.
_LIVE_THREADS: set = set()


def run_in_thread(worker: QObject) -> QThread:
    """Move `worker` to a new QThread, start it, and wire cleanup.

    Critical: the QThread's default run() starts an event loop that keeps
    running after `worker.run()` returns. We must call quit() + wait()
    before destruction or Qt aborts the process. The wrapper below
    quits the thread as soon as worker.run() returns (success or error),
    and holds strong refs in a module-level set until finished.
    """
    thread = QThread()
    worker.moveToThread(thread)

    def _invoke():
        try:
            worker.run()
        finally:
            thread.quit()

    thread.started.connect(_invoke)

    def _cleanup():
        _LIVE_THREADS.discard((worker, thread))
        worker.deleteLater()
        thread.deleteLater()

    thread.finished.connect(_cleanup)

    _LIVE_THREADS.add((worker, thread))
    thread.start()
    return thread


class QtLogHandler(logging.Handler, QObject):
    """A logging.Handler that emits a Qt signal per record."""

    line = Signal(str)

    def __init__(self, level: int = logging.INFO) -> None:
        logging.Handler.__init__(self, level)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            self.line.emit(self.format(record))
        except Exception:
            self.handleError(record)
