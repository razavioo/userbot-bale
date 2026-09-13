"""
Failover routing and automatic transport recovery for the VPN stack.

The original router only picked one transport at startup. This module
now owns transport selection, health observation, and automatic
hot-swap/recovery while keeping the tunnel session alive.
"""

from __future__ import annotations

import logging
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from userbot_bale.control.observability import StructuredEventRecorder, classify_failure

log = logging.getLogger(__name__)


TransportFactory = Callable[[], object]
CarrierSessionFactory = Callable[[], tuple[object, "TransportPool"]]
StatusCallback = Callable[[dict[str, str]], None]
CONTROL_TOPIC = "bb-failover-control"


@dataclass
class RouterChoice:
    name: str
    factory: TransportFactory


@dataclass
class _ChoiceState:
    choice: RouterChoice
    failures: int = 0
    cooling_until: float = 0.0
    last_success_at: float = 0.0
    last_error: str = ""


class FailoverRouter:
    """Pick the first transport that builds."""

    def __init__(self, choices: list[RouterChoice], *, recorder: StructuredEventRecorder | None = None) -> None:
        if not choices:
            raise ValueError("at least one RouterChoice required")
        self._choice_defs = choices
        self._recorder = recorder

    def build(self):  # type: ignore[no-untyped-def]
        pool = TransportPool(self._choice_defs, recorder=self._recorder)
        return pool.start()


class TransportPool:
    """Restartable pool of transport factories with cooldown/backoff."""

    def __init__(
        self,
        choices: list[RouterChoice],
        *,
        recorder: StructuredEventRecorder | None = None,
        cooldown_seconds: float = 2.0,
    ) -> None:
        if not choices:
            raise ValueError("at least one RouterChoice required")
        self._choice_defs = choices
        self._states = [_ChoiceState(choice=item) for item in choices]
        self._recorder = recorder
        self._cooldown_seconds = cooldown_seconds
        self._current: object | None = None
        self._current_name: str | None = None
        self._current_idx: int = -1

    @property
    def _choices(self) -> list[RouterChoice]:
        return self._choice_defs

    def current(self):  # type: ignore[no-untyped-def]
        if self._current is None or self._current_name is None:
            raise RuntimeError("TransportPool not started")
        return self._current_name, self._current

    def start(self):  # type: ignore[no-untyped-def]
        return self.select()

    def select(self, *, preferred_first: bool = True):  # type: ignore[no-untyped-def]
        old = self._current
        old_name = self._current_name
        errors: list[tuple[str, Exception]] = []
        order = list(range(len(self._states)))
        if self._current_idx >= 0 and preferred_first:
            order = order[self._current_idx + 1 :] + order[: self._current_idx + 1]
        now = time.monotonic()
        for idx in order:
            state = self._states[idx]
            if state.cooling_until > now:
                continue
            try:
                candidate = state.choice
                if self._recorder is not None:
                    self._recorder.event(
                        "transport_candidate_attempt",
                        stage="transport_runtime" if self._current is not None else "transport_init",
                        outcome="begin",
                        transport_requested=candidate.name,
                    )
                transport = candidate.factory()
                self._current = transport
                self._current_name = candidate.name
                self._current_idx = idx
                state.last_success_at = time.time()
                state.failures = 0
                state.cooling_until = 0.0
                state.last_error = ""
                if self._recorder is not None:
                    self._recorder.event(
                        "transport_selected",
                        stage="transport_runtime" if old is not None else "transport_init",
                        outcome="success",
                        transport_selected=candidate.name,
                    )
                if old is not None and old is not transport:
                    try:
                        old.close()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        log.exception("closing old transport %s failed", old_name)
                return candidate.name, transport
            except Exception as exc:  # noqa: BLE001
                state.failures += 1
                state.last_error = str(exc)
                state.cooling_until = time.monotonic() + self._cooldown_seconds * max(1, state.failures)
                info = classify_failure(last_error=f"transport factory failed: {exc}")
                if self._recorder is not None:
                    self._recorder.event(
                        "transport_setup_failed",
                        stage="transport_runtime" if self._current is not None else "transport_init",
                        outcome="failure",
                        transport_requested=state.choice.name,
                        failure_class=info.failure_class,
                        failure_code=info.failure_code,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                errors.append((state.choice.name, exc))
                log.warning("transport %s failed: %s", state.choice.name, exc)
        detail = "; ".join(f"{name}: {exc}" for name, exc in errors)
        if old is not None and old_name is not None:
            raise RuntimeError(f"no replacement transport available — {detail or 'all candidates cooling down'}")
        raise RuntimeError(f"no transport available — {detail or 'all candidates cooling down'}")

    def advance(self):  # type: ignore[no-untyped-def]
        return self.select(preferred_first=True)

    def build_named(self, name: str):  # type: ignore[no-untyped-def]
        errors: list[tuple[str, Exception]] = []
        for idx, state in enumerate(self._states):
            if state.choice.name != name:
                continue
            try:
                transport = state.choice.factory()
                state.last_success_at = time.time()
                state.failures = 0
                state.cooling_until = 0.0
                state.last_error = ""
                return idx, transport
            except Exception as exc:  # noqa: BLE001
                state.failures += 1
                state.last_error = str(exc)
                state.cooling_until = time.monotonic() + self._cooldown_seconds * max(1, state.failures)
                errors.append((state.choice.name, exc))
                break
        detail = "; ".join(f"{candidate}: {exc}" for candidate, exc in errors)
        raise RuntimeError(f"failed to build transport {name!r} — {detail or 'not configured'}")

    def activate_prebuilt(self, idx: int, transport: object):  # type: ignore[no-untyped-def]
        old = self._current
        old_name = self._current_name
        self._current = transport
        self._current_idx = idx
        self._current_name = self._states[idx].choice.name
        if old is not None and old is not transport:
            try:
                old.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                log.exception("closing old transport %s failed", old_name)
        return self._current_name, old

    def close(self) -> None:
        if self._current is not None:
            try:
                self._current.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self._current = None
        self._current_name = None
        self._current_idx = -1

class TransportChain(TransportPool):
    """Backwards-compatible alias for the older router API."""


class HealthMonitor:
    """Observe tunnel health and emit callbacks for failover decisions."""

    def __init__(
        self,
        tunnel,
        *,
        interval: float = 5.0,
        stuck_threshold: int = 8,
        on_signal: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:  # type: ignore[no-untyped-def]
        self._tunnel = tunnel
        self._interval = interval
        self._threshold = stuck_threshold
        self._on_signal = on_signal
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="vpn-health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _emit(self, signal: str, **payload: object) -> None:
        if self._on_signal is not None:
            self._on_signal(signal, payload)

    def _run(self) -> None:
        last_pending = 0
        while not self._stop.wait(self._interval):
            pending = getattr(self._tunnel, "pending_count", lambda: 0)()
            if pending >= self._threshold and pending == last_pending:
                log.warning(
                    "vpn health: %d frames unacked for >%.1fs — transport may be stuck",
                    pending,
                    self._interval,
                )
                self._emit("transport_stuck", pending=pending, interval=self._interval)
            last_pending = pending


@dataclass
class FailoverStatus:
    state: str = "healthy"
    transport_selected: str = ""
    transport_previous: str = ""
    failover_count: int = 0
    recovering_since: str = ""
    last_error: str = ""
    last_failover_reason: str = ""
    call_established: str = "yes"
    data_flow_ok: str = "yes"
    carrier_session_id: str = ""
    peer_coordination: str = "disabled"

    def to_dict(self) -> dict[str, str]:
        return {
            "recovery_state": self.state,
            "transport_selected": self.transport_selected,
            "transport_previous": self.transport_previous,
            "failover_count": str(self.failover_count),
            "recovering_since": self.recovering_since,
            "last_error": self.last_error,
            "last_failover_reason": self.last_failover_reason,
            "call_established": self.call_established,
            "data_flow_ok": self.data_flow_ok,
            "carrier_session_id": self.carrier_session_id,
            "peer_coordination": self.peer_coordination,
        }


class _CoordinationChannel:
    def __init__(self, session, on_message: Callable[[dict[str, str]], None]) -> None:  # type: ignore[no-untyped-def]
        self._session = session
        self._on_message = on_message
        self._transport = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not hasattr(self._session, "data_channel"):
            return False
        try:
            self._transport = self._session.data_channel(topic=CONTROL_TOPIC, reliable=True)
        except Exception:  # noqa: BLE001
            log.exception("failed to start coordination channel")
            return False
        self._thread = threading.Thread(target=self._run, name="vpn-failover-ctl", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                pass
            self._transport = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def send(self, payload: dict[str, str]) -> None:
        if self._transport is None:
            return
        try:
            self._transport.send_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            # Peer disconnect races with failover coordination; avoid traceback spam.
            log.debug("coordination send skipped: %s", exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._transport is None:
                return
            raw = self._transport.recv_bytes(timeout=0.25)
            if raw is None:
                continue
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(decoded, dict):
                continue
            self._on_message({str(key): str(value) for key, value in decoded.items()})


class FailoverController:
    """Own transport selection, hot-swap, and carrier-session recovery."""

    def __init__(
        self,
        runner,
        pool: TransportPool,
        *,
        carrier_session: object | None = None,
        carrier_factory: CarrierSessionFactory | None = None,
        recorder: StructuredEventRecorder | None = None,
        status_callback: StatusCallback | None = None,
        health_interval: float = 5.0,
        stuck_threshold: int = 8,
    ) -> None:  # type: ignore[no-untyped-def]
        self._runner = runner
        self._pool = pool
        self._carrier_session = carrier_session
        self._carrier_factory = carrier_factory
        self._recorder = recorder or StructuredEventRecorder(component="vpn-failover")
        self._status_callback = status_callback
        self._health_interval = health_interval
        self._stuck_threshold = stuck_threshold
        self._status = FailoverStatus()
        self._health: HealthMonitor | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._carrier_watcher: threading.Thread | None = None
        self._coordination: _CoordinationChannel | None = None
        self._prepared_idx: int | None = None
        self._prepared_transport: object | None = None
        self._prepared_name: str = ""
        self._commit_deadline: float = 0.0

    @property
    def status(self) -> dict[str, str]:
        return self._status.to_dict()

    def start(self, transport_name: str) -> None:
        self._status.transport_selected = transport_name
        self._status.call_established = "yes"
        self._status.data_flow_ok = "yes"
        self._status.carrier_session_id = hex(id(self._carrier_session)) if self._carrier_session is not None else ""
        self._status.peer_coordination = "disabled"
        self._publish_status()
        if self._carrier_session is not None:
            self._coordination = _CoordinationChannel(self._carrier_session, self._on_control_message)
            if self._coordination.start():
                self._status.peer_coordination = "active"
                self._publish_status()
        tunnel = self._runner.tunnel
        if tunnel is not None:
            self._health = HealthMonitor(
                tunnel,
                interval=self._health_interval,
                stuck_threshold=self._stuck_threshold,
                on_signal=self._on_health_signal,
            )
            self._health.start()
        self._runner.add_event_handler(self._on_runner_event)
        if self._carrier_session is not None:
            observer = getattr(self._carrier_session, "add_terminal_observer", None)
            if callable(observer):
                observer(self._on_carrier_terminal)
            self._carrier_watcher = threading.Thread(target=self._watch_carrier, daemon=True)
            self._carrier_watcher.start()

    def stop(self) -> None:
        self._closed = True
        if self._coordination is not None:
            self._coordination.stop()
            self._coordination = None
        if self._health is not None:
            self._health.stop()
            self._health = None

    def trigger_failover(self, reason: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            previous = self._status.transport_selected
            self._status.state = "switching"
            self._status.recovering_since = str(time.time())
            self._status.last_failover_reason = reason
            self._publish_status()
            try:
                target_name = self._pick_next_name(previous)
                if target_name:
                    self._announce_intent(target_name, reason)
                new_name, new_transport = self._pool.advance()
                old = self._runner.swap_transport(new_transport)
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
                self._status.transport_previous = previous
                self._status.transport_selected = new_name
                self._status.failover_count += 1
                self._status.state = "healthy"
                self._status.call_established = "yes"
                self._status.data_flow_ok = "yes"
                self._status.last_error = ""
                self._publish_status()
                self._send_control(
                    {
                        "kind": "commit",
                        "transport": new_name,
                        "reason": reason,
                        "ts": str(time.time()),
                    }
                )
                self._recorder.event(
                    "transport_failover_succeeded",
                    stage="transport_runtime",
                    outcome="success",
                    transport_selected=new_name,
                    transport_previous=previous,
                    reason=reason,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                self._status.state = "recovering" if self._carrier_factory is not None else "failed"
                self._status.last_error = str(exc)
                self._status.data_flow_ok = "no"
                self._publish_status()
                self._recorder.event(
                    "transport_failover_failed",
                    stage="transport_runtime",
                    outcome="failure",
                    transport_selected=previous,
                    reason=reason,
                    error_message=str(exc),
                )
        if self._carrier_factory is not None:
            return self._recover_carrier(reason)
        return False

    def _recover_carrier(self, reason: str) -> bool:
        with self._lock:
            self._status.state = "recovering"
            self._status.call_established = "no"
            self._status.data_flow_ok = "no"
            self._status.last_failover_reason = reason
            self._status.recovering_since = str(time.time())
            self._publish_status()
        try:
            carrier_session, pool = self._carrier_factory()
            name, transport = pool.start()
            old = self._runner.swap_transport(transport)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
            if self._carrier_session is not None:
                try:
                    self._carrier_session.stop()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            self._pool.close()
            self._pool = pool
            self._carrier_session = carrier_session
            if self._coordination is not None:
                self._coordination.stop()
            self._coordination = _CoordinationChannel(carrier_session, self._on_control_message)
            if self._coordination.start():
                self._status.peer_coordination = "active"
            else:
                self._status.peer_coordination = "disabled"
            observer = getattr(carrier_session, "add_terminal_observer", None)
            if callable(observer):
                observer(self._on_carrier_terminal)
            with self._lock:
                self._status.transport_previous = self._status.transport_selected
                self._status.transport_selected = name
                self._status.carrier_session_id = hex(id(carrier_session))
                self._status.failover_count += 1
                self._status.state = "healthy"
                self._status.call_established = "yes"
                self._status.data_flow_ok = "yes"
                self._status.last_error = ""
                self._publish_status()
                self._send_control(
                    {
                        "kind": "commit",
                        "transport": name,
                        "reason": reason,
                        "ts": str(time.time()),
                    }
                )
            self._recorder.event(
                "carrier_recovery_succeeded",
                stage="transport_runtime",
                outcome="success",
                transport_selected=name,
                reason=reason,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._status.state = "failed"
                self._status.last_error = str(exc)
                self._publish_status()
            self._recorder.event(
                "carrier_recovery_failed",
                stage="transport_runtime",
                outcome="failure",
                reason=reason,
                error_message=str(exc),
            )
            return False

    def _on_health_signal(self, signal: str, payload: dict[str, object]) -> None:
        reason = signal
        if signal == "transport_stuck":
            reason = f"transport_stuck:{payload.get('pending', 0)}"
        self.trigger_failover(reason)

    def _on_runner_event(self, event: str, payload: dict[str, object]) -> None:
        if event in {"transport_send_failed", "transport_retry_failed", "runner_send_failed"}:
            self.trigger_failover(event)
        elif event == "frame_dropped":
            self.trigger_failover(f"frame_dropped:{payload.get('seq', '')}")

    def _on_carrier_terminal(self, exc: BaseException | None = None) -> None:
        reason = "carrier_terminal"
        if exc is not None:
            reason = f"carrier_terminal:{type(exc).__name__}"
        self.trigger_failover(reason)

    def _on_control_message(self, message: dict[str, str]) -> None:
        kind = message.get("kind", "")
        target = message.get("transport", "")
        if kind == "intent" and target:
            self._prepare_named_transport(target)
            self._send_control(
                {
                    "kind": "ready",
                    "transport": target,
                    "ts": str(time.time()),
                }
            )
            return
        if kind == "commit" and target:
            self._commit_prepared_transport(target)
            return
        if kind == "ready" and target:
            self._commit_deadline = max(self._commit_deadline, time.monotonic() + 0.5)
            return

    def _watch_carrier(self) -> None:
        while not self._closed:
            session = self._carrier_session
            if session is None:
                return
            is_terminal = getattr(session, "is_terminal", None)
            if callable(is_terminal) and is_terminal():
                self._on_carrier_terminal(getattr(session, "terminal_error", None))
                return
            time.sleep(0.5)

    def _publish_status(self) -> None:
        if self._status_callback is not None:
            self._status_callback(self._status.to_dict())

    def _pick_next_name(self, current: str) -> str:
        choices = [choice.name for choice in self._pool._choices]
        if not choices:
            return ""
        if current not in choices:
            return choices[0]
        idx = choices.index(current)
        return choices[(idx + 1) % len(choices)] if len(choices) > 1 else ""

    def _announce_intent(self, transport_name: str, reason: str) -> None:
        self._send_control(
            {
                "kind": "intent",
                "transport": transport_name,
                "reason": reason,
                "ts": str(time.time()),
            }
        )

    def _send_control(self, payload: dict[str, str]) -> None:
        if self._coordination is None:
            return
        try:
            self._coordination.send(payload)
        except Exception:  # noqa: BLE001
            log.exception("coordination send failed")

    def _prepare_named_transport(self, name: str) -> None:
        if self._prepared_name == name and self._prepared_transport is not None:
            return
        idx, transport = self._pool.build_named(name)
        if self._prepared_transport is not None:
            try:
                self._prepared_transport.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self._prepared_idx = idx
        self._prepared_transport = transport
        self._prepared_name = name

    def _commit_prepared_transport(self, name: str) -> None:
        with self._lock:
            if self._prepared_transport is None or self._prepared_name != name or self._prepared_idx is None:
                return
            old = self._runner.swap_transport(self._prepared_transport)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
            committed_name, _old = self._pool.activate_prebuilt(self._prepared_idx, self._prepared_transport)
            self._status.transport_previous = self._status.transport_selected
            self._status.transport_selected = committed_name
            self._status.failover_count += 1
            self._status.state = "healthy"
            self._status.last_error = ""
            self._status.call_established = "yes"
            self._status.data_flow_ok = "yes"
            self._publish_status()
            self._prepared_transport = None
            self._prepared_name = ""
            self._prepared_idx = None
