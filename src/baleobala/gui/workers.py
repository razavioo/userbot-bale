"""Qt worker threads — keep blocking Bale/proxy calls off the UI thread."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

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

        self.connecting.emit("Resolving Bale call session…")

        controller = BaleCarrierController(client=BaleApiClient(jwt=self._jwt))
        try:
            log.info("resolving call session")
            peer_id = self._peer_id
            if peer_id is None and self._peer_name:
                log.info("searching contact by name=%r", self._peer_name)
                matches = controller.search_contacts(self._peer_name)
                if not matches:
                    raise RuntimeError(
                        f"no contacts match name {self._peer_name!r}"
                    )
                peer_id = matches[0].user_id
                self.log_line.emit(
                    f"matched {self._peer_name!r} -> user_id {peer_id}"
                )
            if peer_id is not None:
                log.info("dialing peer_id=%d", peer_id)
                creds = controller.dial(
                    peer_id=peer_id,
                    creds_timeout=self._dial_timeout,
                    cancel_event=self._stop_event,
                )
            elif self._answer:
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
        finally:
            try:
                controller._client.stop()
            except Exception:
                pass

        if self._stop_event.is_set():
            return

        self.connecting.emit("Opening LiveKit audio tunnel…")
        log.info("opening livekit session room=%s identity=%s", creds.room, creds.identity)
        carrier_ctl = BaleCarrierController()
        carrier = carrier_ctl.open_session(creds)
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
            log.info("starting socks5 server")
            server.serve_forever()
        else:
            relay = TunnelTcpRelay(transport, secret=secret)
            self._server = relay
            self.connected.emit("Relay ready — awaiting client packets")
            log.info("starting tcp relay")
            relay.serve_forever()


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
