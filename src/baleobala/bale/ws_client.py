"""
WebSocket transport for Bale's `next-ws.bale.ai/ws/` endpoint.

Observed details (from captures/bale-web-session*.mitm on 2026-04-19):

- Endpoint:  wss://next-ws.bale.ai/ws/
- Auth:      `access_token` cookie (JWT issued by /bale.auth.v1.Auth/ValidateCode)
- Protocol:  binary WebSocket; each frame is a protobuf envelope
             (see baleobala.bale.rpc_envelope)

`WsClient` exposes a synchronous-looking `rpc(service, method, payload)`
method that awaits the matching response frame (matched by `seq`) and
returns its payload bytes. Internally it runs the WS on an asyncio
thread, mirroring the pattern used by bale.livekit_backend.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import itertools
import logging
import os
import queue
import ssl
import threading
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

try:
    import websockets  # type: ignore
except ImportError:  # pragma: no cover - optional dep
    websockets = None  # type: ignore

from baleobala.bale.rpc_envelope import Request, Response

log = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://next-ws.bale.ai/ws/"


class WsTlsPolicyError(RuntimeError):
    def __init__(self, message: str, *, failure_class: str) -> None:
        super().__init__(message)
        self.failure_class = failure_class


@dataclasses.dataclass(frozen=True)
class WsTlsConfig:
    verify_mode: str = "strict"
    ca_file: str | None = None
    ca_path: str | None = None
    allow_insecure_debug: bool = False

    @property
    def insecure(self) -> bool:
        return self.verify_mode == "insecure"

    @classmethod
    def from_sources(
        cls,
        *,
        ca_file: str | None = None,
        ca_path: str | None = None,
        insecure: bool = False,
        allow_insecure_debug: bool = False,
    ) -> "WsTlsConfig":
        env_ca_file = os.environ.get("BALE_SSL_CA_FILE") or os.environ.get("SSL_CERT_FILE")
        env_ca_path = os.environ.get("BALE_SSL_CA_PATH") or os.environ.get("SSL_CERT_DIR")
        env_insecure = _env_truthy("BALE_WS_SSL_NO_VERIFY") or _env_truthy("BALE_SSL_NO_VERIFY")
        resolved_ca_file = ca_file or env_ca_file
        resolved_ca_path = ca_path or env_ca_path
        resolved_insecure = insecure or env_insecure
        if resolved_insecure and not allow_insecure_debug:
            raise WsTlsPolicyError(
                "insecure Bale WS TLS bypass is only allowed for debug flows",
                failure_class="tls_insecure_rejected",
            )
        verify_mode = "insecure" if resolved_insecure else "strict"
        return cls(
            verify_mode=verify_mode,
            ca_file=resolved_ca_file,
            ca_path=resolved_ca_path,
            allow_insecure_debug=allow_insecure_debug,
        )


def _require_websockets() -> None:
    if websockets is None:
        raise ImportError("websockets package is not installed.")


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _tls_event(
    event: str,
    *,
    url: str,
    failure_class: str,
    ca_file: str | None,
    ca_path: str | None,
    insecure: bool,
    error: BaseException | None = None,
) -> dict[str, object]:
    parsed = urlparse(url)
    return {
        "event": event,
        "failure_class": failure_class,
        "host": parsed.hostname or "",
        "url": f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path or ''}",
        "custom_ca_file": bool(ca_file),
        "custom_ca_path": bool(ca_path),
        "insecure_debug": insecure,
        "error_type": type(error).__name__ if error is not None else "",
    }


def _build_ssl_context(config: WsTlsConfig) -> ssl.SSLContext | None:
    if not config.ca_file and not config.ca_path and not config.insecure:
        return None
    ctx = ssl.create_default_context()
    try:
        if config.ca_file or config.ca_path:
            ctx.load_verify_locations(
                cafile=config.ca_file or None,
                capath=config.ca_path or None,
            )
    except (OSError, ssl.SSLError) as e:
        raise WsTlsPolicyError(
            f"failed to load Bale WS CA override: {e}",
            failure_class="tls_ca_load_failed",
        ) from e
    if config.insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class WsClient:
    def __init__(
        self,
        jwt: str,
        url: str = DEFAULT_WS_URL,
        on_update: Optional[Callable[[Response], None]] = None,
        tls_config: WsTlsConfig | None = None,
    ) -> None:
        _require_websockets()
        self._jwt = jwt
        self._url = url
        self._on_update = on_update
        self._tls_config = tls_config or WsTlsConfig.from_sources()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws = None
        self._connected = threading.Event()
        self._stopped = threading.Event()
        self._connect_error: BaseException | None = None
        self._seq_counter = itertools.count(1)
        self._pending: Dict[int, "queue.Queue[Response]"] = {}
        self._lock = threading.Lock()

    def start(self, timeout: float = 15.0) -> None:
        self._thread = threading.Thread(
            target=self._run_thread, name="baleobala-bale-ws", daemon=True,
        )
        self._thread.start()
        if not self._connected.wait(timeout):
            if self._connect_error is not None:
                raise RuntimeError(
                    f"WS failed to connect: {self._connect_error}"
                ) from self._connect_error
            raise RuntimeError(f"WS did not connect within {timeout}s")

    def stop(self, timeout: float = 5.0) -> None:
        if self._loop and not self._stopped.is_set():
            self._stopped.set()
            try:
                asyncio.run_coroutine_threadsafe(self._close(), self._loop).result(timeout)
            except Exception:  # noqa: BLE001
                pass
        if self._thread:
            self._thread.join(timeout=timeout)

    def send_oneway(
        self, service: str, method: str, payload: bytes = b"",
    ) -> None:
        """Send an RPC without waiting for its response.

        Useful for subscription-style RPCs (e.g. GetDiff) where we only
        care about the push updates that follow, not the RPC ack.
        """
        if self._loop is None or self._ws is None:
            raise RuntimeError("WsClient not started")
        seq = next(self._seq_counter)
        req = Request(service=service, method=method, payload=payload, seq=seq)
        asyncio.run_coroutine_threadsafe(self._ws.send(req.encode()), self._loop)

    def rpc(
        self, service: str, method: str, payload: bytes = b"",
        timeout: float = 10.0,
    ) -> Response:
        if self._loop is None or self._ws is None:
            raise RuntimeError("WsClient not started")
        seq = next(self._seq_counter)
        req = Request(service=service, method=method, payload=payload, seq=seq)
        q: "queue.Queue[Response]" = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[seq] = q
        frame = req.encode()
        asyncio.run_coroutine_threadsafe(self._ws.send(frame), self._loop)
        try:
            return q.get(timeout=timeout)
        finally:
            with self._lock:
                self._pending.pop(seq, None)

    def _run_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    async def _run(self) -> None:
        # Bale's WS gateway requires Origin to match the web client's
        # origin; without it the server returns HTTP 403 on upgrade.
        # User-Agent is not strictly required today but a realistic one
        # avoids caching surprises.
        headers = [
            ("Origin", "https://web.bale.ai"),
            ("User-Agent",
             "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
            ("Cookie", f"access_token={self._jwt}"),
        ]
        connect_kwargs = {"max_size": None}
        ssl_context = _build_ssl_context(self._tls_config)
        if ssl_context is not None:
            connect_kwargs["ssl"] = ssl_context
        if self._tls_config.insecure:
            log.warning(
                "Bale WS TLS verification disabled for debug-only flow: %s",
                _tls_event(
                    "tls_insecure_debug",
                    url=self._url,
                    failure_class="tls_insecure_debug",
                    ca_file=self._tls_config.ca_file,
                    ca_path=self._tls_config.ca_path,
                    insecure=True,
                ),
            )
        params = inspect.signature(websockets.connect).parameters
        if "additional_headers" in params:
            connect_kwargs["additional_headers"] = headers
        else:
            connect_kwargs["extra_headers"] = headers
        conn = websockets.connect(self._url, **connect_kwargs)
        try:
            async with conn as ws:
                self._ws = ws
                init = bytes.fromhex("1a04080110"
                                     "01")  # tag 3, len 4, {1:1, 2:1}
                log.info("sending init frame: %s", init.hex())
                await ws.send(init)
                log.info("init frame sent; waiting for init ack...")
                # Wait for server's init-ack before declaring ready.
                try:
                    first = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    log.info("init ack: %s", first.hex() if isinstance(first, (bytes, bytearray)) else first)
                except asyncio.TimeoutError:
                    log.warning("no init-ack within 5s; continuing anyway")
                self._connected.set()
                async for msg in ws:
                    log.debug("WS rx: %dB", len(msg) if isinstance(msg, (bytes, bytearray)) else -1)
                    if isinstance(msg, (bytes, bytearray)):
                        self._dispatch(bytes(msg))
                    else:
                        log.debug("text WS frame ignored: %r", msg[:64])
        except ssl.SSLCertVerificationError as e:
            self._connect_error = e
            log.warning(
                "Bale WS TLS verification failed: %s",
                _tls_event(
                    "ws_tls_failure",
                    url=self._url,
                    failure_class="tls_verify_failed",
                    ca_file=self._tls_config.ca_file,
                    ca_path=self._tls_config.ca_path,
                    insecure=self._tls_config.insecure,
                    error=e,
                ),
            )
        except ssl.SSLError as e:
            self._connect_error = e
            log.warning(
                "Bale WS TLS handshake failed: %s",
                _tls_event(
                    "ws_tls_failure",
                    url=self._url,
                    failure_class="tls_handshake_failed",
                    ca_file=self._tls_config.ca_file,
                    ca_path=self._tls_config.ca_path,
                    insecure=self._tls_config.insecure,
                    error=e,
                ),
            )
        except Exception as e:  # noqa: BLE001
            self._connect_error = e
            log.exception(
                "WS connection error: %s",
                _tls_event(
                    "ws_connect_failure",
                    url=self._url,
                    failure_class="ws_connect_failed",
                    ca_file=self._tls_config.ca_file,
                    ca_path=self._tls_config.ca_path,
                    insecure=self._tls_config.insecure,
                    error=e,
                ),
            )
        finally:
            self._stopped.set()

    def _dispatch(self, buf: bytes) -> None:
        resp = Response.decode(buf)
        if resp.seq is not None:
            with self._lock:
                q = self._pending.get(resp.seq)
            if q is not None:
                try:
                    q.put_nowait(resp)
                except queue.Full:
                    pass
                return
        if self._on_update is not None:
            try:
                self._on_update(resp)
            except Exception:  # noqa: BLE001
                log.exception("on_update callback failed")

    async def _close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
