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
from typing import Any, Callable, Protocol

from .constants import DEFAULT_TUNNEL_SESS_ID
from .relay_state import RelayState


log = logging.getLogger(__name__)


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

    One instance is shared across all account-indexed listen callbacks;
    the original closure was per-account in scope but stateful across
    accounts via the `_account_active` / `expected_clients` / `sessions`
    dicts. Behavior is preserved by keeping the same shared mutable
    structures here.
    """

    def __init__(
        self,
        *,
        use_coordinator: bool,
        identity_prefix: str,
        pool_cidr: str,
        tun_mtu: int,
        provision_timeout: float,
        psk_key: bytes | None,
        mesh: Any,
        allocator: Any,
        control: Any,
        expected_clients: Any,
        session_map: dict[str, str],
        session_map_lock: threading.Lock,
        probe_locks: dict[int, Any],
        relay_state: RelayState,
        stale_active_flag_secs: float,
        sessions: list[tuple[int, Any, Any]],
        reporters: list,
        livekit_session_factory: _LiveKitSessionFactory,
        keepalive_factory: _KeepaliveFactory,
        datachannel_transport_factory: _DataChannelTransportFactory,
        encrypted_transport_factory: _EncryptedTransportFactory,
        provision_message_factory: _ProvisionMessageFactory,
        recv_provision: _RecvProvision,
        provisioning_error_cls: type[Exception],
        log_stream=sys.stderr,
    ) -> None:
        self._use_coordinator = use_coordinator
        self._identity_prefix = identity_prefix
        self._pool_cidr = pool_cidr
        self._tun_mtu = tun_mtu
        self._provision_timeout = provision_timeout
        self._psk_key = psk_key
        self._mesh = mesh
        self._allocator = allocator
        self._control = control
        self._expected_clients = expected_clients
        self._session_map = session_map
        self._session_map_lock = session_map_lock
        self._probe_locks = probe_locks
        self._relay_state = relay_state
        self._stale_active_flag_secs = stale_active_flag_secs
        self._sessions = sessions
        self._reporters = reporters
        self._LiveKitSession = livekit_session_factory
        self._Keepalive = keepalive_factory
        self._DataChannelTransport = datachannel_transport_factory
        self._EncryptedTransport = encrypted_transport_factory
        self._ProvisionMessage = provision_message_factory
        self._recv_provision = recv_provision
        self._ProvisioningError = provisioning_error_cls
        self._log = log_stream

    # ------------------------------------------------------------------
    # Public entry point — the listen callback adapter binds the
    # account_index for each registered Bale client.
    # ------------------------------------------------------------------

    def handle_call(self, event: Any, account_index: int = 0) -> None:
        peer_id = event.peer_id
        if peer_id is None:
            print(
                "[vpn-mesh] incoming call rejected: missing canonical peer_id",
                file=self._log,
            )
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
            self._do_handle(event, account_index, peer_id, state)
        finally:
            if state[0] and not state[1]:
                self._relay_state.transition(account_index, False, "outer-finally")

    # ------------------------------------------------------------------
    # Internal flow — split out for readability but preserves the exact
    # control flow (and stderr lines) of the original closure.
    # ------------------------------------------------------------------

    def _do_handle(self, event: Any, account_index: int, peer_id: int, state: list) -> None:
        # _state[0] := we are the caller that set _account_active=True
        # _state[1] := we reached sessions.append (commit)
        # ── Coordinator mode: route by probing the control channel ────────
        # parse_call_peer_id returns the CALLEE's peer_id (our own account),
        # not the CALLER's. We cannot use peer_id to distinguish coordinator
        # instruction calls (EXPECT_CLIENT) from VPN client calls. Instead we
        # join the room and check if EXPECT_CLIENT arrives on "control" topic.
        session_pre_joined: Any = None
        if self._use_coordinator:
            session_pre_joined = self._coordinator_probe(event, account_index, peer_id, state)
            if session_pre_joined is None:
                return  # coordinator branch handled the call (or rejected it)
            # Caller-identity override + EXPECT_CLIENT verification both
            # mutate `peer_id`; keep the result here for the rest of the
            # flow.
            peer_id = self._resolve_and_verify_caller(
                event, account_index, peer_id, session_pre_joined
            )
            if peer_id is None:
                return

        # ───────────────────────────────────────────────────────────────────

        print(
            f"[vpn-mesh] incoming call → peer_id={peer_id} account={account_index}",
            file=self._log,
        )
        slot = self._allocator.join(peer_id)
        if slot is None:
            print(
                f"[vpn-mesh] incoming call rejected: no server JWT capacity "
                f"peer_id={peer_id} active={len(self._allocator.assignment())} "
                f"capacity={self._allocator.total_capacity}",
                file=self._log,
            )
            if session_pre_joined is not None:
                try:
                    session_pre_joined.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._relay_state.transition(account_index, False, "no-jwt-capacity")
            return
        print(
            f"[vpn-mesh] server_jwt_assignment={self._allocator.assignment()} "
            f"slots={[s.peer_ids for s in self._allocator.slots()]}",
            file=self._log,
        )

        self._provision_tunnel(event, account_index, peer_id, session_pre_joined, state)

    # ---- coordinator-path probe -------------------------------------

    def _coordinator_probe(
        self, event: Any, account_index: int, peer_id: int, state: list
    ) -> Any:
        """Run the EXPECT_CLIENT probe. Returns the joined LiveKitSession
        if the call should be promoted to a VPN session, or None if the
        probe fully handled the call (EXPECT_CLIENT registration,
        skip-probe, or probe error)."""
        # If this relay account already hosts an active VPN session, skip
        # the probe entirely. Starting a concurrent LiveKit session while
        # a VPN session is running overloads the shared livekit-ffi Rust
        # runtime, delaying on_data_received callbacks and causing
        # WireGuard keepalive drops → tunnel_dead within 30 s.
        # The coordinator will get no EXPECT_ACK and roll back, then
        # retry via the other relay.
        from baleobala.coordinator.protocol import (
            CONTROL_TOPIC as _CTRL_TOPIC,
            ControlMessage as _CM,
            Kind as _Kind,
            decode as _ctrl_decode,
            encode as _ctrl_encode,
            ControlError as _CtrlError,
        )

        with self._probe_locks[account_index]:
            # Re-check inside the lock: a duplicate Bale push (same call
            # redelivered, or coordinator's EXPECT_CLIENT arriving while
            # we still hold the lock for the client probe) must NOT
            # start a second probe — it would overwrite the live VPN
            # tunnel and silently drop every Android frame.
            slot = self._allocator.slots()[account_index]
            if slot.peer_ids or self._relay_state.is_active(account_index):
                self._maybe_evict_stale_sessions(account_index)
                slot = self._allocator.slots()[account_index]
                # Stale-flag auto-recovery
                if not slot.peer_ids and self._relay_state.is_stale(
                    account_index, threshold=self._stale_active_flag_secs
                ):
                    age = self._relay_state.age(account_index)
                    print(
                        f"[vpn-mesh] _active[{account_index}] looks stale "
                        f"({age:.1f}s old, slot empty) — clearing & continuing",
                        file=self._log,
                    )
                    self._relay_state.transition(
                        account_index, False, "stale-recovery"
                    )
                if slot.peer_ids or self._relay_state.is_active(account_index):
                    age_str = (
                        f" age={self._relay_state.age(account_index):.1f}s"
                        if self._relay_state.is_active(account_index)
                        else ""
                    )
                    print(
                        f"[vpn-mesh] skipping probe for account={account_index}: "
                        f"VPN session already active "
                        f"(slot={slot.peer_ids} active={self._relay_state.is_active(account_index)}{age_str})",
                        file=self._log,
                    )
                    return None

            self._relay_state.transition(account_index, True, "probe-start")
            state[0] = True  # we own the flag now
            probe = self._LiveKitSession(
                url=event.credentials.url,
                token=event.credentials.token,
                identity=f"{self._identity_prefix}-{account_index}-{peer_id}",
                publish_audio=False,
            )
            try:
                probe.start()
                try:
                    probe.wait_for_remote_participant(timeout=5.0)
                except (TimeoutError, RuntimeError):
                    pass
                ctrl = probe.data_channel(topic=_CTRL_TOPIC, reliable=True)
                # Wait long enough for the coordinator's EXPECT_CLIENT
                # message to arrive after both sides are in the room.
                # 1.5 s was previously too tight when the coordinator's
                # quick_exchange ran behind audio-track setup; bumping
                # to 5 s keeps real VPN-call latency acceptable while
                # giving EXPECT_CLIENT a reliable window.
                payload = ctrl.recv_bytes(timeout=5.0)
                if payload is not None:
                    try:
                        msg = _ctrl_decode(payload)
                        if msg.kind == _Kind.EXPECT_CLIENT:
                            cpid = int(msg.get("client_peer_id", 0))
                            csid = str(msg.get("session_id", ""))
                            cexp = int(msg.get("expires_in_secs", 30))
                            if cpid and csid:
                                self._expected_clients.register(
                                    client_peer_id=cpid,
                                    session_id=csid,
                                    expires_in_secs=cexp,
                                )
                                ctrl.send_bytes(
                                    _ctrl_encode(_CM(kind=_Kind.EXPECT_ACK, body={}))
                                )
                                print(
                                    f"[vpn-mesh] EXPECT_CLIENT: registered "
                                    f"client={cpid} session={csid}",
                                    file=self._log,
                                )
                            # Brief flush before tearing down the room so
                            # the queued EXPECT_ACK actually lands on the
                            # SFU before room.disconnect() races it.
                            _time.sleep(0.5)
                            try:
                                probe.stop()
                            except Exception:  # noqa: BLE001
                                pass
                            self._relay_state.transition(
                                account_index, False, "expect-client-done"
                            )
                            return None  # coordinator instruction handled
                    except _CtrlError:
                        pass
            except Exception:  # noqa: BLE001
                log.exception("relay: probe session error")
                try:
                    probe.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._relay_state.transition(account_index, False, "probe-error")
                return None
        # Falling through means "EXPECT_CLIENT probe didn't trigger early
        # return; the caller should be a real VPN client". Promote the
        # already-joined probe session.
        return probe

    def _maybe_evict_stale_sessions(self, account_index: int) -> None:
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
                f"[vpn-mesh] reconnect detected for account={account_index}; "
                f"evicting {len(evicted)} stale session(s) and continuing",
                file=self._log,
            )
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

    def _resolve_and_verify_caller(
        self, event: Any, account_index: int, peer_id: int, probe: Any
    ) -> int | None:
        """Promote the probe to a VPN session and resolve the actual
        caller user_id from the LiveKit participant identity (commit
        36a8579). Returns the resolved peer_id, or None if the call was
        rejected (audio publish failed, or no EXPECT_CLIENT pre-approval
        within the 5 s polling window)."""
        # VPN client call — reuse the already-joined probe session.
        # Publish audio now so the Bale SFU keeps this long-lived
        # session alive (without a media track the SFU closes it
        # after ~20 s).
        try:
            probe.enable_audio()
        except Exception:  # noqa: BLE001
            log.exception("relay: enable_audio failed; cleaning up")
            try: probe.stop()
            except Exception: pass  # noqa: BLE001
            self._relay_state.transition(account_index, False, "enable-audio-failed")
            return None

        # Override peer_id with the ACTUAL caller user_id from the
        # LiveKit participant identity. event.peer_id is the relay's
        # own peer_id under Bale push semantics, so all clients on
        # the same relay account would collide on mesh.issue_client.
        try:
            identities = probe.remote_participant_identities()
        except Exception:  # noqa: BLE001
            identities = []
        for ident in identities:
            try:
                caller_id = int(ident)
            except (TypeError, ValueError):
                continue
            if caller_id != peer_id and caller_id > 0:
                print(
                    f"[vpn-mesh] caller user_id resolved from LiveKit "
                    f"identity: peer_id={peer_id} → {caller_id}",
                    file=self._log,
                )
                peer_id = caller_id
                break

        # Verify the caller was pre-approved by the coordinator.
        # Race: the Android client typically dials the relay within
        # 100-300 ms of receiving ASSIGN, but the coordinator's
        # quick_exchange (which registers the EXPECT_CLIENT) can take
        # 2-5 s if the relay has to publish its audio track first.
        # Poll briefly so the legitimate client doesn't get rejected
        # just because EXPECT_CLIENT was racing the inbound dial.
        expected_session = self._expected_clients.consume(peer_id)
        deadline = _time.monotonic() + 5.0
        while expected_session is None and _time.monotonic() < deadline:
            _time.sleep(0.25)
            expected_session = self._expected_clients.consume(peer_id)
        if expected_session is None:
            print(
                f"[vpn-mesh] rejecting unknown caller={peer_id} on "
                f"account={account_index} (no EXPECT_CLIENT pre-approval; "
                f"likely the coordinator's dispatch call landed here)",
                file=self._log,
            )
            try: probe.stop()
            except Exception: pass  # noqa: BLE001
            self._relay_state.transition(account_index, False, "no-expect-client")
            return None
        with self._session_map_lock:
            self._session_map[f"sess:{peer_id}"] = expected_session
        return peer_id

    # ---- post-coordinator: actually bring up the tunnel -------------

    def _provision_tunnel(
        self,
        event: Any,
        account_index: int,
        peer_id: int,
        session_pre_joined: Any,
        state: list,
    ) -> None:
        on_drop = self._make_on_drop(account_index)
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
            self._Keepalive(session, interval=20.0).start()
            dc = self._DataChannelTransport(session, topic="vpn", reliable=False)
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
                f"[vpn-mesh] peer={peer_id} status=assigned "
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
                f"[vpn-mesh] peer={peer_id} status=acknowledged",
                file=self._log,
            )
            assignment = self._mesh.activate_client(
                peer_id, mtu_override=transport.mtu,
            )
            self._control.activate_mesh_assignment(
                peer_id, transport="dc", session_id=sess_id,
            )
            print(
                f"[vpn-mesh] peer={peer_id} status=active → assigned "
                f"{assignment.client} (gateway={assignment.gateway})",
                file=self._log,
            )
            self._sessions.append((peer_id, session, transport))
            state[1] = True  # outer finally checks this
            # Probe + provisioning are done — release the per-account
            # probe-in-progress flag so a *different* peer (e.g. a
            # second device on the same Bale account) can also probe
            # this account. The reaper handles cleanup of the session
            # itself when the LiveKit room ends; _on_drop also clears
            # this flag, so the dual-clearing is safe.
            self._relay_state.transition(account_index, False, "after-commit")
        except Exception:  # noqa: BLE001
            self._control.release_mesh_assignment(peer_id, error="provisioning failed")
            on_drop(peer_id)
            try:
                self._mesh.drop_client(peer_id)
            except Exception:
                pass
            self._allocator.leave(peer_id)
            log.exception("failed to bring up client tunnel")

    def _make_on_drop(self, account_index: int) -> Callable[[int], None]:
        def _on_drop(dropped_peer_id: int) -> None:
            # Slot freed — clear the per-account active flag so the next
            # incoming Bale call can start a fresh probe.
            self._relay_state.transition(account_index, False, "on-drop")
            if not self._use_coordinator or not self._reporters:
                return
            with self._session_map_lock:
                sid = self._session_map.pop(f"sess:{dropped_peer_id}", None)
            if sid is not None:
                self._reporters[account_index].report_released(str(sid))
        return _on_drop
