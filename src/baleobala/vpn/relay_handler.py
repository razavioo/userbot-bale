"""RelayCallHandler — per-account incoming-call state machine.

Originally a 380-line closure (`on_incoming_call` + `_do_on_incoming`)
inside `cmd_vpn_mesh`. Extracted so the logic can be unit-tested with
fakes; behavior is intentionally byte-equivalent to the original
closure, including every stderr log line that production triage greps
for. Recent fixes preserved verbatim:

  - c318c14 — coordinator dial-race (instruct relay before assign)
  - cf66326 — sess_id 0x1111 hardcoded to match Android
  - 36a8579 — per-account/per-caller routing via LiveKit identity
  - a08072a — _account_active scoped to probe phase only
  - 2215aee — peer-lost grace + reconnect-evict
  - 9fea7eb — reaper serialization (lives in session_reaper.py)

This file deliberately avoids any "improvement" beyond the extraction
itself; that is reserved for follow-up changes once the regression
suite at tests/vpn/test_relay_handler.py is exercising the new shape.
"""

from __future__ import annotations

import logging
import sys
import threading
import time as _time
import uuid
from typing import Any, Callable, Protocol

from baleobala.runtime.metrics import counter, gauge

from .constants import DEFAULT_TUNNEL_SESS_ID
from .relay_state import RelayState


log = logging.getLogger(__name__)


# Outcomes:
#   committed         — full tunnel up (sessions.append reached)
#   slot_busy         — call refused because account already has a session
#   no_capacity       — allocator full
#   session_start_failed — LiveKit session failed to start
#   no_caller_identity — couldn't read caller user_id from LiveKit participant list
#   missing_peer_id   — Bale event arrived without a canonical peer_id
#   provision_failed  — tunnel bring-up failed inside _provision_tunnel
_CALLS = counter(
    "baleobala_relay_calls_total",
    "Inbound relay calls, labelled by outcome.",
    labelnames=("outcome",),
)
_EVICTIONS = counter(
    "baleobala_relay_evictions_total",
    "Stale sessions evicted on a fresh inbound call (reconnect-evict path).",
)
_STALE_RECOVERY = counter(
    "baleobala_relay_stale_flag_recoveries_total",
    "Times the stale-flag auto-recovery branch fired (leaked _account_active flag was force-cleared).",
)
_SESSIONS_GAUGE = gauge(
    "baleobala_relay_sessions_active",
    "Current count of live VPN sessions on this relay process.",
)


class _LiveKitSessionFactory(Protocol):
    def __call__(self, *, url: str, token: str, identity: str, **kwargs: Any) -> Any: ...


class _DataChannelTransportFactory(Protocol):
    def __call__(self, session: Any, *, topic: str, reliable: bool) -> Any: ...


class _EncryptedTransportFactory(Protocol):
    def __call__(self, transport: Any, key: bytes) -> Any: ...


class _KeepaliveFactory(Protocol):
    def __call__(self, session: Any, *, interval: float) -> Any: ...


class _ProvisionMessageFactory(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


class _RecvProvision(Protocol):
    def __call__(self, transport: Any, *, timeout: float) -> Any: ...


class RelayCallHandler:
    """Owns the per-incoming-call state machine for one mesh relay process.

    Coordinator-free model: the relay accepts any inbound Bale call,
    joins the LiveKit room, resolves the caller's user_id from the
    participant identity, and provisions a tunnel. The PSK handshake
    on the encrypted DataChannel authenticates the caller (without a
    valid PSK no traffic can flow). If a per-account capacity slot is
    free, the call becomes a tunnel; otherwise it is refused.
    """

    def __init__(
        self,
        *,
        identity_prefix: str,
        pool_cidr: str,
        tun_mtu: int,
        provision_timeout: float,
        psk_key: bytes | None,
        mesh: Any,
        allocator: Any,
        control: Any,
        session_map: dict[str, str],
        session_map_lock: threading.Lock,
        probe_locks: dict[int, Any],
        relay_state: RelayState,
        stale_active_flag_secs: float,
        sessions: list[tuple[int, Any, Any]],
        livekit_session_factory: _LiveKitSessionFactory,
        keepalive_factory: _KeepaliveFactory,
        datachannel_transport_factory: _DataChannelTransportFactory,
        encrypted_transport_factory: _EncryptedTransportFactory,
        provision_message_factory: _ProvisionMessageFactory,
        recv_provision: _RecvProvision,
        provisioning_error_cls: type[Exception],
        log_stream=sys.stderr,
    ) -> None:
        self._identity_prefix = identity_prefix
        self._pool_cidr = pool_cidr
        self._tun_mtu = tun_mtu
        self._provision_timeout = provision_timeout
        self._psk_key = psk_key
        self._mesh = mesh
        self._allocator = allocator
        self._control = control
        self._session_map = session_map
        self._session_map_lock = session_map_lock
        self._probe_locks = probe_locks
        self._relay_state = relay_state
        self._stale_active_flag_secs = stale_active_flag_secs
        self._sessions = sessions
        self._LiveKitSession = livekit_session_factory
        self._Keepalive = keepalive_factory
        self._DataChannelTransport = datachannel_transport_factory
        self._EncryptedTransport = encrypted_transport_factory
        self._ProvisionMessage = provision_message_factory
        self._recv_provision = recv_provision
        self._ProvisioningError = provisioning_error_cls
        self._log = log_stream
        # Wire the sessions-active gauge to the live list. Idempotent;
        # if multiple handlers exist (one per relay process), they all
        # point at the same list anyway.
        try:
            _SESSIONS_GAUGE.set_function(lambda: float(len(self._sessions)))
        except Exception:
            pass

    @staticmethod
    def _bump(outcome: str) -> None:
        try:
            _CALLS.inc(outcome=outcome)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public entry point — the listen callback adapter binds the
    # account_index for each registered Bale client.
    # ------------------------------------------------------------------

    def handle_call(self, event: Any, account_index: int = 0) -> None:
        # 8-char hex correlation ID — short enough to read in a journal
        # tail, wide enough (~4 billion) that two concurrent calls
        # collide one in a million. Operators grep `cid=<id>` to pull
        # the full lifecycle of a single call from probe → assign →
        # tunnel → reap.
        cid = uuid.uuid4().hex[:8]

        peer_id = event.peer_id
        if peer_id is None:
            print(
                f"[vpn-mesh] cid={cid} incoming call rejected: missing canonical peer_id",
                file=self._log,
            )
            self._bump("missing_peer_id")
            return

        # cell[0] = "did this call set _account_active=True"
        # cell[1] = "did this call reach sessions.append (long-lived)"
        # On exit:
        #   - If we set the flag and reached sessions.append → leave True
        #     (the reaper / _on_drop will clear it when the session ends).
        #   - If we set the flag but didn't reach sessions.append → MUST
        #     clear the flag here, otherwise the account is locked out.
        #   - If we didn't set the flag (skip-probe path) → DO NOT touch
        #     it; another thread owns it.
        state = [False, False]
        try:
            self._do_handle(event, account_index, peer_id, state, cid)
        finally:
            if state[0] and not state[1]:
                self._relay_state.transition(
                    account_index, False, "outer-finally", cid=cid,
                )

    # ------------------------------------------------------------------
    # Internal flow — split out for readability but preserves the exact
    # control flow (and stderr lines) of the original closure.
    # ------------------------------------------------------------------

    def _do_handle(
        self, event: Any, account_index: int, peer_id: int, state: list, cid: str,
    ) -> None:
        # _state[0] := we are the caller that set _account_active=True
        # _state[1] := we reached sessions.append (commit)
        #
        # event.peer_id under Bale push semantics is the CALLEE (our own
        # account), so every caller on the same relay account would
        # collide on mesh.issue_client. Join the LiveKit room, read the
        # actual caller user_id from the participant identity, then
        # promote that session to a VPN tunnel. The PSK handshake on
        # the encrypted DataChannel is what authenticates the caller.
        session_pre_joined = self._join_room(event, account_index, peer_id, state, cid)
        if session_pre_joined is None:
            return
        resolved_peer_id = self._resolve_caller_identity(
            session_pre_joined, peer_id, cid,
        )
        if resolved_peer_id is None:
            try: session_pre_joined.stop()
            except Exception: pass  # noqa: BLE001
            self._relay_state.transition(
                account_index, False, "no-caller-identity", cid=cid,
            )
            return
        peer_id = resolved_peer_id

        print(
            f"[vpn-mesh] cid={cid} incoming call → peer_id={peer_id} account={account_index}",
            file=self._log,
        )
        slot = self._allocator.join(peer_id)
        if slot is None:
            print(
                f"[vpn-mesh] cid={cid} incoming call rejected: no server JWT capacity "
                f"peer_id={peer_id} active={len(self._allocator.assignment())} "
                f"capacity={self._allocator.total_capacity}",
                file=self._log,
            )
            if session_pre_joined is not None:
                try:
                    session_pre_joined.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._relay_state.transition(
                account_index, False, "no-jwt-capacity", cid=cid,
            )
            self._bump("no_capacity")
            return
        print(
            f"[vpn-mesh] cid={cid} server_jwt_assignment={self._allocator.assignment()} "
            f"slots={[s.peer_ids for s in self._allocator.slots()]}",
            file=self._log,
        )

        self._provision_tunnel(event, account_index, peer_id, session_pre_joined, state, cid)

    # ---- join LiveKit room for the inbound call ---------------------

    def _join_room(
        self, event: Any, account_index: int, peer_id: int, state: list, cid: str,
    ) -> Any:
        """Accept the inbound Bale call by joining the LiveKit room.
        Returns the joined session ready to be promoted to a VPN tunnel,
        or None if the slot is busy / session start failed."""
        with self._probe_locks[account_index]:
            # If this relay account already hosts an active VPN session,
            # refuse the new call. Starting a concurrent LiveKit session
            # while a VPN session is running overloads the shared
            # livekit-ffi Rust runtime, delaying on_data_received
            # callbacks and causing keepalive drops → tunnel_dead within
            # 30 s. The caller's app will fail over to the next relay
            # peer_id in its list.
            slot = self._allocator.slots()[account_index]
            if slot.peer_ids or self._relay_state.is_active(account_index):
                self._maybe_evict_stale_sessions(account_index, cid)
                slot = self._allocator.slots()[account_index]
                # Stale-flag auto-recovery
                if not slot.peer_ids and self._relay_state.is_stale(
                    account_index, threshold=self._stale_active_flag_secs
                ):
                    age = self._relay_state.age(account_index)
                    print(
                        f"[vpn-mesh] cid={cid} _active[{account_index}] looks stale "
                        f"({age:.1f}s old, slot empty) — clearing & continuing",
                        file=self._log,
                    )
                    self._relay_state.transition(
                        account_index, False, "stale-recovery", cid=cid,
                    )
                    try:
                        _STALE_RECOVERY.inc()
                    except Exception:
                        pass
                if slot.peer_ids or self._relay_state.is_active(account_index):
                    age_str = (
                        f" age={self._relay_state.age(account_index):.1f}s"
                        if self._relay_state.is_active(account_index)
                        else ""
                    )
                    print(
                        f"[vpn-mesh] cid={cid} refusing call on account={account_index}: "
                        f"already in use (slot={slot.peer_ids} "
                        f"active={self._relay_state.is_active(account_index)}{age_str})",
                        file=self._log,
                    )
                    self._bump("slot_busy")
                    return None

            self._relay_state.transition(account_index, True, "join-start", cid=cid)
            state[0] = True  # we own the flag now
            session = self._LiveKitSession(
                url=event.credentials.url,
                token=event.credentials.token,
                identity=f"{self._identity_prefix}-{account_index}-{peer_id}",
                publish_audio=False,
            )
            try:
                session.start()
                # Wait briefly for the caller to join the room so we can
                # read their identity. If they don't show up the call is
                # spam/abandoned; bail out.
                try:
                    session.wait_for_remote_participant(timeout=5.0)
                except (TimeoutError, RuntimeError):
                    pass
            except Exception:  # noqa: BLE001
                log.exception("relay: cid=%s session start failed", cid)
                try: session.stop()
                except Exception: pass  # noqa: BLE001
                self._relay_state.transition(
                    account_index, False, "session-start-failed", cid=cid,
                )
                self._bump("session_start_failed")
                return None
        return session

    def _resolve_caller_identity(self, session: Any, peer_id: int, cid: str) -> int | None:
        """Read the caller user_id from the LiveKit participant identity.
        event.peer_id is the relay's own user_id under Bale push
        semantics, so we MUST resolve the real caller from the room
        participant list before issuing a /30 — otherwise every caller on
        the same relay account would collide on mesh.issue_client."""
        try:
            identities = session.remote_participant_identities()
        except Exception:  # noqa: BLE001
            identities = []
        for ident in identities:
            try:
                caller_id = int(ident)
            except (TypeError, ValueError):
                continue
            if caller_id > 0 and caller_id != peer_id:
                print(
                    f"[vpn-mesh] cid={cid} caller resolved from LiveKit identity: {caller_id}",
                    file=self._log,
                )
                return caller_id
        # Couldn't resolve — caller didn't join the room within wait
        # window, or only the relay itself is present.
        print(
            f"[vpn-mesh] cid={cid} could not resolve caller identity "
            f"(participants={identities}); rejecting call",
            file=self._log,
        )
        self._bump("no_caller_identity")
        return None

    def _maybe_evict_stale_sessions(self, account_index: int, cid: str) -> None:
        """Reconnect-evict (commit 2215aee). When a fresh inbound call
        arrives and the existing session has lost its peer or is
        terminal, force-cleanup immediately rather than waiting out
        PEER_LOST_GRACE_SECS."""
        evicted: list[int] = []
        for i, (_pid, sess, _) in list(enumerate(self._sessions)):
            try:
                is_terminal = sess.is_terminal()
                peer_lost = getattr(sess, "_peer_disconnect_at", None) is not None
            except Exception:  # noqa: BLE001
                is_terminal, peer_lost = True, True
            if is_terminal or peer_lost:
                evicted.append(i)
        if evicted:
            print(
                f"[vpn-mesh] cid={cid} reconnect detected for account={account_index}; "
                f"evicting {len(evicted)} stale session(s) and continuing",
                file=self._log,
            )
            try:
                _EVICTIONS.inc(len(evicted))
            except Exception:
                pass
            for i in reversed(evicted):
                ev_pid, ev_sess, _ = self._sessions.pop(i)
                try: ev_sess.stop()
                except Exception: pass  # noqa: BLE001
                try: self._mesh.drop_client(ev_pid)
                except Exception: pass  # noqa: BLE001
                try: self._control.release_mesh_assignment(ev_pid)
                except Exception: pass  # noqa: BLE001
                try: self._allocator.leave(ev_pid)
                except Exception: pass  # noqa: BLE001


    # ---- bring up the tunnel ---------------------------------------

    def _provision_tunnel(
        self,
        event: Any,
        account_index: int,
        peer_id: int,
        session_pre_joined: Any,
        state: list,
        cid: str,
    ) -> None:
        on_drop = self._make_on_drop(account_index, cid)
        try:
            if session_pre_joined is not None:
                session = session_pre_joined
            else:
                session = self._LiveKitSession(
                    url=event.credentials.url,
                    token=event.credentials.token,
                    identity=f"{self._identity_prefix}-{account_index}-{peer_id}",
                )
                session.start()
            # Publish audio so the Bale SFU keeps this long-lived session
            # alive (without a media track the SFU closes it after ~20s).
            # _join_room creates the session with publish_audio=False;
            # enabling here is the promotion to a long-lived VPN session.
            try:
                session.enable_audio()
            except Exception:  # noqa: BLE001
                log.exception("relay: cid=%s enable_audio failed", cid)
            self._Keepalive(session, interval=20.0).start()
            dc = self._DataChannelTransport(session, topic="vpn", reliable=False)
            # PSK authenticates the caller: if the encrypted handshake
            # fails no data ever flows. With no PSK configured (debug
            # only) the relay accepts plaintext.
            transport = (
                self._EncryptedTransport(dc, self._psk_key) if self._psk_key else dc
            )
            # Must match the Android client's hardcoded DEFAULT_TUNNEL_SESS_ID.
            # Per-peer offsets caused frames to be silently dropped by the
            # receiving side's sess_id filter — see exit_node.py and the
            # cross-language assertion in tests/vpn/test_constants.py.
            sess_id = DEFAULT_TUNNEL_SESS_ID
            record = self._control.issue_mesh_assignment(
                peer_id,
                pool_cidr=self._pool_cidr,
                transport="dc",
                session_id=sess_id,
            )
            assignment = record.assignment()
            self._mesh.issue_client(
                peer_id, transport, assignment=assignment, on_drop=on_drop,
            )
            print(
                f"[vpn-mesh] cid={cid} peer={peer_id} status=assigned "
                f"client={assignment.client} gateway={assignment.gateway}",
                file=self._log,
            )
            transport.send_bytes(
                self._ProvisionMessage(
                    version=1,
                    kind="assign",
                    peer_id=peer_id,
                    session_id=sess_id,
                    pool_cidr=assignment.pool_cidr,
                    prefix=assignment.prefix,
                    gateway_ip=assignment.gateway,
                    client_ip=assignment.client,
                    tun_mtu=self._tun_mtu,
                    transport="dc",
                ).encode()
            )
            ack = self._recv_provision(transport, timeout=self._provision_timeout)
            if ack is None or ack.kind != "ack" or ack.session_id != sess_id:
                raise self._ProvisioningError(
                    "client did not acknowledge provisioning"
                )
            print(
                f"[vpn-mesh] cid={cid} peer={peer_id} status=acknowledged",
                file=self._log,
            )
            assignment = self._mesh.activate_client(
                peer_id, mtu_override=transport.mtu,
            )
            self._control.activate_mesh_assignment(
                peer_id, transport="dc", session_id=sess_id,
            )
            print(
                f"[vpn-mesh] cid={cid} peer={peer_id} status=active → assigned "
                f"{assignment.client} (gateway={assignment.gateway})",
                file=self._log,
            )
            self._sessions.append((peer_id, session, transport))
            state[1] = True  # outer finally checks this
            self._bump("committed")
            # Probe + provisioning are done — release the per-account
            # probe-in-progress flag so a *different* peer (e.g. a
            # second device on the same Bale account) can also probe
            # this account. The reaper handles cleanup of the session
            # itself when the LiveKit room ends; _on_drop also clears
            # this flag, so the dual-clearing is safe.
            self._relay_state.transition(
                account_index, False, "after-commit", cid=cid,
            )
        except Exception:  # noqa: BLE001
            self._control.release_mesh_assignment(peer_id, error="provisioning failed")
            on_drop(peer_id)
            try:
                self._mesh.drop_client(peer_id)
            except Exception:
                pass
            self._allocator.leave(peer_id)
            log.exception("relay: cid=%s failed to bring up client tunnel", cid)
            self._bump("provision_failed")

    def _make_on_drop(self, account_index: int, cid: str) -> Callable[[int], None]:
        def _on_drop(dropped_peer_id: int) -> None:
            # Slot freed — clear the per-account active flag so the next
            # incoming Bale call can start a fresh join. cid threads
            # through so the journal links the drop back to the call
            # that originally created the session.
            self._relay_state.transition(
                account_index, False, "on-drop", cid=cid,
            )
            with self._session_map_lock:
                self._session_map.pop(f"sess:{dropped_peer_id}", None)
        return _on_drop
