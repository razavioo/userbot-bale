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

    def report_heartbeat(self, in_use: list[int], *, capacity: int | None = None) -> None:
        self._enqueue(make_heartbeat(
            relay_id=self._relay_id,
            in_use=in_use,
            peer_id=self._relay_peer_id,
            capacity=capacity,
        ))

    def report_released(self, session_id: str) -> None:
        self._enqueue(make_released(relay_id=self._relay_id, session_id=session_id))

    def report_offline(self, reason: str = "shutdown") -> None:
        self._enqueue(make_offline(relay_id=self._relay_id, reason=reason))

    def start_heartbeat(
        self,
        *,
        interval: float = 60.0,
        get_in_use: Callable[[], list[int]],
        capacity: int | None = None,
    ) -> None:
        if self._heartbeat_thread is not None:
            return

        def _loop() -> None:
            while not self._stop_ev.wait(interval):
                try:
                    self.report_heartbeat(get_in_use(), capacity=capacity)
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

        import time as _time
        session = LiveKitSession(
            url=creds.url,
            token=creds.token,
            identity=f"{self._identity}-{msg.kind.lower()}",
            publish_audio=False,
        )
        try:
            try:
                session.start()
            except RuntimeError as exc:
                # "LiveKit remote participant disconnected" during startup means
                # the coordinator joined the room, received our message (or sent
                # before we could), and hung up before our session finished
                # starting. This is expected for the fast coordinator path.
                # Treat it as best-effort success and log at INFO level.
                log.info(
                    "coord-reporter: session startup race for kind=%s relay=%s: %s "
                    "(coordinator may have received the message already)",
                    msg.kind, self._relay_id, exc,
                )
                return

            # Wait briefly for coordinator to join the room before sending.
            # We use wait_for_remote_participant with a generous timeout; if it
            # times out (coordinator left already), treat as best-effort.
            try:
                session.wait_for_remote_participant(timeout=REMOTE_JOIN_TIMEOUT)
            except (TimeoutError, RuntimeError):
                log.info(
                    "coord-reporter: coordinator not present in room for kind=%s relay=%s; "
                    "will retry on next heartbeat", msg.kind, self._relay_id,
                )
                return

            ch = session.data_channel(topic=CONTROL_TOPIC, reliable=True)
            ch.send_bytes(encode(msg))
            # close() joins the sender thread, ensuring publish_data completes
            # before we disconnect the room. This replaces the old sleep(0.5)
            # guess with a deterministic drain.
            ch.close()
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
            # 2s flush window: A7's "synchronous stop is enough" hypothesis
            # was wrong. Without this pause, back-to-back HEARTBEAT sessions
            # on the same Bale account race the Rust tokio runtime's async
            # teardown, eventually causing "LiveKit room did not become
            # ready within 45s" timeouts on the next attempt and
            # 401 Unauthorized on the receiving coordinator.
            # Live regression observed 2026-05-16 post-deploy.
            _time.sleep(2.0)


class ExpectedClientSet:
    """Thread-safe set of client peer_ids that the coordinator pre-approved.

    When the coordinator sends EXPECT_CLIENT, the relay registers the
    client here with a TTL. When the client's call arrives, the relay
    consumes the entry. Entries that expire without a call are pruned on
    the next check.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # peer_id → (session_id, session_psk, expires_at)
        self._entries: dict[int, tuple[str, str, float]] = {}

    def register(
        self,
        *,
        client_peer_id: int,
        session_id: str,
        expires_in_secs: int,
        session_psk: str = "",
    ) -> None:
        with self._lock:
            self._entries[int(client_peer_id)] = (
                session_id,
                session_psk,
                time.monotonic() + float(expires_in_secs),
            )

    def consume(self, client_peer_id: int) -> tuple[str, str] | None:
        """Remove and return (session_id, session_psk) if expected and not expired.

        Returns None if the peer is unknown or the TTL has passed.
        session_psk is an empty string when the coordinator did not send one
        (legacy relay path without B3).
        """
        with self._lock:
            self._prune()
            entry = self._entries.pop(int(client_peer_id), None)
        if entry is None:
            return None
        session_id, session_psk, expires_at = entry
        if time.monotonic() > expires_at:
            return None
        return session_id, session_psk

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [pid for pid, (_, _psk, exp) in self._entries.items() if exp < now]
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
    session = LiveKitSession(url=creds.url, token=creds.token, identity=identity, publish_audio=False)
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
        session_psk = str(msg.get("session_psk", ""))

        if not client_peer_id or not session_id:
            log.warning("relay: EXPECT_CLIENT missing fields; ignoring")
            return

        expected.register(
            client_peer_id=client_peer_id,
            session_id=session_id,
            expires_in_secs=expires_in,
            session_psk=session_psk,
        )
        log.info(
            "relay: registered expected client=%d session=%s expires_in=%ds psk=%s",
            client_peer_id, session_id, expires_in, "yes" if session_psk else "no",
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
