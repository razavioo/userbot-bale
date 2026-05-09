"""CoordinatorService: orchestrates the broker handoff.

The service is wire-agnostic — it consumes a CoordinatorTransport and dispatches
control messages. The same logic runs in production with a real Bale-backed
transport and in tests with an in-memory fake.

Lifecycle of a client connection:

    1. Client (peer C) places a Bale call to coordinator JWT.
    2. on_incoming_call → expects HELLO. Picks a relay slot R via policy.
    3. Sends ASSIGN{relay_peer_id=R.peer_id, session_id=S}, hangs up. Or DENY.
    4. Service places outbound call to R, sends EXPECT_CLIENT{client_peer_id=C,
       session_id=S}, expects EXPECT_ACK, hangs up.
    5. Client enters answer-mode; relay calls client. Coordinator is out.
    6. When the call ends, relay places a short call to coordinator with
       RELEASED{session_id=S}, which frees the slot.

Relay-originated events: ONLINE/HEARTBEAT/RELEASED/OFFLINE all arrive on the
listen path the same as client HELLOs and are dispatched by `kind`.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Callable

from baleobala.coordinator.auth import verify as verify_relay_sig
from baleobala.coordinator.policy import pick_relay
from baleobala.coordinator.protocol import (
    ControlError,
    ControlMessage,
    DenyReason,
    Kind,
    make_assign,
    make_deny,
    make_expect_client,
)
from baleobala.coordinator.registry import RelayRegistry, RelaySlot
from baleobala.coordinator.transport import CoordinatorTransport, IncomingCall
from baleobala.runtime.metrics import counter, gauge


log = logging.getLogger(__name__)


_ASSIGNS = counter(
    "baleobala_coordinator_assigns_total",
    "HELLO outcomes from the coordinator's point of view.",
    labelnames=("result",),
)
_RELAY_EVENTS = counter(
    "baleobala_coordinator_relay_events_total",
    "Relay-originated control events received (ONLINE/HEARTBEAT/RELEASED/OFFLINE).",
    labelnames=("kind",),
)
_RELAYS_ONLINE = gauge(
    "baleobala_coordinator_relays_online",
    "Number of relays currently registered.",
)
_RELAYS_CAPACITY = gauge(
    "baleobala_coordinator_relays_capacity",
    "Total in-use / total capacity, summed across all registered relays.",
    labelnames=("kind",),
)


DEFAULT_HELLO_TIMEOUT = 15.0
DEFAULT_EXPECT_TIMEOUT = 5.0
DEFAULT_SESSION_EXPIRES_SECS = 30


@dataclass
class ServiceConfig:
    hello_timeout: float = DEFAULT_HELLO_TIMEOUT
    expect_timeout: float = DEFAULT_EXPECT_TIMEOUT
    session_expires_secs: int = DEFAULT_SESSION_EXPIRES_SECS
    stale_relay_timeout_secs: float = 90.0


class CoordinatorService:
    def __init__(
        self,
        *,
        transport: CoordinatorTransport,
        registry: RelayRegistry,
        config: ServiceConfig | None = None,
        new_session_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._transport = transport
        self._registry = registry
        self._config = config or ServiceConfig()
        self._new_session_id = new_session_id
        self._lock = threading.Lock()
        self._stopped = False
        # Per-scrape gauges sourced from the live registry.
        try:
            _RELAYS_ONLINE.set_function(lambda: float(len(self._registry.list_relays())))
            _RELAYS_CAPACITY.set_function(
                lambda: float(self._registry.total_capacity()), kind="capacity",
            )
            _RELAYS_CAPACITY.set_function(
                lambda: float(self._registry.total_in_use()), kind="in_use",
            )
        except Exception:
            pass

    def start(self) -> None:
        self._transport.listen(self._handle_incoming_call)

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self._transport.stop()

    # ---- main dispatch ---------------------------------------------------

    def _handle_incoming_call(self, call: IncomingCall) -> None:
        try:
            msg = call.recv(timeout=self._config.hello_timeout)
        except ControlError:
            log.warning("coordinator: malformed control payload from peer=%d", call.peer_id)
            self._safe_hangup(call)
            return
        except Exception:  # noqa: BLE001
            log.exception("coordinator: error reading first control message")
            self._safe_hangup(call)
            return

        if msg is None:
            log.warning("coordinator: peer=%d sent no control message before timeout", call.peer_id)
            self._safe_hangup(call)
            return

        try:
            if msg.kind == Kind.HELLO:
                self._handle_hello(call, msg)
            elif msg.kind == Kind.ONLINE:
                self._handle_online(call, msg)
            elif msg.kind == Kind.HEARTBEAT:
                self._handle_heartbeat(call, msg)
            elif msg.kind == Kind.RELEASED:
                self._handle_released(call, msg)
            elif msg.kind == Kind.OFFLINE:
                self._handle_offline(call, msg)
            else:
                log.warning("coordinator: unexpected kind=%s from peer=%d", msg.kind, call.peer_id)
                self._safe_send(call, make_deny(reason=DenyReason.INTERNAL, detail=f"unexpected kind {msg.kind}"))
        except Exception:  # noqa: BLE001
            log.exception("coordinator: handler crashed for kind=%s", msg.kind)
        finally:
            self._safe_hangup(call)

    # ---- client-side flow ------------------------------------------------

    def _handle_hello(self, call: IncomingCall, msg: ControlMessage) -> None:
        # Bale push notifications only carry the callee's OutPeer, so
        # `call.peer_id` is the coordinator's own peer_id, not the caller's.
        # Prefer the explicit `client_peer_id` field from HELLO; fall back to
        # `call.peer_id` only for legacy clients that don't send it.
        client_peer_id = int(msg.get("client_peer_id") or call.peer_id)
        if client_peer_id <= 0:
            log.warning("coordinator: HELLO missing client_peer_id and call has no peer_id")
            self._safe_send(call, make_deny(reason=DenyReason.INTERNAL, detail="missing client_peer_id"))
            _ASSIGNS.inc(result="deny_missing_peer_id")
            return
        slot = pick_relay(self._registry, client_peer_id=client_peer_id)
        if slot is None:
            log.info(
                "coordinator: deny client=%d capacity=%d/%d",
                client_peer_id,
                self._registry.total_in_use(),
                self._registry.total_capacity(),
            )
            self._safe_send(call, make_deny(reason=DenyReason.NO_CAPACITY))
            _ASSIGNS.inc(result="deny_no_capacity")
            return

        session_id = self._new_session_id()
        # 8-char cid derived from session_id so operators can grep the
        # same id across coordinator + relay (EXPECT_CLIENT carries
        # session_id, the relay's EXPECT_CLIENT log line includes
        # session=<sid>, and a future protocol bump can put the cid
        # directly into the body).
        cid = session_id[:8] if isinstance(session_id, str) else uuid.uuid4().hex[:8]
        try:
            self._registry.reserve_session(
                session_id=session_id,
                client_peer_id=client_peer_id,
                relay_id=slot.relay_id,
                expires_in_secs=self._config.session_expires_secs,
            )
        except Exception:  # noqa: BLE001
            log.exception("coordinator: cid=%s reserve_session failed", cid)
            self._safe_send(call, make_deny(reason=DenyReason.INTERNAL))
            _ASSIGNS.inc(result="deny_reserve_failed")
            return

        # Instruct the relay BEFORE sending ASSIGN. The device dials the
        # relay within ~100-300 ms of receiving ASSIGN, but the dispatch
        # call (Bale push + LiveKit join + EXPECT_CLIENT exchange) takes
        # 5-8 s. With the previous "ASSIGN first, instruct later" order,
        # the device's call landed at the relay before EXPECT_CLIENT was
        # registered; the relay's probe set _account_active=True and then
        # skip-probe'd the dispatch call when it finally arrived, so the
        # device timed out without ever receiving EXPECT_CLIENT and got
        # rejected as an unknown caller. Dispatching first guarantees the
        # relay has the client pre-approved by the time the device dials.
        # The device's HELLO recv timeout (25 s) comfortably absorbs the
        # added latency on the client side.
        if not self._instruct_relay(
            slot=slot,
            client_peer_id=client_peer_id,
            session_id=session_id,
            cid=cid,
        ):
            self._safe_send(
                call,
                make_deny(
                    reason=DenyReason.INTERNAL,
                    detail="relay did not ack EXPECT_CLIENT",
                ),
            )
            self._safe_hangup(call)
            _ASSIGNS.inc(result="deny_no_ack")
            return

        self._safe_send(
            call,
            make_assign(
                relay_peer_id=slot.peer_id,
                session_id=session_id,
                expires_in_secs=self._config.session_expires_secs,
            ),
        )
        self._safe_hangup(call)
        log.info(
            "coordinator: cid=%s assigned client=%d → relay=%s session=%s",
            cid, client_peer_id, slot.relay_id, session_id,
        )
        _ASSIGNS.inc(result="ok")

    def _instruct_relay(
        self,
        *,
        slot: RelaySlot,
        client_peer_id: int,
        session_id: str,
        cid: str = "",
    ) -> bool:
        """Send EXPECT_CLIENT to the relay and wait for EXPECT_ACK.

        Returns True if the relay acknowledged so the caller can safely
        proceed to ASSIGN the client. On any failure the session is
        released and False is returned so the caller can DENY instead of
        directing the client at a relay that won't accept it.
        """
        msg = make_expect_client(
            client_peer_id=client_peer_id,
            session_id=session_id,
            expires_in_secs=self._config.session_expires_secs,
        )
        try:
            ack = self._transport.quick_exchange(
                peer_id=slot.peer_id,
                send=msg,
                timeout=self._config.expect_timeout,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "coordinator: cid=%s quick_exchange to relay=%s failed",
                cid, slot.relay_id,
            )
            self._registry.release_session(session_id)
            return False

        if ack is None or ack.kind != Kind.EXPECT_ACK:
            log.warning(
                "coordinator: cid=%s relay=%s did not ack EXPECT_CLIENT (got %r); rolling back session=%s",
                cid, slot.relay_id,
                ack.kind if ack else None,
                session_id,
            )
            self._registry.release_session(session_id)
            return False
        return True

    # ---- relay-side events -----------------------------------------------

    def _check_relay_auth(self, relay_id: str, msg: ControlMessage) -> bool:
        """Verify HMAC sig on relay control messages (A6).

        Returns True (auth ok or relay not enrolled — legacy compat).
        Returns False and logs a warning if the relay IS enrolled but the
        sig is missing or wrong — the caller must drop the message.
        """
        secret = self._registry.get_secret(relay_id)
        if secret is None:
            # Not enrolled: accept but warn so operators know.
            log.warning(
                "coordinator: relay=%s has no enrolled secret — accepting without auth "
                "(run 'baleobala coordinator enroll' to fix)",
                relay_id,
            )
            return True
        sig = str(msg.get("sig", ""))
        if not verify_relay_sig(secret, relay_id, msg.kind, sig):
            log.warning(
                "coordinator: relay=%s FAILED auth for kind=%s sig=%r — dropping message",
                relay_id, msg.kind, sig,
            )
            return False
        return True

    def _handle_online(self, call: IncomingCall, msg: ControlMessage) -> None:
        relay_id = str(msg.get("relay_id", ""))
        peer_id = int(msg.get("peer_id", call.peer_id))
        capacity = int(msg.get("capacity", 1))
        if not relay_id:
            log.warning("coordinator: ONLINE missing relay_id from peer=%d", call.peer_id)
            return
        if not self._check_relay_auth(relay_id, msg):
            return
        slot = RelaySlot(relay_id=relay_id, peer_id=peer_id, capacity=capacity, in_use=[])
        self._registry.register(slot)
        log.info("coordinator: relay=%s online peer=%d capacity=%d", relay_id, peer_id, capacity)
        _RELAY_EVENTS.inc(kind="online")

    def _handle_heartbeat(self, call: IncomingCall, msg: ControlMessage) -> None:
        relay_id = str(msg.get("relay_id", ""))
        in_use = [int(p) for p in msg.get("in_use", [])]
        if not relay_id:
            return
        if not self._check_relay_auth(relay_id, msg):
            return
        _RELAY_EVENTS.inc(kind="heartbeat")
        if self._registry.heartbeat(relay_id, in_use=in_use) is None:
            # `call.peer_id` is the callee's (coordinator's own) peer_id under
            # Bale's push semantics; prefer the explicit `peer_id` field from
            # the HEARTBEAT body if the relay sent it. Falling back to
            # `call.peer_id` makes the registry useless because we'd record
            # the coordinator as a relay — only do that for legacy clients.
            peer_id = int(msg.get("peer_id") or call.peer_id)
            capacity = int(msg.get("capacity", 1))
            log.info(
                "coordinator: heartbeat from unknown relay=%s — re-registering peer=%d capacity=%d",
                relay_id, peer_id, capacity,
            )
            self._registry.register(
                RelaySlot(relay_id=relay_id, peer_id=peer_id, capacity=capacity, in_use=in_use)
            )

    def _handle_released(self, call: IncomingCall, msg: ControlMessage) -> None:
        session_id = str(msg.get("session_id", ""))
        if not session_id:
            return
        relay_id = str(msg.get("relay_id", ""))
        if relay_id and not self._check_relay_auth(relay_id, msg):
            return
        _RELAY_EVENTS.inc(kind="released")
        released = self._registry.release_session(session_id)
        cid = session_id[:8]
        if released is None:
            log.info("coordinator: cid=%s RELEASED for unknown session=%s", cid, session_id)
        else:
            log.info(
                "coordinator: cid=%s released session=%s relay=%s client=%d",
                cid, session_id,
                released.relay_id,
                released.client_peer_id,
            )

    def _handle_offline(self, call: IncomingCall, msg: ControlMessage) -> None:
        relay_id = str(msg.get("relay_id", ""))
        if not relay_id:
            return
        if not self._check_relay_auth(relay_id, msg):
            return
        log.info("coordinator: relay=%s offline reason=%s", relay_id, msg.get("reason", ""))
        self._registry.mark_offline(relay_id)
        _RELAY_EVENTS.inc(kind="offline")

    # ---- maintenance -----------------------------------------------------

    def prune_stale(self) -> None:
        removed = self._registry.prune_stale(timeout_secs=self._config.stale_relay_timeout_secs)
        for relay_id in removed:
            log.info("coordinator: pruned stale relay=%s", relay_id)
        expired = self._registry.expire_pending()
        for session in expired:
            # Sessions expire when the relay never sends RELEASED to confirm
            # teardown. With the current protocol that's the normal case for
            # any session that's still active on the relay — there's no
            # ACTIVATED report, so the registry has no way to extend the TTL.
            # Logging at INFO clutters the journal with one line per session
            # per ~30s; drop to DEBUG so it's available for forensics but
            # doesn't drown the real signals.
            log.debug("coordinator: expired pending session=%s", session.session_id)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _safe_send(call: IncomingCall, msg: ControlMessage) -> None:
        try:
            call.send(msg)
        except Exception:  # noqa: BLE001
            log.exception("coordinator: send failed for kind=%s", msg.kind)

    @staticmethod
    def _safe_hangup(call: IncomingCall) -> None:
        try:
            call.hangup()
        except Exception:  # noqa: BLE001
            log.exception("coordinator: hangup failed")
