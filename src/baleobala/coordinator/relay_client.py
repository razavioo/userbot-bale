"""Relay-side coordinator client.

Provides two helpers:
  - CoordinatorReporter  — fires-and-forgets control messages (ONLINE,
    HEARTBEAT, RELEASED, OFFLINE) to the coordinator via short Bale calls.
  - ExpectedClientSet    — tracks which client peer_ids the coordinator has
    told us to expect (via EXPECT_CLIENT) with TTL-based expiry.

Design constraints:
  - The reporter uses the same BaleApiClient that the relay already has for
    VPN calls. While an outbound report call is in flight that client is
    temporarily busy. Reports are queued and dispatched sequentially from a
    background thread, so the VPN listen path is never blocked.
  - HEARTBEAT is low-frequency (default 60 s) to avoid triggering Bale's
    rate limits. The coordinator's prune_stale() timeout must be set higher
    (>= 2× heartbeat interval).
  - ONLINE is sent once at startup (per JWT slot). RELEASED is sent per
    session end. These are event-driven, not periodic.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Sequence

from baleobala.bale.livekit_backend import LiveKitSession
from baleobala.coordinator.protocol import (
    CONTROL_TOPIC,
    ControlError,
    ControlMessage,
    Kind,
    decode,
    encode,
    make_heartbeat,
    make_offline,
    make_online,
    make_released,
)

log = logging.getLogger(__name__)

REMOTE_JOIN_TIMEOUT = 10.0
REPORT_CALL_TIMEOUT = 15.0


class CoordinatorReporter:
    """Send one-way control messages to the coordinator via short Bale calls.

    Each message is queued and dispatched in order by a single background
    thread so the relay's listen loop is never blocked. The reporter uses
    one BaleApiClient and one relay slot. Pass the client that is least
    likely to receive a VPN call at the same moment (e.g. the first one).
    """

    def __init__(
        self,
        *,
        coordinator_peer_id: int,
        relay_id: str,
        relay_peer_id: int,
        bale_client,  # BaleApiClient — avoid circular import
        identity_prefix: str = "relay-reporter",
    ) -> None:
        self._coordinator_peer_id = coordinator_peer_id
        self._relay_id = relay_id
        self._relay_peer_id = relay_peer_id
        self._client = bale_client
        self._identity = f"{identity_prefix}-{relay_id}"
        self._q: queue.Queue[ControlMessage | None] = queue.Queue()
        self._stop_ev = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            name=f"coord-reporter-{relay_id}",
            daemon=True,
        )
        self._heartbeat_thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_ev.set()
        self._q.put(None)
        self._thread.join(timeout=5)
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2)

    def report_online(self, capacity: int = 1) -> None:
        self._enqueue(make_online(
            relay_id=self._relay_id,
            peer_id=self._relay_peer_id,
            capacity=capacity,
        ))

    def report_heartbeat(self, in_use: list[int]) -> None:
        self._enqueue(make_heartbeat(relay_id=self._relay_id, in_use=in_use))

    def report_released(self, session_id: str) -> None:
        self._enqueue(make_released(relay_id=self._relay_id, session_id=session_id))

    def report_offline(self, reason: str = "shutdown") -> None:
        self._enqueue(make_offline(relay_id=self._relay_id, reason=reason))

    def start_heartbeat(
        self,
        *,
        interval: float = 60.0,
        get_in_use: Callable[[], list[int]],
    ) -> None:
        if self._heartbeat_thread is not None:
            return

        def _loop() -> None:
            while not self._stop_ev.wait(interval):
                try:
                    self.report_heartbeat(get_in_use())
                except Exception:  # noqa: BLE001
                    log.exception("coord-reporter: heartbeat error for relay=%s", self._relay_id)

        self._heartbeat_thread = threading.Thread(
            target=_loop,
            name=f"coord-heartbeat-{self._relay_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    # ---- internals -------------------------------------------------------

    def _enqueue(self, msg: ControlMessage) -> None:
        if not self._stop_ev.is_set():
            self._q.put(msg)

    def _worker(self) -> None:
        while True:
            msg = self._q.get()
            if msg is None or self._stop_ev.is_set():
                return
            try:
                self._dispatch(msg)
            except Exception:  # noqa: BLE001
                log.exception(
                    "coord-reporter: failed to send kind=%s for relay=%s",
                    msg.kind,
                    self._relay_id,
                )

    def _dispatch(self, msg: ControlMessage) -> None:
        try:
            creds = self._client.fetch_livekit_credentials(
                self._coordinator_peer_id,
                creds_timeout=REPORT_CALL_TIMEOUT,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "coord-reporter: dial coordinator failed for kind=%s relay=%s",
                msg.kind, self._relay_id,
            )
            return

        session = LiveKitSession(
            url=creds.url,
            token=creds.token,
            identity=f"{self._identity}-{msg.kind.lower()}",
        )
        try:
            session.start()
            session.wait_for_remote_participant(timeout=REMOTE_JOIN_TIMEOUT)
            ch = session.data_channel(topic=CONTROL_TOPIC, reliable=True)
            ch.send_bytes(encode(msg))
            # No reply expected for event messages
            log.info("coord-reporter: sent kind=%s for relay=%s", msg.kind, self._relay_id)
        except Exception:  # noqa: BLE001
            log.exception(
                "coord-reporter: send kind=%s failed for relay=%s",
                msg.kind, self._relay_id,
            )
        finally:
            try:
                session.stop()
            except Exception:  # noqa: BLE001
                pass


class ExpectedClientSet:
    """Thread-safe set of client peer_ids that the coordinator pre-approved.

    When the coordinator sends EXPECT_CLIENT, the relay registers the
    client here with a TTL. When the client's call arrives, the relay
    consumes the entry. Entries that expire without a call are pruned on
    the next check.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # peer_id → (session_id, expires_at)
        self._entries: dict[int, tuple[str, float]] = {}

    def register(
        self,
        *,
        client_peer_id: int,
        session_id: str,
        expires_in_secs: int,
    ) -> None:
        with self._lock:
            self._entries[int(client_peer_id)] = (
                session_id,
                time.monotonic() + float(expires_in_secs),
            )

    def consume(self, client_peer_id: int) -> str | None:
        """Remove and return the session_id if peer_id is expected and not expired."""
        with self._lock:
            self._prune()
            entry = self._entries.pop(int(client_peer_id), None)
        if entry is None:
            return None
        session_id, expires_at = entry
        if time.monotonic() > expires_at:
            return None
        return session_id

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [pid for pid, (_, exp) in self._entries.items() if exp < now]
        for pid in stale:
            self._entries.pop(pid, None)


def handle_coordinator_instruction(
    *,
    event,  # IncomingCallEvent
    expected: ExpectedClientSet,
    reporter: CoordinatorReporter | None,
    identity: str = "relay-ctrl",
) -> None:
    """Handle an inbound call that came from the coordinator (EXPECT_CLIENT).

    Joins the short LiveKit room, reads one EXPECT_CLIENT message, registers
    the expected client in `expected`, acks, and hangs up.
    """
    creds = event.credentials
    session = LiveKitSession(url=creds.url, token=creds.token, identity=identity)
    try:
        session.start()
        session.wait_for_remote_participant(timeout=REMOTE_JOIN_TIMEOUT)
        ch = session.data_channel(topic=CONTROL_TOPIC, reliable=True)

        payload = ch.recv_bytes(timeout=5.0)
        if payload is None:
            log.warning("relay: coordinator call arrived but no message; ignoring")
            return

        try:
            msg = decode(payload)
        except ControlError:
            log.warning("relay: coordinator call had non-control payload; ignoring")
            return

        if msg.kind != Kind.EXPECT_CLIENT:
            log.warning("relay: coordinator sent unexpected kind=%s", msg.kind)
            return

        client_peer_id = int(msg.get("client_peer_id", 0))
        session_id = str(msg.get("session_id", ""))
        expires_in = int(msg.get("expires_in_secs", 30))

        if not client_peer_id or not session_id:
            log.warning("relay: EXPECT_CLIENT missing fields; ignoring")
            return

        expected.register(
            client_peer_id=client_peer_id,
            session_id=session_id,
            expires_in_secs=expires_in,
        )
        log.info(
            "relay: registered expected client=%d session=%s expires_in=%ds",
            client_peer_id, session_id, expires_in,
        )

        # Send EXPECT_ACK back
        ack_msg = ControlMessage(kind=Kind.EXPECT_ACK, body={})
        ch.send_bytes(encode(ack_msg))

    except Exception:  # noqa: BLE001
        log.exception("relay: error handling coordinator instruction")
    finally:
        try:
            session.stop()
        except Exception:  # noqa: BLE001
            pass
