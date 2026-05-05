"""Bale-backed implementation of the CoordinatorTransport protocol.

Wires the wire-agnostic CoordinatorService to real BaleApiClient +
LiveKitSession instances. Each control exchange is a short Bale call whose
LiveKit data channel carries one request/response pair on topic "control".

Design note: the coordinator uses two BaleApiClient instances — one with the
"listen" JWT that all clients call, and one with a separate "dispatch" JWT
that the coordinator uses to place outbound calls to relays. This avoids
the race that exists when the same JWT is mid-outbound-call and an incoming
call lands at the same time. Both can be the same JWT if you accept the
race (single-JWT mode); pass `dispatch_jwt=listen_jwt` for that.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from baleobala.bale.api import BaleApiClient
from baleobala.bale.livekit_backend import LiveKitSession
from baleobala.bale.protos import IncomingCallEvent
from baleobala.coordinator.protocol import (
    CONTROL_TOPIC,
    ControlError,
    ControlMessage,
    decode,
    encode,
)
from baleobala.coordinator.transport import CoordinatorTransport, IncomingCall


log = logging.getLogger(__name__)


REMOTE_JOIN_TIMEOUT = 15.0
DEFAULT_QUICK_EXCHANGE_CREDS_TIMEOUT = 30.0


class BaleIncomingCall:
    """Wraps a single LiveKitSession + control DataChannel into the
    IncomingCall protocol the service consumes."""

    def __init__(
        self,
        *,
        peer_id: int,
        session: LiveKitSession,
        topic: str = CONTROL_TOPIC,
    ) -> None:
        self._peer_id = int(peer_id)
        self._session = session
        self._channel = session.data_channel(topic=topic, reliable=True)
        self._closed = False
        self._lock = threading.Lock()

    @property
    def peer_id(self) -> int:
        return self._peer_id

    def recv(self, *, timeout: float) -> ControlMessage | None:
        try:
            payload = self._channel.recv_bytes(timeout)
        except Exception:  # noqa: BLE001
            log.exception("coordinator: recv_bytes failed for peer=%d", self._peer_id)
            return None
        if payload is None:
            return None
        try:
            return decode(payload)
        except ControlError:
            log.warning("coordinator: dropping non-control payload from peer=%d", self._peer_id)
            return None

    def send(self, msg: ControlMessage) -> None:
        self._channel.send_bytes(encode(msg))

    def hangup(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._channel.close()
        except Exception:  # noqa: BLE001
            log.exception("coordinator: channel.close failed for peer=%d", self._peer_id)
        try:
            self._session.stop()
        except Exception:  # noqa: BLE001
            log.exception("coordinator: session.stop failed for peer=%d", self._peer_id)


class BaleCoordinatorTransport(CoordinatorTransport):
    """Bale + LiveKit implementation of CoordinatorTransport."""

    def __init__(
        self,
        *,
        listen_jwt: str,
        dispatch_jwt: Optional[str] = None,
        identity_prefix: str = "coordinator",
        ws_tls_config=None,
        client_factory: Callable[..., BaleApiClient] | None = None,
    ) -> None:
        self._identity_prefix = identity_prefix
        factory = client_factory or self._default_client_factory
        self._listen_client = factory(jwt=listen_jwt, ws_tls_config=ws_tls_config)
        if dispatch_jwt and dispatch_jwt != listen_jwt:
            self._dispatch_client = factory(jwt=dispatch_jwt, ws_tls_config=ws_tls_config)
            self._shared_client = False
        else:
            self._dispatch_client = self._listen_client
            self._shared_client = True
            log.warning(
                "coordinator: listen and dispatch share one JWT — incoming calls "
                "may be missed while we're placing an outbound call to a relay."
            )
        self._on_call: Callable[[IncomingCall], None] | None = None
        self._stopped = False
        self._stop_lock = threading.Lock()
        self._started = False

    @staticmethod
    def _default_client_factory(*, jwt: str, ws_tls_config=None) -> BaleApiClient:
        return BaleApiClient(jwt=jwt, ws_tls_config=ws_tls_config)

    # ---- CoordinatorTransport API ---------------------------------------

    def listen(self, on_call: Callable[[IncomingCall], None]) -> None:
        self._on_call = on_call
        if not self._started:
            self._listen_client.start()
            if not self._shared_client:
                self._dispatch_client.start()
            self._started = True
        self._listen_client.listen_incoming_calls(self._on_listen_event)

    def quick_exchange(
        self,
        *,
        peer_id: int,
        send: ControlMessage,
        timeout: float,
    ) -> ControlMessage | None:
        creds = self._dispatch_client.fetch_livekit_credentials(
            peer_id, creds_timeout=DEFAULT_QUICK_EXCHANGE_CREDS_TIMEOUT,
        )
        session = LiveKitSession(
            url=creds.url,
            token=creds.token,
            identity=f"{self._identity_prefix}-out-{peer_id}",
        )
        try:
            session.start()
            session.wait_for_remote_participant(timeout=REMOTE_JOIN_TIMEOUT)
        except Exception:  # noqa: BLE001
            log.exception("coordinator: outbound session to peer=%d failed", peer_id)
            try:
                session.stop()
            except Exception:
                pass
            return None

        channel = session.data_channel(topic=CONTROL_TOPIC, reliable=True)
        try:
            channel.send_bytes(encode(send))
            payload = channel.recv_bytes(timeout)
        except Exception:  # noqa: BLE001
            log.exception("coordinator: control exchange with peer=%d failed", peer_id)
            payload = None
        finally:
            try:
                channel.close()
            except Exception:
                pass
            try:
                session.stop()
            except Exception:
                pass

        if payload is None:
            return None
        try:
            return decode(payload)
        except ControlError:
            log.warning("coordinator: outbound peer=%d sent non-control reply", peer_id)
            return None

    def stop(self) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        try:
            self._listen_client.stop()
        except Exception:  # noqa: BLE001
            log.exception("coordinator: listen client stop failed")
        if not self._shared_client:
            try:
                self._dispatch_client.stop()
            except Exception:  # noqa: BLE001
                log.exception("coordinator: dispatch client stop failed")

    # ---- internal: incoming call handling --------------------------------

    def _on_listen_event(self, event: IncomingCallEvent) -> None:
        peer_id = event.peer_id
        if peer_id is None:
            log.warning("coordinator: incoming call missing peer_id; ignoring")
            return
        creds = event.credentials
        identity = f"{self._identity_prefix}-in-{peer_id}"
        try:
            session = LiveKitSession(url=creds.url, token=creds.token, identity=identity)
            session.start()
            # For relay event messages, the relay (caller) may join and leave
            # quickly. Don't fail if we can't wait for the remote; instead
            # proceed and try to read whatever data arrived. If the relay
            # already sent and disconnected, recv will return None (timeout).
            try:
                session.wait_for_remote_participant(timeout=REMOTE_JOIN_TIMEOUT)
            except (TimeoutError, RuntimeError) as exc:
                log.info(
                    "coordinator: remote did not stay in room for peer=%d: %s — "
                    "proceeding to read any data already in channel", peer_id, exc,
                )
        except Exception:  # noqa: BLE001
            log.exception("coordinator: failed to bring up inbound session for peer=%d", peer_id)
            return
        try:
            call = BaleIncomingCall(peer_id=peer_id, session=session, topic=CONTROL_TOPIC)
        except Exception:  # noqa: BLE001
            log.exception("coordinator: failed to create incoming call wrapper")
            try:
                session.stop()
            except Exception:
                pass
            return
        if self._on_call is None:
            log.warning("coordinator: received call but no listener; hanging up")
            call.hangup()
            return
        threading.Thread(
            target=self._dispatch_call,
            args=(call,),
            daemon=True,
            name=f"coord-call-{peer_id}",
        ).start()

    def _dispatch_call(self, call: BaleIncomingCall) -> None:
        if self._on_call is None:
            call.hangup()
            return
        try:
            self._on_call(call)
        except Exception:  # noqa: BLE001
            log.exception("coordinator: on_call handler crashed for peer=%d", call.peer_id)
            call.hangup()
